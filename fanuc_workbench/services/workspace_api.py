from __future__ import annotations

import math
import shutil
from dataclasses import asdict, is_dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fanuc_workbench.parsers.comments import parse_comment_file, set_comment
from fanuc_workbench.parsers.data import (parse_numeric_registers, parse_position_registers, parse_frames,
                                        parse_io, set_numeric_register_value, set_position_register)
from fanuc_workbench.services import diagnostics, inventory, ethernetip, workspace, xref
from fanuc_workbench.services.robot_lint import lint_workspace
from fanuc_workbench.services.compare import compare_workspaces


def serial(value):
    if is_dataclass(value):
        return serial(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): serial(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [serial(item) for item in (sorted(value) if isinstance(value, set) else value)]
    return value


def page(rows, offset=0, limit=100):
    if offset < 0 or not 1 <= limit <= 500:
        raise ValueError("Use offset >= 0 and limit between 1 and 500")
    return {"items": serial(rows[offset:offset + limit]), "total": len(rows), "offset": offset,
            "next_offset": offset + limit if offset + limit < len(rows) else None}


from fanuc_workbench.services.analysis_catalog import ANALYSES


class WorkspaceService:
    """Shared, read-only evidence analysis and edits to separate working copies."""

    @staticmethod
    def analyze(root: Path, kind: str, offset=0, limit=100, query="") -> dict:
        functions = {"health": diagnostics.health_summary, "options": inventory.option_inventory,
                     "alarms": diagnostics.scan_alarms, "alarm_intelligence": diagnostics.alarm_intelligence,
                     "mastering": diagnostics.mastering_evidence, "dcs": diagnostics.dcs_evidence,
                     "ethernetip": ethernetip.scan_ethernetip, "lint": lint_workspace,
                     "programs": workspace.scan_programs, "dependencies": xref.program_dependencies,
                     "unused_programs": xref.unused_programs, "xref": xref.build_xref,
                     "register_usage": xref.register_usage, "comments": workspace.scan_comments,
                     "positions": workspace.scan_positions, "variables": workspace.scan_variables}
        if kind in functions:
            result = functions[kind](root)
            if isinstance(result, dict):
                result = [{"name": key, "value": value} for key, value in result.items()]
        elif kind in {"numeric_registers", "position_registers", "frames", "io"}:
            name, parser = {"numeric_registers": ("NUMREG.VA", parse_numeric_registers),
                            "position_registers": ("POSREG.VA", parse_position_registers),
                            "frames": ("FRAME.DG", parse_frames), "io": ("IOSTATE.DG", parse_io)}[kind]
            result = [row for path in root.rglob("*") if path.is_file() and path.name.upper() == name for row in parser(path)]
        else:
            raise ValueError("Unknown analysis; use one of: " + ", ".join(ANALYSES))
        result = serial(result)
        if query:
            result = [row for row in result if query.casefold() in str(row).casefold()]
        return {"analysis": kind, **page(result, offset, limit), "evidence_only": True}

    @staticmethod
    def compare(before: Path, after: Path, offset=0, limit=100) -> dict:
        return page(compare_workspaces(before, after), offset, limit)

    @staticmethod
    def edit(source: Path, destination: Path, kind: str, target: str, value: dict) -> dict:
        """Never modify source; malformed or unsupported edits leave no proposed copy."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError(destination)
        shutil.copyfile(source, destination)
        try:
            if kind == "comment":
                rows = [row for row in parse_comment_file(destination) if row.identifier == target]
                if len(rows) != 1:
                    raise ValueError("Comment identifier must identify exactly one record in this file")
                comment = value.get("comment")
                if not isinstance(comment, str) or len(comment) > 256:
                    raise ValueError("Supply comment text with at most 256 characters")
                set_comment(rows[0], comment)
            elif kind == "numeric_register":
                rows = [row for row in parse_numeric_registers(destination) if f"R[{row.index}]" == target]
                if len(rows) != 1:
                    raise ValueError("Numeric register must identify exactly one existing record")
                number = str(value["value"])
                try:
                    if not Decimal(number).is_finite():
                        raise ValueError("Value must be finite")
                except InvalidOperation:
                    raise ValueError("Value must be numeric") from None
                set_numeric_register_value(rows[0], number)
            elif kind == "position_register":
                rows = [row for row in parse_position_registers(destination)
                        if (f"PR[{row.index}]" if row.group == 1 else f"PR[{row.group},{row.index}]") == target]
                if len(rows) != 1:
                    raise ValueError("Position register must identify exactly one existing record")
                row = rows[0]
                if not value or set(value) - {"values", "comment", "user_frame", "user_tool"}:
                    raise ValueError("Unsupported PR edit fields")
                axes = value.get("values", {})
                if set(axes) - set(row.values) or any(not math.isfinite(float(x)) for x in axes.values()):
                    raise ValueError("PR axes must exist in the source representation and have finite values")
                for field in ("user_frame", "user_tool"):
                    if field in value and (not str(value[field]).isdigit() or not getattr(row, field)):
                        raise ValueError("UF/UT edit requires an existing field and nonnegative integer")
                comment = value.get("comment")
                if comment is not None and (not isinstance(comment, str) or any(c in comment for c in "\r\n'")):
                    raise ValueError("Invalid PR comment")
                set_position_register(row, **value)
            else:
                raise ValueError("Edit kind must be comment, numeric_register or position_register")
            return {"path": str(destination), "controller_written": False,
                    "generated_listing": destination.suffix.upper() in {".VA", ".DG"},
                    "controller_restore_support": "not_verified"}
        except Exception:
            destination.unlink(missing_ok=True)
            raise
