"""Reviewed local policy editing shared by desktop frontends; no MCP write tool."""
from __future__ import annotations

import copy
from dataclasses import dataclass
import difflib
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import uuid

from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.storage import RobotLocks


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(document):
    return (json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _commit(path, original, proposed):
    if path.read_bytes() != original:
        raise ValueError("The policy changed since loading; reload it before saving")
    history = path.parent / ".fanuc-policy-history"
    history.mkdir(mode=0o700, exist_ok=True)
    backup = history / (path.name + "." + digest(original) + "." + uuid.uuid4().hex + ".bak")
    descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(original)
        stream.flush()
        os.fsync(stream.fileno())
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(proposed)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        if path.read_bytes() != original:
            raise ValueError("The policy changed during review; reload it before saving")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return backup


@dataclass(frozen=True)
class BrokerRuntime:
    python: Path
    state: Path

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        python, state = Path(data["python"]), Path(data["state"])
        if not python.is_absolute() or not python.is_file() or not state.is_absolute() or not (state / "settings.json").is_file():
            raise ValueError("Select an initialized broker runtime.json with an absolute Python and state path")
        return cls(python.absolute(), state.resolve())


@dataclass(frozen=True)
class PolicySnapshot:
    path: Path
    kind: str
    raw: bytes
    broker: BrokerRuntime | None = None

    @property
    def sha256(self):
        return digest(self.raw)

    @property
    def document(self):
        return json.loads(self.raw.decode("utf-8"))


@dataclass(frozen=True)
class PolicyReview:
    snapshot: PolicySnapshot
    proposed: bytes
    diff: str

    @property
    def sha256(self):
        return digest(self.proposed)


# Run only in the explicitly selected broker runtime. Metadata validation never
# starts the daemon. Registered policy writes require its actual administrative
# locks and no retained FANUC context, including a durable recovery barrier.
_BROKER_CODE = '''
import hashlib,json,os,stat,sys,uuid
from pathlib import Path
from universal_tools.models import ServiceManifest,Settings
request=json.load(sys.stdin)
kind=request['kind']; document=request['document']
model=(ServiceManifest if kind=='broker' else Settings).model_validate_json(json.dumps(document,allow_nan=False))
if kind=='broker' and model.id!='fanuc-mcp-server':
    raise ValueError('Select the FanucMCPServer service registration')
if request['action']=='save':
    from contextlib import ExitStack
    from universal_tools.daemon import InstanceLock
    from universal_tools.ownership import OwnershipJournal
    from universal_tools.models import atomic_json
    path=Path(request['path']).resolve(); state=Path(request['state']).resolve()
    registered=path==(state/'services/fanuc-mcp-server.json').resolve()
    if kind=='broker_limits' and path!=(state/'settings.json').resolve():
        raise ValueError('Select the settings.json for this runtime')
    with ExitStack() as locks:
        if registered or kind=='broker_limits':
            locks.enter_context(InstanceLock(state/'registration.lock'))
            locks.enter_context(InstanceLock(state/'broker.lock'))
            if any(kind=='broker_limits' or row.get('service_id')=='fanuc-mcp-server' for row in OwnershipJournal(state/'ownership.sqlite3').entries()):
                raise ValueError('Reconcile retained FANUC work or recovery before changing broker policy')
        original=path.read_bytes(); original_mode=stat.S_IMODE(path.stat().st_mode)
        if hashlib.sha256(original).hexdigest()!=request['original_sha256']:
            raise ValueError('The policy changed since loading; reload it before saving')
        before=json.loads(original)
        allowed=({'enabled','allow_writes','allowed_write_tools','read_only_tools','max_argument_bytes',
                  'timeout_seconds','session_mode','lease_seconds','resource_id'} if kind=='broker' else {'max_request_body_bytes','port','token'})
        if any(before.get(key)!=document.get(key) for key in set(before)|set(document) if key not in allowed):
            raise ValueError('Identity, transport and authentication are not policy controls')
        history=path.parent/'.fanuc-policy-history'; history.mkdir(mode=0o700,exist_ok=True)
        backup=history/(path.name+'.'+request['original_sha256']+'.'+uuid.uuid4().hex+'.bak')
        fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as stream: stream.write(original)
        atomic_json(path,document)
        os.chmod(path,original_mode)
        print(json.dumps({'backup':str(backup)}))
else:
    print(json.dumps({'valid':True}))
'''


def _broker_call(runtime, kind, document, action="validate", **kwargs):
    if runtime is None:
        raise ValueError("Select the broker runtime.json before editing its policy")
    environment = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
    environment.update(PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    try:
        result = subprocess.run([str(runtime.python), "-B", "-c", _BROKER_CODE],
            input=json.dumps({"kind": kind, "document": document, "action": action, "state": str(runtime.state), **kwargs}, allow_nan=False),
            env=environment, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("The selected broker runtime could not validate this policy within its deadline") from None
    if result.returncode:
        # Validation errors can echo credential inputs. Show only the last error
        # category; paths and full documents remain local and are not logged.
        if "already running" in result.stderr or "already held" in result.stderr:
            raise ValueError("Stop the broker before changing its registered policy")
        message = result.stderr.splitlines()[-1] if result.stderr else "Broker validation failed"
        if message.startswith("ValueError:"):
            raise ValueError(message.removeprefix("ValueError: "))
        raise ValueError("Broker policy validation/save failed; check supported values, stop the broker and reconcile retained work before saving")
    return json.loads(result.stdout)


def validate_policy(document, kind, path, broker=None):
    encoded(document)  # Refuse non-finite/non-JSON values, including unknown metadata.
    if kind == "robot":
        BridgeConfig.from_document(document, path)
    elif kind in {"broker", "broker_limits"}:
        _broker_call(broker, kind, document)
    elif kind == "application":
        try:
            from universal_tools.framework import ApplicationDefinition
        except ImportError:
            raise ValueError("Install the app's pinned framework dependency to edit its publication") from None
        model = ApplicationDefinition.model_validate(document)
        if model.id != "fanuc-mcp-server" or len(model.backends) != 1 or model.backends[0].service.id != model.id:
            raise ValueError("Select the FanucMCPServer application publication")
    else:
        raise ValueError("Unknown policy kind")


def load_policy(path, kind="robot", broker=None):
    path = Path(path).resolve(strict=True)
    raw = path.read_bytes()
    snapshot = PolicySnapshot(path, kind, raw, broker)
    validate_policy(snapshot.document, kind, path, broker)
    if path.read_bytes() != raw:
        raise ValueError("The policy changed while loading; load it again")
    return snapshot


def review_policy(snapshot, document):
    document = copy.deepcopy(document)
    if snapshot.kind == "application":
        from fanuc_workbench.bridge.framework_host import POLICY_KEYS, revision
        before = snapshot.document
        proposed_service = document["backends"][0]["service"]
        allowed = copy.deepcopy(before)
        for key in POLICY_KEYS:
            if key in proposed_service:
                allowed["backends"][0]["service"][key] = proposed_service[key]
            else:
                allowed["backends"][0]["service"].pop(key, None)
        if document != allowed:
            raise ValueError("Only access policy can be edited; publish backend code/identity changes through the app updater")
        if document != before:
            document["backends"][0]["revision"] = revision(document["backends"][0])
    validate_policy(document, snapshot.kind, snapshot.path, snapshot.broker)
    if snapshot.path.read_bytes() != snapshot.raw:
        raise ValueError("The policy changed since loading; reload it before review")
    before = snapshot.document
    proposed = snapshot.raw if document == before else encoded(document)
    if snapshot.kind == "broker_limits":
        changed_token = before.get("token") != document.get("token")
        before["token"] = "<authentication token>"
        document["token"] = "<new authentication token>" if changed_token else "<authentication token>"
    changes = "".join(difflib.unified_diff(encoded(before).decode().splitlines(True),
        encoded(document).decode().splitlines(True), fromfile="Saved policy", tofile="Proposed policy"))
    return PolicyReview(snapshot, proposed, changes or "No changes. Original bytes will be retained.")


def save_policy(review):
    snapshot = review.snapshot
    if snapshot.path.read_bytes() != snapshot.raw:
        raise ValueError("The policy changed since loading; reload it before saving")
    if review.proposed == snapshot.raw:
        return snapshot, None
    document = json.loads(review.proposed)
    validate_policy(document, snapshot.kind, snapshot.path, snapshot.broker)
    if snapshot.kind in {"robot", "application"}:
        history = snapshot.path.parent / ".fanuc-policy-history"
        history.mkdir(mode=0o700, exist_ok=True)
        with RobotLocks(history / "locks").acquire(str(snapshot.path)):
            backup = _commit(snapshot.path, snapshot.raw, review.proposed)
    else:
        result = _broker_call(snapshot.broker, snapshot.kind, document, "save",
                              path=str(snapshot.path), original_sha256=snapshot.sha256)
        backup = Path(result["backup"])
    saved = load_policy(snapshot.path, snapshot.kind, snapshot.broker)
    if saved.document != document:
        raise ValueError("Saved policy differs from the reviewed proposal; inspect the file and retained backup")
    return saved, backup
