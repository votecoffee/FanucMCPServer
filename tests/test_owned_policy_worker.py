"""Fixed metadata execution and launcher routing; no child/MCP/native starts."""
import hashlib
import json
from types import SimpleNamespace

import pytest
from universal_tools.models import Settings

from fanuc_workbench.bridge import policy_editor
from fanuc_workbench.bridge.policy_worker import run_policy


def test_fixed_policy_validation_redacts_invalid_credential_values():
    secret = "private-value-do-not-echo"
    result = run_policy({"document": {"token": secret}, "action": "validate"})
    assert result["ok"] is False and secret not in json.dumps(result)


def test_fixed_policy_save_preserves_backup_and_stale_refusal(tmp_path):
    document = Settings(token="t" * 48, port=19801).model_dump()
    path = tmp_path / "settings.json"
    original = json.dumps(document).encode()
    path.write_bytes(original)
    proposed = {**document, "port": 19802}
    request = {"document": proposed, "action": "save", "path": str(path), "state": str(tmp_path),
               "original_sha256": hashlib.sha256(original).hexdigest()}
    result = run_policy(request)
    assert result["ok"] and json.loads(path.read_bytes()) == proposed
    from pathlib import Path
    assert Path(result["value"]["backup"]).read_bytes() == original
    changed = path.read_bytes()
    denied = run_policy(request)
    assert denied["ok"] is False and "changed since loading" in denied["message"]
    assert path.read_bytes() == changed


def test_host_policy_uses_selected_runtime_owned_ipc_and_closes_on_refusal(tmp_path, monkeypatch):
    from fanuc_workbench.bridge import extension_process
    events = []
    runtime = SimpleNamespace(python=tmp_path / "selected-python.exe", state=tmp_path)
    class Worker:
        def __init__(self, command, fields, **kwargs):
            assert command[0] == str(runtime.python) and command[-1].endswith("policy_worker.py")
            assert fields == {"provider": "host-policy"}
            events.append("owned-start")
        def call(self, operation, arguments, context):
            assert operation == "policy" and arguments["state"] == str(tmp_path)
            events.append("fixed-policy")
            return {"ok": False, "message": "Reconcile retained work or recovery before changing app-host settings"}
        def close(self):
            events.append("close-owned")
    monkeypatch.setattr(extension_process, "RpcProcess", Worker)
    with pytest.raises(ValueError, match="Reconcile retained"):
        policy_editor._host_call(runtime, "host_settings", {"token": "t" * 48})
    assert events == ["owned-start", "fixed-policy", "close-owned"]
