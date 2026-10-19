from __future__ import annotations

import base64
import asyncio
import json
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from test_robot_bridge import MemoryRobot, ORIGINAL, PROPOSED, digest
from fanuc_workbench.bridge.config import BridgeConfig, RobotAccess
from fanuc_workbench.bridge.service import RobotBridge
from fanuc_workbench.bridge.jobs import JobManager
from fanuc_workbench.bridge.storage import RobotLocks, RobotBusyError, atomic_json
from fanuc_workbench.core.models import RobotProfile
from fanuc_workbench.services.monitoring import Sample, TimelineRecorder, WorkspaceSampleProvider, WatchSpec
from fanuc_workbench.services.workspace_api import ANALYSES


@pytest.fixture
def bridge_case(tmp_path):
    source = tmp_path / "edits"
    source.mkdir()
    robot = MemoryRobot()
    robot.files.update({"md:NUMREG.VA": b"[1] = 42 'Count'\r\n", "md:IOSTATE.DG": b"DIN[1] ON Sensor\nFLG[1] OFF Flag\n",
                        "md:POSREG.VA": b"[1,1] = 'Pick'\nUF: 1, UT: 1, X: 100, Y: 200, Z: 300, W: 0, P: 0, R: 90\n"
                                           b"[2,7] = 'Group two'\nUF: 1, UT: 1, J1: 7, J2: 8\n",
                        "md:FRAME.DG": b"User Frame\n1 2 3 0 0 0 Fixture\n",
                        "md:EIP.DG": b"KAREL option\nEtherNet/IP Adapter IP=192.0.2.1\n"})
    profile = RobotProfile("Test", "192.0.2.1")
    config = BridgeConfig(tmp_path / "state", source, {"r1": RobotAccess(profile, writable_files=("md:MAIN.LS",))}, True)
    bridge = RobotBridge(config, lambda _: robot)
    try:
        yield bridge, robot, config
    finally:
        bridge.close()


def imported(bridge, robot, config, name="backup"):
    folder = config.source_dir / name
    folder.mkdir()
    for remote, data in robot.files.items():
        (folder / remote.split(":")[1]).write_bytes(data)
    return bridge.operations.import_workspace(name)["artifact_id"]


def finished(jobs, identifier):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        item = jobs.status(identifier)
        if item["status"] in {"complete", "failed", "cancelled", "interrupted"}:
            return item
        time.sleep(.01)
    pytest.fail("Job failed to finish within ten seconds")


@pytest.mark.parametrize("kind", ANALYSES)
def test_all_workspace_analyses_use_preserved_manifest(bridge_case, kind):
    bridge, robot, config = bridge_case
    identifier = imported(bridge, robot, config)
    result = bridge.operations.analyze(identifier, kind, limit=2)
    assert result["analysis"] == kind and result["evidence_only"]
    assert len(result["items"]) <= 2
    assert robot.connects == robot.uploads == 0


def test_workspace_ranges_tamper_detection_and_import_confinement(bridge_case):
    bridge, robot, config = bridge_case
    identifier = imported(bridge, robot, config)
    result = bridge.operations.store.read(identifier, "MAIN.LS", 2, 10)
    assert base64.b64decode(result["base64"]) == ORIGINAL[2:12]
    with pytest.raises(ValueError):
        bridge.operations.store.read(identifier, "../MAIN.LS")
    with pytest.raises(ValueError):
        bridge.operations.import_workspace("../")
    root = bridge.operations.store.root(identifier)
    (root / "MAIN.LS").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        bridge.operations.analyze(identifier, "health")


def test_file_set_change_is_rejected(bridge_case):
    bridge, robot, config = bridge_case
    identifier = imported(bridge, robot, config)
    (bridge.operations.store.root(identifier) / "EXTRA.LS").write_bytes(ORIGINAL)
    with pytest.raises(ValueError, match="file set"):
        bridge.operations.analyze(identifier, "lint")


