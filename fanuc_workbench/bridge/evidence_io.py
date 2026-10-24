"""Small shared evidence primitives, independent of controller/analysis owners."""
from datetime import datetime, timezone
import json
import os
import uuid


def append_audit(bridge, action, **fields):
    record = {"at": datetime.now(timezone.utc).isoformat(), "action": action, **fields}
    if bridge.requests.current_request_id:
        record["request_id"] = bridge.requests.current_request_id
    with bridge._audit_lock, bridge.audit_locks.acquire("journal", timeout=15), (bridge.config.state_dir / "audit.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def artifact_folder(bridge):
    folder = bridge.config.state_dir / uuid.uuid4().hex
    folder.mkdir()
    return folder
