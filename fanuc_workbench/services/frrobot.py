"""Narrow FANUC Robot Object adapter. No UI, KCL, run, motion or I/O writes."""
from __future__ import annotations

import time

from fanuc_workbench.services.native_values import IO, fingerprint, register_matches, register_target

IO_CODES = {"DI": 1, "DO": 2, "AI": 3, "AO": 4, "RI": 8, "RO": 9,
            "GI": 18, "GO": 19, "UI": 20, "UO": 21, "F": 35}


class VendorCallError(RuntimeError):
    """Numeric vendor diagnostics without vendor text or controller data."""
    def __init__(self, operation, member, cause):
        details = {"operation": operation, "member": member}
        code = getattr(cause, "hresult", None)
        if type(code) is int:
            details["hresult"] = code
        info = getattr(cause, "excepinfo", None)
        if isinstance(info, tuple) and len(info) == 6 and type(info[5]) is int:
            details["vendor_code"] = info[5]
        self.details = details
        super().__init__("Vendor API call failed; inspect controller state before retrying")


class ComBinding:
    def __init__(self):
        import pythoncom
        from win32com.client.dynamic import Dispatch
        self.com, self.dispatch = pythoncom, Dispatch

    def get(self, obj, name, *args):
        value = self._invoke(obj, name, "get", self.com.DISPATCH_PROPERTYGET, True, *args)
        return self.dispatch(value) if hasattr(value, "GetTypeInfo") else value

    def put(self, obj, name, value, *indexes):
        self._invoke(obj, name, "put", self.com.DISPATCH_PROPERTYPUT, False, *indexes, value)

    def call(self, obj, name, *args):
        return self._invoke(obj, name, "call", self.com.DISPATCH_METHOD, True, *args)

    def _invoke(self, obj, name, operation, flags, result, *args):
        try:
            return obj._oleobj_.Invoke(obj._oleobj_.GetIDsOfNames(name), 0, flags, result, *args)
        except Exception as exc:
            raise VendorCallError(operation, name, exc) from None

    def items(self, obj, maximum):
        count = int(self.get(obj, "Count"))
        if not 0 <= count <= maximum:
            raise ValueError("Vendor collection exceeds its bounded size")
        values = []
        for item in obj:
            if len(values) >= count:
                raise ValueError("Vendor collection changed during enumeration; read again")
            values.append(item)
        if len(values) != count:
            raise ValueError("Vendor collection changed during enumeration; read again")
        return values