def test_offline_edits_leave_source_unchanged_and_register_listing_unrestored(bridge_case):
    bridge, robot, config = bridge_case
    identifier = imported(bridge, robot, config)
    result = bridge.operations.edit(identifier, "NUMREG.VA", "numeric_register", "R[1]", {"value": "123.25"})
    assert b"123.25" in Path(result["path"]).read_bytes()
    assert bridge.operations.store.file(identifier, "NUMREG.VA").read_bytes() == robot.files["md:NUMREG.VA"]
    assert not result["controller_written"] and result["generated_listing"]
    position = bridge.operations.edit(identifier, "POSREG.VA", "position_register", "PR[2,7]", {"values": {"J2": 9.5}})
    from fanuc_workbench.parsers.data import parse_position_registers
    assert parse_position_registers(Path(position["path"]))[1].values["J2"] == 9.5
    with pytest.raises(ValueError, match="axes"):
        bridge.operations.edit(identifier, "POSREG.VA", "position_register", "PR[2,7]", {"values": {"X": 9}})
    with pytest.raises(ValueError):
        bridge.operations.edit(identifier, "NUMREG.VA", "numeric_register", "R[1]", {"value": "NaN"})
    assert robot.uploads == 0


@pytest.mark.parametrize("encoding", ["utf-8", "cp1252"])
def test_offline_comment_edits_preserve_encoding_and_crlf(bridge_case, encoding):
    bridge, robot, config = bridge_case
    robot.files["md:NUMREG.VA"] = "[1] = 42 'Café'\r\n".encode(encoding)
    identifier = imported(bridge, robot, config)
    result = bridge.operations.edit(identifier, "NUMREG.VA", "comment", "R[1]", {"comment": "Café revised"})
    assert Path(result["path"]).read_bytes() == "[1] = 42 'Café revised'\r\n".encode(encoding)


def test_full_and_selective_backup_jobs_register_hashes(bridge_case):
    bridge, robot, _ = bridge_case
    for paths, count in [(None, len(robot.files)), (["md:MAIN.LS"], 1)]:
        job = bridge.operations.start_backup("r1", paths, attempt_ascii=False)
        status = finished(bridge.operations.jobs, job["job_id"])
        assert status["status"] == "complete", status
        assert status["result"]["complete"] is True
        item = bridge.operations.store.get(status["artifact_id"])
        assert len(item["files"]) == count + 1  # controller files plus backup manifest
        bridge.operations.store.verify(item["artifact_id"])
    assert robot.uploads == 0


def test_incomplete_backup_is_explicit_and_redacts_errors(bridge_case):
    bridge, robot, _ = bridge_case
    status = finished(bridge.operations.jobs, bridge.operations.start_backup("r1", ["md:MISSING.TXT"], attempt_ascii=False)["job_id"])
    assert status["status"] == "complete" and status["result"]["complete"] is False
    item = bridge.operations.store.get(status["artifact_id"])
    manifest = next(row["path"] for row in item["files"] if row["path"].endswith("backup_manifest.json"))
    data = json.loads(bridge.operations.store.file(item["artifact_id"], manifest).read_text())
    assert data["records"][0]["error"] == "KeyError"


def test_capture_size_limit_leaves_partial_artifact(bridge_case):
    bridge, robot, config = bridge_case
    limited = RobotBridge(replace(config, max_file_bytes=3), lambda _: robot)
    try:
        status = finished(limited.operations.jobs, limited.operations.start_backup("r1", ["md:MAIN.LS"], attempt_ascii=False)["job_id"])
        assert status["status"] == "failed" and status["artifact_id"]
        item = limited.operations.store.get(status["artifact_id"])
        assert all(not row["path"].endswith("MAIN.LS") for row in item["files"])
    finally:
        limited.close()


