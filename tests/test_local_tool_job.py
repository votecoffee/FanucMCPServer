"""Inert ownership/IPC adapters; these tests never create a native process."""
from contextlib import nullcontext
import base64
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from fanuc_workbench.bridge import local_tool_job as module
from fanuc_workbench.bridge.owned_workers import WorkerInventory
from fanuc_workbench.bridge.storage import atomic_json


@pytest.mark.parametrize("changed", [False, True])
def test_explicit_executable_hash_and_request_limits(tmp_path, changed):
    executable = tmp_path / "maketp.exe"; executable.write_bytes(b"inert; never executed")
    expected = hashlib.sha256(executable.read_bytes()).hexdigest()
    if changed: executable.write_bytes(b"changed")
    if changed:
        with pytest.raises(ValueError, match="reviewed SHA"):
            module.validate_request([str(executable)], tmp_path, 1, expected)
    else:
        assert module.validate_request([str(executable)], tmp_path, 1, expected)["executable_sha256"] == expected
        with pytest.raises(ValueError, match="byte limit"):
            module.validate_request([str(executable), *("x" * 32768 for _ in range(3))], tmp_path, 1)


@pytest.mark.parametrize("arguments", [["maketp.exe"], ["cmd.exe", "/c", "anything"], [], ["x\0"]])
def test_arbitrary_command_is_refused_without_any_launch(tmp_path, arguments):
    with pytest.raises(ValueError, match="MakeTP/PrintTP"):
        module.validate_request(arguments, tmp_path, 1)


def fake_job(tmp_path, monkeypatch, outcome="terminated", broken=False):
    from universal_tools import resources, management, instance_lock
    from fanuc_workbench.bridge import owned_workers
    events=[]; inventory=WorkerInventory(limit=1)
    monkeypatch.setattr(owned_workers, "workers", inventory)
    monkeypatch.setattr(management, "private_directory", lambda path: path.mkdir(parents=True, exist_ok=True))
    monkeypatch.setattr(instance_lock, "InstanceLock", lambda path: nullcontext())
    identity={"host_id":"a"*64,"pid":999999,"created":"123", "executable":os.path.normcase(str(Path(module.sys._base_executable).resolve()))}
    member=SimpleNamespace(model_dump=lambda **kwargs: identity)
    record=SimpleNamespace(resource_id="b"*32,owner_id="c"*32,owner=member,members=[member])
    class Handle:
        def __enter__(self): events.append("retain-actual-worker"); return self
        def __exit__(self,*args): events.append("close-retained-handle")
    driver=SimpleNamespace(open=lambda value: Handle(),kernel=SimpleNamespace(WaitForSingleObject=lambda *args:258))
    class Launch:
        resource_id=record.resource_id
        group=SimpleNamespace(info=SimpleNamespace(process=object()))
        def refresh(self): events.append("verify-private-job-membership"); return record
        def close(self): events.append("close-exact-job"); return {"outcome":outcome}
    class Owner:
        def launch(self, argv, label, **options):
            assert argv[1:4]==["-I","-B","-m"] and argv[4]=="fanuc_workbench.bridge.local_tool_worker"
            assert options["max_processes"]==8 and options["allow_termination"] and not options["show_window"]
            assert "PYTHONPATH" not in options["env"]
            path=Path(argv[argv.index("--request")+1]); request=json.loads(path.read_text())
            assert (path.parent/"receipt.json").is_file()
            events.append("durable-atomic-launch")
            atomic_json(path.parent/"boot.json",{"nonce":request["nonce"],"identity":identity,"request_sha256":argv[-1]})
            return Launch()
        def close(self): events.append("owner-close")
    ledger=SimpleNamespace(directory=tmp_path/"registry/resources",driver=driver,records=lambda:[],owner=lambda app:Owner())
    monkeypatch.setattr(resources,"ResourceLedger",lambda *args,**kwargs:ledger)
    monkeypatch.setattr(module,"bounded_driver",lambda:driver)
    original=module.LocalToolJob._await
    def await_file(self,path,deadline):
        if path.name=="result.json":
            activation=json.loads((path.parent/"activate.json").read_text())
            assert activation["resource_id"]==record.resource_id
            assert events.index("verify-private-job-membership")<len(events)
            events.append("activated-before-native-result")
            request=json.loads((path.parent/"request.json").read_text())
            response={"nonce":request["nonce"],"identity":identity,"request_sha256":module.digest(path.parent/"request.json",65536),
                "state":{"returncode":0},"output_base64":base64.b64encode(b"retained").decode()}
            if broken: response["nonce"]="wrong"
            atomic_json(path,response)
        original(self,path,deadline)
    monkeypatch.setattr(module.LocalToolJob,"_await",await_file)
    return events,inventory


@pytest.mark.parametrize("outcome,broken",[("terminated",False),("uncertain",False),("terminated",True)])
@pytest.mark.skipif(os.name != "nt", reason="Windows utility job adapter")
def test_identity_activation_cleanup_and_uncertainty_are_not_bypassed(tmp_path,monkeypatch,outcome,broken):
    events,inventory=fake_job(tmp_path,monkeypatch,outcome,broken)
    executable=tmp_path/"maketp.exe"; executable.write_bytes(b"not executable")
    if outcome=="terminated" and not broken:
        state,output=module.run_tool([str(executable)],tmp_path,1)
        assert state["returncode"]==0 and output==b"retained"
        assert events[-1]=="owner-close"
    else:
        with pytest.raises((RuntimeError,ValueError)):
            module.run_tool([str(executable)],tmp_path,1)
        receipt=json.loads(next(tmp_path.glob("native-tool-*/receipt.json")).read_text())
        assert receipt["state"]=="uncertain" and receipt["never_replay"]
    assert "close-exact-job" in events
    assert inventory.snapshot()["uncertain"]==(outcome=="uncertain")


@pytest.mark.skipif(os.name != "nt", reason="Windows job limits")
def test_native_job_memory_cap_is_set_while_still_suspended(monkeypatch):
    import win32job
    from universal_tools.windows_resources import ProcessDriver
    events=[]; group=SimpleNamespace(job=object(),close=lambda:events.append("close"))
    monkeypatch.setattr(ProcessDriver,"create_suspended",lambda *args,**kwargs:events.append("suspended") or group)
    monkeypatch.setattr(win32job,"QueryInformationJobObject",lambda *args:{"BasicLimitInformation":{"LimitFlags":0}})
    def set_limits(job,kind,limits):
        assert events==["suspended"] and limits["JobMemoryLimit"]==512*1024*1024
        events.append("memory-cap-before-bind-and-resume")
    monkeypatch.setattr(win32job,"SetInformationJobObject",set_limits)
    assert module.bounded_driver().create_suspended(["inert"]) is group
    assert events==["suspended","memory-cap-before-bind-and-resume"]