class FrRobot:
    def __init__(self, host, *, binding=None, robot=None):
        self.binding = binding or ComBinding()
        self.robot = robot or self.binding.dispatch("FRRobot.FRCRobot")
        if robot is None:
            # Zero retries means no connection attempt in the installed API.
            self.binding.call(self.robot, "ConnectEx", host, False, 2, 1000)
        if not self.binding.get(self.robot, "IsConnected"):
            raise RuntimeError("FANUC Robot Object is not connected")
        if self.binding.get(self.robot, "IsOffline"):
            raise ValueError("Native TCP access requires a connected controller, not an offline file object")
        actual = self.binding.get(self.robot, "HostName")
        if not isinstance(actual, str) or actual.casefold() != host.casefold():
            raise ValueError("Vendor connection does not match the configured endpoint")

    def _get(self, obj, name, *args):
        return self.binding.get(obj, name, *args)

    def _register(self, target):
        kind, group, index = register_target(target)
        if kind == "R":
            variable = self._get(self._get(self.robot, "RegNumerics"), "Item", index)
            self.binding.call(variable, "Refresh")
            return self._get(variable, "Value")
        position = self._get(self._get(self._get(self.robot, "RegPositions"), "Item", index), "Group", group)
        self.binding.call(position, "Refresh")
        return position

    def position(self, position):
        initialized = bool(self._get(position, "IsInitialized"))
        if not initialized:
            return {"initialized": False}
        kind = int(self._get(position, "Type"))
        data = {"initialized": True, "native_type": kind,
                "user_frame": int(self._get(position, "UserFrame")), "user_tool": int(self._get(position, "UserTool"))}
        fmt = self._get(position, "Formats", kind)
        if kind in {2, 6}:
            data.update(representation="cartesian", values={axis: float(self._get(fmt, axis)) for axis in ("X", "Y", "Z", "W", "P", "R")})
            config = self._get(fmt, "Config")
            data["configuration"] = str(self._get(config, "Text"))
            ext = self._get(fmt, "Ext")
            count = int(self._get(ext, "Count"))
            if not 0 <= count <= 9:
                raise ValueError("Unsupported external-axis count")
            data["external_axes"] = [float(self._get(ext, "Item", i)) for i in range(1, count + 1)]
        elif kind == 9:
            count = int(self._get(fmt, "Count"))
            if not 1 <= count <= 9:
                raise ValueError("Unsupported joint count")
            data.update(representation="joint", values={f"J{i}": float(self._get(fmt, "Item", i)) for i in range(1, count + 1)})
        else:
            raise ValueError("Unsupported native position representation")
        return data

    def read_register(self, target):
        kind, group, index = register_target(target)
        obj = self._register(target)
        result = {"kind": kind, "group": group, "index": index}
        if kind == "R":
            code = int(self._get(obj, "Type"))
            if code not in {16, 17}:
                raise ValueError("Unsupported numeric register type")
            return {**result, "type": "integer" if code == 16 else "real",
                    "value": self._get(obj, "RegLong" if code == 16 else "RegFloat")}
        return {**result, **self.position(obj)}

    def sample(self, expressions):
        values, errors = {}, {}
        for expression in expressions:
            try:
                match = IO.fullmatch(expression)
                if match:
                    signal = self._get(self._get(self._get(self.robot, "IOTypes"), "Item", IO_CODES[match[1]]), "Signals")
                    signal = self._get(signal, "Item", int(match[2]))
                    self.binding.call(signal, "Refresh")
                    values[expression] = {"value": self._get(signal, "Value"), "assigned": bool(self._get(signal, "IsAssigned"))}
                else:
                    values[expression] = self.read_register(expression)
            except Exception as exc:
                errors[expression] = type(exc).__name__
        return {"timestamp": time.time(), "provider": "frrobot", "values": values, "errors": errors}

    def status(self, group=1, alarm_limit=20):
        result = {"provider": "frrobot", "timestamp": time.time(), "host": self._get(self.robot, "HostName"), "errors": {}}
        for name, read in {
            "position": lambda: self.position(self._get(self._get(self.robot, "CurPosition"), "Group", group, 1)),
            "tasks": self.tasks,
            "alarms": lambda: self.alarms(alarm_limit),
        }.items():
            try:
                result[name] = read()
            except Exception as exc:
                result["errors"][name] = type(exc).__name__
        return result

    def tasks(self):
        rows = []
        for task in self.binding.items(self._get(self.robot, "Tasks"), 256):
            status = int(self._get(task, "Status"))
            try:
                program = self._get(task, "CurProgram")
                program = self._get(program, "Name") if program is not None else None
            except Exception:
                if status not in {2, -32768}:
                    raise
                program = None  # Aborted/idle system tasks can have no program.
            rows.append({"name": self._get(task, "Name"), "status_code": status,
                "program_type": int(self._get(task, "ProgramType")),
                "current_line": int(self._get(task, "CurLine")),
                "program": program})
        return rows

    def alarms(self, limit):
        alarms = self._get(self.robot, "Alarms")
        count = int(self._get(alarms, "Count"))
        if not 0 <= count <= 10000:
            raise ValueError("Alarm collection is too large")
        rows = []
        for i in range(1, min(count, limit) + 1):
            alarm = self._get(alarms, "Item", i)
            rows.append({"mnemonic": self._get(alarm, "ErrorMnemonic"), "number": self._get(alarm, "ErrorNumber"),
                "message": self._get(alarm, "ErrorMessage"), "timestamp": str(self._get(alarm, "TimeStamp")),
                "severity": self._get(alarm, "SeverityMessage")})
        return {"active_alarm_flag": bool(self._get(alarms, "ActiveAlarms")), "history": rows, "total": count}

    def programs(self):
        rows = []
        for program in self.binding.items(self._get(self.robot, "Programs"), 10000):
            rows.append({"name": self._get(program, "Name"), "comment": self._get(program, "Comment"),
                "bytes": int(self._get(program, "Size")), "protection": int(self._get(program, "Protection"))})
        return rows

    def write_register(self, target, expected, proposed):
        before = self.read_register(target)
        if before != expected:
            raise ValueError("Native register changed since review")
        # Validate again inside the vendor worker; no arbitrary property paths.
        from fanuc_workbench.services.native_values import propose_register
        change = {"type": proposed["type"], "value": proposed["value"]} if before["kind"] == "R" else {
            "values": {axis: value for axis, value in proposed["values"].items() if value != before["values"].get(axis)}}
        if propose_register(before, change) != proposed:
            raise ValueError("Native proposal no longer matches its contract")
        obj = self._register(target)
        if before["kind"] == "R":
            self.binding.put(obj, "RegLong" if proposed["type"] == "integer" else "RegFloat", proposed["value"])
        else:
            # Disable automatic writes while staging axis fields, then send one
            # explicit Update. Frames, tool, config and external axes are untouched.
            # Some controller register objects reject this setter even when
            # automatic updating is already disabled. Read the actual mode and
            # only change it when needed; never stage fields in automatic mode.
            if self._get(obj, "AutomaticUpdate"):
                self.binding.put(obj, "AutomaticUpdate", False)
            if self._get(obj, "AutomaticUpdate"):
                raise ValueError("Cannot disable automatic native position updates")
            fmt = self._get(obj, "Formats", before["native_type"])
            for axis, value in proposed["values"].items():
                if value != before["values"][axis]:
                    if before["representation"] == "joint":
                        self.binding.put(fmt, "Item", value, int(axis[1:]))
                    else:
                        self.binding.put(fmt, axis, value)
            self.binding.call(obj, "Update")
        after = self.read_register(target)
        if not register_matches(after, proposed):
            raise RuntimeError("Native write readback differs; inspect before another change")
        return {"status": "verified", "value": after, "value_sha256": fingerprint(after),
                "verification": "exact" if before["kind"] == "R" else "axis absolute tolerance 0.0001 mm/degrees; metadata exact"}

    def load_program(self, source, name, expected_programs):
        # Refuse changes to a program referenced by any non-idle task. Do not
        # pause, abort, select or run anything to make a load succeed.
        for task in self.tasks():
            if task["program_type"] == 1 and task["status_code"] not in {2, -32768}:
                raise ValueError("Finish active or paused TP tasks before native program loading")
            if task["program"] == name and task["status_code"] != -32768:
                raise ValueError("Program is referenced by an active or paused task")
        current = next((row for row in self.programs() if row["name"].upper() == name), None)
        if current != expected_programs:
            raise ValueError("Native program metadata changed since preflight")
        self.binding.call(self.robot, "Load", source, 1 if current else 0)
        loaded = next((row for row in self.programs() if row["name"].upper() == name), None)
        if loaded is None:
            raise RuntimeError("Loader returned without a matching native program")
        return {"status": "native_loader_accepted", "program": loaded, "executed": False}