def test_cancellation_and_competing_robot_access(bridge_case):
    bridge, robot, _ = bridge_case
    entered, release = threading.Event(), threading.Event()
    original = robot.download_bounded

    def blocked(remote, local, maximum):
        entered.set()
        assert release.wait(5)
        original(remote, local, maximum)

    robot.download_bounded = blocked
    job = bridge.operations.start_backup("r1", attempt_ascii=False)
    assert entered.wait(5)
    try:
        with pytest.raises(RobotBusyError):
            bridge.read_file("r1", "md:MAIN.LS")
        assert bridge.operations.jobs.cancel(job["job_id"])["status"] == "cancel_requested"
    finally:
        release.set()
    status = finished(bridge.operations.jobs, job["job_id"])
    assert status["status"] == "cancelled" and status["artifact_id"]
    assert robot.uploads == 0


def test_locks_coordinate_distinct_bridge_instances(bridge_case):
    bridge, _, config = bridge_case
    other = RobotLocks(config.state_dir / "robot-locks")
    with bridge._robot_lock("r1"):
        with pytest.raises(RobotBusyError):
            with other.acquire("192.0.2.1"):
                pytest.fail("Lock was acquired twice")
    with other.acquire("192.0.2.1"):
        pass


def test_live_sampling_group_indices_flags_and_missing_values(bridge_case):
    bridge, robot, _ = bridge_case
    result = bridge.operations.sample("r1", ["R[1]", "PR[2,7]", "DI[1]", "F[1]", "R[99]"])
    assert result["values"]["R[1]"] == "42"
    assert result["values"]["PR[2,7]"]["J2"] == 8
    assert result["values"]["F[1]"] == "OFF"
    assert "R[99]" in result["errors"]
    assert bridge.operations.store.get(result["artifact_id"])["kind"] == "sample"
    with pytest.raises(ValueError, match="two seconds"):
        bridge.operations.sample("r1", ["R[1]"])
    with pytest.raises(ValueError):
        bridge.operations.sample("r1", ["R[1,2]"])


def test_sampling_interval_is_shared_between_bridge_clients(bridge_case):
    bridge, robot, config = bridge_case
    bridge.operations.sample("r1", ["R[1]"])
    with RobotBridge(config, lambda _: robot) as other:
        with pytest.raises(ValueError, match="two seconds"):
            other.operations.sample("r1", ["R[1]"])


def test_watch_job_generates_recordings_and_exports(bridge_case):
    bridge, robot, _ = bridge_case
    job = bridge.operations.start_watch("r1", ["R[1]"], samples=1, cycle_signal="R[1]")
    status = finished(bridge.operations.jobs, job["job_id"])
    assert status["status"] == "complete", status
    identifier = status["artifact_id"]
    files = {row["path"] for row in bridge.operations.store.get(identifier)["files"]}
    assert files == {"trend.csv", "timeline.csv", "cycle.json", "recording.json"}
    assert bridge.operations.analyze_recording(identifier)["profile_seconds"] == {}
    assert robot.uploads == 0


def test_missing_samples_do_not_invent_timeline_transitions():
    recorder = TimelineRecorder()
    recorder.add(Sample(1, {"DI[1]": "ON"}))
    recorder.add(Sample(2, {"DI[1]": None}, {"DI[1]": "unavailable"}))
    recorder.add(Sample(3, {"DI[1]": "ON"}))
    assert recorder.events == []


def test_snapshots_packages_inspection_and_recovery_plan(bridge_case):
    bridge, robot, config = bridge_case
    identifier = imported(bridge, robot, config)
    snapshot = bridge.operations.snapshot(identifier)
    assert bridge.operations.compare_snapshots(snapshot["artifact_id"], snapshot["artifact_id"])["total"] == 0
    assert bridge.operations.inspect_program(identifier, "MAIN.LS")["compiler_acceptance"] == "not_verified"
    for kind in ("deployment", "baseline", "roboguide"):
        package = bridge.operations.make_package(identifier, kind, ["MAIN.LS"])
        assert package["file_count"] > 0 and not package["controller_written"]
    robot.files["md:MAIN.LS"] = PROPOSED
    plan = bridge.operations.prepare_restore("r1", identifier, "MAIN.LS", "md:MAIN.LS", digest(PROPOSED))
    assert plan["proposed_sha256"] == digest(ORIGINAL) and robot.uploads == 0
    result = bridge.commit_upload(plan["plan_id"], plan["proposed_sha256"])
    assert result["status"] == "verified" and robot.files["md:MAIN.LS"] == ORIGINAL
    assert bridge.operations.audit()["total"] > 0


