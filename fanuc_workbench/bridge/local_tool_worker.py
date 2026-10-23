"""One-shot background compiler worker; no inherited stdio, GUI or controller."""
import argparse
import base64
import os
from pathlib import Path
import time

from fanuc_workbench.bridge.local_tool_job import digest, read_message, validate_request
from fanuc_workbench.bridge.storage import atomic_json


class StepReceipt:
    def __init__(self, folder, activation, request, driver):
        self.path = folder / "compiler-intent.json"
        self.driver, self.handle = driver, None
        self.data = {"state": "launching", "parent_resource_id": activation["resource_id"],
            "parent_owner_id": activation["owner_id"], "parent_owner": activation["owner"],
            "executable": request["arguments"][0], "executable_sha256": request["executable_sha256"]}
        atomic_json(self.path, self.data)  # Durable intent before CreateProcess.

    def bind_handle(self, handle, pid):
        self.handle = handle
        self.data.update(state="running", identity=self.driver.capture_handle(handle, pid))
        if self.data["identity"]["executable"] != os.path.normcase(self.data["executable"]):
            raise ValueError("Compiler image differs from its reviewed executable")
        atomic_json(self.path, self.data)

    def exited(self, evidence):
        if self.handle is None or self.driver.kernel.WaitForSingleObject(self.handle, 0) != 0:
            raise RuntimeError("Compiler retained handle has not signaled exit")
        self.data.update(state="exited", evidence=evidence)
        atomic_json(self.path, self.data)

    def uncertain(self, reason):
        self.data.update(state="uncertain", reason=reason, never_replay=True)
        atomic_json(self.path, self.data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args(argv)
    path = args.request.absolute(); folder = path.parent
    if digest(path, 65536) != args.sha256:
        raise ValueError("Native utility request changed before bootstrap")
    request = read_message(path, 65536)
    from universal_tools.windows_resources import ProcessDriver
    driver = ProcessDriver(); identity = driver.capture(os.getpid())
    atomic_json(folder / "boot.json", {"nonce": request["nonce"], "identity": identity, "request_sha256": args.sha256})
    deadline = time.monotonic() + 10
    while not (folder / "activate.json").is_file():
        if time.monotonic() >= deadline:
            raise TimeoutError("Native utility was never activated")
        time.sleep(.02)
    activation = read_message(folder / "activate.json", 65536)
    if activation.get("nonce") != request["nonce"] or activation.get("identity") != identity:
        raise ValueError("Native worker activation identity does not match")
    response = {"nonce": request["nonce"], "identity": identity, "request_sha256": args.sha256}
    try:
        validate_request(request["arguments"], request["cwd"], request["timeout"], request["executable_sha256"])
        from fanuc_workbench.services.native_validation import _run_process
        claim = StepReceipt(folder, activation, request, driver)
        state, output = _run_process(request["arguments"], request["cwd"], request["timeout"], contained=True, claim=claim)
        response.update(state=state, output_base64=base64.b64encode(output).decode("ascii"))
    except Exception as exc:
        response.update(error_type=type(exc).__name__)
    atomic_json(folder / "result.json", response)


if __name__ == "__main__":
    main()