def test_approval_mode_does_not_accept_model_hash_alone(bridge_case):
    bridge, robot, config = bridge_case
    guarded = RobotBridge(replace(config, approval_mode="elicitation"), lambda _: robot)
    (config.source_dir / "MAIN.LS").write_bytes(PROPOSED)
    try:
        plan = guarded.prepare_upload("r1", "md:MAIN.LS", "MAIN.LS", digest(ORIGINAL))
        with pytest.raises(ValueError, match="elicitation"):
            guarded.commit_upload(plan["plan_id"], plan["proposed_sha256"])
        assert robot.uploads == 0
        assert guarded.upload_review(plan["plan_id"], plan["proposed_sha256"])["robot_id"] == "r1"
    finally:
        guarded.close()


def test_restart_marks_only_orphaned_jobs_interrupted(tmp_path):
    directory = tmp_path / "jobs"
    jobs = JobManager(directory)
    identifier = "a" * 32
    atomic_json(directory / (identifier + ".json"), {"job_id": identifier, "status": "running", "owner": "dead"})
    try:
        assert jobs.status(identifier)["status"] == "interrupted"
    finally:
        jobs.close()


def test_live_comment_policy_prepare_and_verified_readback(bridge_case, monkeypatch):
    bridge, robot, config = bridge_case
    with pytest.raises(ValueError, match="Live comments require"):
        bridge.comments.prepare("r1", "md:NUMREG.VA", "R[1]", "New count", digest(robot.files["md:NUMREG.VA"]))
    access = replace(config.robots["r1"], comment_service_url="http://192.0.2.1", comment_targets=("R[1]",))
    guarded = RobotBridge(replace(config, robots={"r1": access}), lambda _: robot)
    calls = []

    def setter(host, record, comment, timeout, **policy):
        assert policy == {"max_bytes": 16, "encoding": "ascii"}
        calls.append((host, record.identifier, comment))
        robot.files["md:NUMREG.VA"] = b"[1] = 42 'New count'\r\n"

    monkeypatch.setattr("fanuc_workbench.bridge.comments.set_live_comment", setter)
    try:
        plan = guarded.comments.prepare("r1", "md:NUMREG.VA", "R[1]", "New count", digest(robot.files["md:NUMREG.VA"]))
        assert not calls
        result = guarded.comments.commit(plan["plan_id"], plan["proposed_sha256"])
        assert result["status"] == "verified" and not result["controller_value_written"]
        assert calls == [("http://192.0.2.1", "R[1]", "New count")]
        assert Path(result["backup_path"]).read_bytes() == b"[1] = 42 'Count'\r\n"
        with pytest.raises(ValueError):
            guarded.comments.commit(plan["plan_id"], plan["proposed_sha256"])
    finally:
        guarded.close()


@pytest.mark.parametrize("change_before", [True, False])
def test_live_comment_stale_or_unconfirmed_changes_fail_closed(bridge_case, monkeypatch, change_before):
    bridge, robot, config = bridge_case
    access = replace(config.robots["r1"], comment_service_url="http://192.0.2.1", comment_targets=("R[1]",))
    guarded = RobotBridge(replace(config, robots={"r1": access}), lambda _: robot)
    calls = []
    monkeypatch.setattr("fanuc_workbench.bridge.comments.set_live_comment", lambda *a, **k: calls.append(True))
    try:
        plan = guarded.comments.prepare("r1", "md:NUMREG.VA", "R[1]", "New count", digest(robot.files["md:NUMREG.VA"]))
        if change_before:
            robot.files["md:NUMREG.VA"] = b"[1] = 99 'Other engineer'\r\n"
        with pytest.raises(RuntimeError, match="not_attempted" if change_before else "uncertain"):
            guarded.comments.commit(plan["plan_id"], plan["proposed_sha256"])
        assert len(calls) == (0 if change_before else 1)
    finally:
        guarded.close()


def test_documentation_package_and_pose_conversion(bridge_case):
    bridge, robot, config = bridge_case
    identifier = imported(bridge, robot, config)
    result = bridge.operations.make_package(identifier, "documentation")
    files = {Path(row["path"]).suffix for row in bridge.operations.store.get(result["artifact_id"])["files"]}
    assert files == {".html", ".xlsx", ".pdf"}
    pose = [10., 20., 30., 0., 0., 90.]
    assert bridge.operations.transform_pose(pose, [0.] * 6, [0.] * 6)["pose"] == pytest.approx(pose)
    with pytest.raises(ValueError):
        bridge.operations.transform_pose([float("nan")] * 6, [0.] * 6, [0.] * 6)


def test_connection_failures_close_ftp_and_sftp_clients(monkeypatch):
    from fanuc_workbench.backends.ftp import FanucFtpBackend
    from fanuc_workbench.backends.sftp import FanucSftpBackend

    class Failing:
        closed = False
        def connect(self, *a, **k): raise OSError("simulated connection failure")
        def close(self): self.closed = True
        def load_system_host_keys(self): pass
        def set_missing_host_key_policy(self, policy): pass

    ftp, sftp = Failing(), Failing()
    monkeypatch.setattr("fanuc_workbench.backends.ftp.FTP", lambda: ftp)
    import paramiko
    monkeypatch.setattr(paramiko, "SSHClient", lambda: sftp)
    for backend in (FanucFtpBackend(RobotProfile("r", "unused")), FanucSftpBackend(RobotProfile("r", "unused"))):
        with pytest.raises(OSError):
            backend.connect()
    assert ftp.closed and sftp.closed


@pytest.mark.parametrize("client_supports_confirmation", [False, True])
@pytest.mark.parametrize("commit_tool", ["commit_upload", "commit_change"])
def test_mcp_client_confirmation_cannot_be_replaced_by_tool_arguments(tmp_path, client_supports_confirmation, commit_tool):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.types import ElicitResult
    import os, sys

    edits = tmp_path / "edits"
    edits.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"state_dir": "state", "source_dir": "edits", "allow_writes": True,
                                  "approval_mode": "elicitation", "robots": {"r1": {
                                      "profile": {"host": "192.0.2.1"}, "writable_files": ["md:MAIN.LS"]}}}))
    program = tmp_path / "fake_server.py"
    program.write_text("""
from pathlib import Path
import sys
from fanuc_workbench.bridge.service import RobotBridge
from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.mcp_server import create_server
class Fake:
    data = b'/PROG MAIN\\r\\n/ATTR\\r\\nCOMMENT = "Original";\\r\\n/MN\\r\\n/END\\r\\n'
    def connect(self): pass
    def close(self): pass
    def download_bounded(self, remote, local, maximum): local.write_bytes(self.data)
    def upload(self, local, remote):
        self.data = local.read_bytes()
        Path(sys.argv[2]).write_text('uploaded')
backend = Fake()
bridge = RobotBridge(BridgeConfig.load(Path(sys.argv[1])), lambda _: backend)
try: create_server(bridge).run(transport='stdio')
finally: bridge.close()
""")
    uploaded = tmp_path / "upload_marker"

    async def scenario():
        decisions = iter([False, True])
        messages = []

        async def confirm(ctx, params):
            messages.append(params.message)
            return ElicitResult(action="accept", content={"approve": next(decisions)})

        params = StdioServerParameters(command=sys.executable, args=[str(program), str(config), str(uploaded)],
                                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                                            "PYTHONPATH": str(Path(__file__).resolve().parents[1])})
        callbacks = {"elicitation_callback": confirm} if client_supports_confirmation else {}
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer, **callbacks) as session:
            await session.initialize()
            import base64
            captured = await session.call_tool("read_file", {"robot_id": "r1", "path": "md:MAIN.LS"})
            assert not captured.is_error
            capture = json.loads(captured.content[0].text)
            exact = await session.call_tool("read_captured_file", {"robot_id": "r1", "path": "md:MAIN.LS",
                                                                  "sha256": capture['sha256']})
            assert not exact.is_error and base64.b64decode(json.loads(exact.content[0].text)['base64']) == ORIGINAL
            staged = await session.call_tool("stage_file", {"filename": "MAIN.LS",
                "data_base64": base64.b64encode(PROPOSED).decode('ascii'), "expected_sha256": digest(PROPOSED)})
            assert not staged.is_error
            source = json.loads(staged.content[0].text)['source_file']
            prepared = await session.call_tool("prepare_upload", {"robot_id": "r1", "path": "md:MAIN.LS",
                                                                   "source_file": source, "expected_sha256": digest(ORIGINAL)})
            assert not prepared.is_error
            plan = json.loads(prepared.content[0].text)
            args = {"plan_id": plan["plan_id"], "approved_sha256": plan["proposed_sha256"]}
            denied = await session.call_tool(commit_tool, args)
            assert denied.is_error and not uploaded.exists()
            if client_supports_confirmation:
                accepted = await session.call_tool(commit_tool, args)
                assert not accepted.is_error and uploaded.exists()
                assert all(plan["proposed_sha256"] in message and "md:MAIN.LS" in message for message in messages)

    asyncio.run(scenario())


def test_controller_comment_redirects_are_refused():
    from fanuc_workbench.services.live_comments import _NoWriteRedirect
    from urllib.request import Request
    from urllib.error import HTTPError
    with pytest.raises(HTTPError):
        _NoWriteRedirect().redirect_request(Request("http://192.0.2.1/karel/ComSet"), None,
                                           302, "Moved", {}, "http://192.0.2.2/karel/ComSet")


def test_robot_lock_is_enforced_in_another_process(bridge_case):
    import subprocess, sys
    bridge, _, config = bridge_case
    program = "from pathlib import Path; import sys; from fanuc_workbench.bridge.storage import RobotLocks; " \
              "locks=RobotLocks(Path(sys.argv[1])); lease=locks.acquire('192.0.2.1'); lease.__enter__(); lease.__exit__(None,None,None)"
    args = [sys.executable, "-B", "-c", program, str(config.state_dir / "robot-locks")]
    with bridge._robot_lock("r1"):
        result = subprocess.run(args, capture_output=True, text=True, timeout=10)
        assert result.returncode != 0 and "RobotBusyError" in result.stderr
    assert subprocess.run(args, capture_output=True, text=True, timeout=10).returncode == 0


def test_backup_rejects_directory_entries_outside_configured_roots(bridge_case):
    bridge, robot, _ = bridge_case
    robot.files["mc:SECRET.TXT"] = b"external root"
    fetched = []
    fetch = robot.download_bounded

    def track(remote, local, maximum):
        fetched.append(remote)
        fetch(remote, local, maximum)

    robot.download_bounded = track
    status = finished(bridge.operations.jobs, bridge.operations.start_backup("r1", attempt_ascii=False)["job_id"])
    assert status["status"] == "failed"
    assert "mc:SECRET.TXT" not in fetched


def test_active_job_owner_is_preserved_and_cross_client_cancellation_is_durable(tmp_path):
    first, other = JobManager(tmp_path / "jobs"), JobManager(tmp_path / "jobs")
    entered, release = threading.Event(), threading.Event()

    def work(context):
        entered.set()
        assert release.wait(5)
        context.check()
        return {"finished": True}

    try:
        job = first.start("read", "r1", work)
        assert entered.wait(5)
        assert other.status(job["job_id"])["status"] == "running"
        assert other.cancel(job["job_id"])["status"] == "cancel_requested"
        release.set()
        assert finished(first, job["job_id"])["status"] == "cancelled"
        assert other.cancel(job["job_id"])["status"] == "cancelled"
    finally:
        release.set()
        first.close()
        other.close()
