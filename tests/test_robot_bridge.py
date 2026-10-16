from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import pytest

from fanuc_workbench.backends.base import RobotBackend
from fanuc_workbench.bridge.config import BridgeConfig, RobotAccess, remote_path
from fanuc_workbench.bridge.service import RobotBridge
from fanuc_workbench.core.models import RemoteEntry, RobotProfile
from fanuc_workbench.services.controller import ControllerService
from fanuc_workbench.services.restore import RestoreCandidate
from fanuc_workbench.services.transfer import TransferLimitError, download_bounded


ORIGINAL = b'/PROG MAIN\r\n/ATTR\r\nCOMMENT = "Original";\r\n/MN\r\n/END\r\n'
PROPOSED = ORIGINAL.replace(b"Original", b"Reviewed")


def digest(data):
    return hashlib.sha256(data).hexdigest()


class MemoryRobot(RobotBackend):
    def __init__(self):
        self.files = {"md:MAIN.LS": ORIGINAL, "md:ERRALL.LS": b"2026/10/16 12:00:00 SRVO-001 Operator panel E-stop\r\n"}
        self.connects = self.closes = self.uploads = 0
        self.fail_connect = self.fail_upload = self.corrupt_upload = False

    def connect(self):
        self.connects += 1
        if self.fail_connect:
            raise OSError("connection detail containing secret-password")

    def close(self):
        self.closes += 1

    def list_dir(self, path):
        return [RemoteEntry(name=p.split(":")[-1], path=p, is_dir=False, size=len(v)) for p, v in self.files.items()]

    def download(self, remote, local):
        local.write_bytes(self.files[remote])

    def download_bounded(self, remote, local, maximum):
        data = self.files[remote]
        if len(data) > maximum:
            raise TransferLimitError("too large")
        local.write_bytes(data)

    def upload(self, local, remote):
        self.uploads += 1
        self.files[remote] = local.read_bytes()
        if self.fail_upload:
            raise OSError("connection interrupted containing secret-password")
        if self.corrupt_upload:
            self.files[remote] += b"changed"

    def file_exists(self, remote):
        return remote in self.files


@pytest.fixture
def setup(tmp_path):
    robot = MemoryRobot()
    source = tmp_path / "edits"
    source.mkdir()
    (source / "MAIN.LS").write_bytes(PROPOSED)
    profile = RobotProfile("Test robot", "192.0.2.1")
    config = BridgeConfig(tmp_path / "evidence", source,
                          {"r1": RobotAccess(profile, writable_files=("md:MAIN.LS", "md:SYSVARS.SV"))},
                          allow_writes=True)
    bridge = RobotBridge(config, lambda _: robot)
    return bridge, robot, config


def prepare(bridge):
    return bridge.prepare_upload("r1", "md:MAIN.LS", "MAIN.LS", digest(ORIGINAL))


@pytest.mark.parametrize("path", ["MAIN.LS", "md:../MAIN.LS", "md:/../x", "md:a//b", "md:a/./b",
                                  "md:MAIN.LS\r\nDELE x", "md:*", "md:%2e%2e", "md:a\\b", " md:x", "/a/../b"])
def test_remote_path_rejects_ambiguous_input(path):
    with pytest.raises(ValueError):
        remote_path(path)


def test_device_path_normalization():
    assert remote_path("MD:/MAIN.LS") == "md:MAIN.LS"
    assert remote_path("/md/MAIN.LS") == "/md/MAIN.LS"


def test_read_preserves_bytes_and_redacts_secrets(setup):
    bridge, robot, _ = setup
    result = bridge.read_file("r1", "md:MAIN.LS")
    assert Path(result["local_path"]).read_bytes() == ORIGINAL
    assert result["sha256"] == digest(ORIGINAL)
    assert "\r\n" in result["text"]
    assert robot.connects == robot.closes == 1
    robot.fail_connect = True
    with pytest.raises(RuntimeError) as error:
        bridge.read_file("r1", "md:MAIN.LS")
    assert "secret-password" not in str(error.value)
    assert robot.connects == robot.closes == 2


def test_scope_checks_precede_connections(setup):
    bridge, robot, _ = setup
    for robot_id, path in [("unknown", "md:MAIN.LS"), ("r1", "mc:MAIN.LS"), ("r1", "md:../x")]:
        with pytest.raises(ValueError):
            bridge.read_file(robot_id, path)
    assert robot.connects == 0


def test_read_only_and_exact_write_policy(setup):
    bridge, robot, config = setup
    readonly = RobotBridge(replace(config, allow_writes=False), lambda _: robot)
    with pytest.raises(ValueError, match="disabled"):
        prepare(readonly)
    with pytest.raises(ValueError, match="writable_files"):
        bridge.prepare_upload("r1", "md:OTHER.LS", "MAIN.LS", digest(ORIGINAL))
    with pytest.raises(ValueError, match="application"):
        bridge.prepare_upload("r1", "md:SYSVARS.SV", "MAIN.LS", digest(ORIGINAL))
    assert robot.connects == robot.uploads == 0


def test_source_path_cannot_escape_edit_directory(setup):
    bridge, robot, config = setup
    for source in [str(config.source_dir / "MAIN.LS"), "../MAIN.LS"]:
        with pytest.raises(ValueError, match="source_file"):
            bridge.prepare_upload("r1", "md:MAIN.LS", source, digest(ORIGINAL))
    assert robot.connects == 0


def test_listing_does_not_return_untrusted_outside_paths(setup):
    bridge, robot, _ = setup
    robot.files["mc:SECRET.TXT"] = b"not allowed"
    robot.files["md:../SECRET.TXT"] = b"not allowed"
    paths = [row["path"] for row in bridge.list_files("r1", "md:")["entries"]]
    assert paths == ["md:MAIN.LS", "md:ERRALL.LS"]


def test_logs_reuse_alarm_parser_and_report_missing_files(setup):
    bridge, robot, _ = setup
    result = bridge.collect_logs("r1", ["md:ERRALL.LS", "md:MISSING.DG"])
    assert result["alarms"][0]["code"] == "SRVO-001"
    assert result["alarms"][0]["line"] == 1
    assert result["unavailable"] == [{"remote_path": "md:MISSING.DG", "status": "unavailable"}]
    assert robot.uploads == 0


def test_prepared_then_verified_upload_preserves_backups_and_staged_bytes(setup):
    bridge, robot, config = setup
    plan = prepare(bridge)
    assert robot.uploads == 0 and plan["status"] == "prepared_only"
    assert Path(plan["backup_path"]).read_bytes() == ORIGINAL
    assert "Original" in plan["diff"] and "Reviewed" in plan["diff"]
    with pytest.raises(ValueError, match="Approval"):
        bridge.commit_upload(plan["plan_id"], "0" * 64)
    # Later edits to either local source or the on-disk preview cannot alter a plan.
    (config.source_dir / "MAIN.LS").write_bytes(b"unreviewed")
    Path(plan["proposed_path"]).write_bytes(b"unreviewed")
    result = bridge.commit_upload(plan["plan_id"], plan["proposed_sha256"])
    assert result["status"] == "verified"
    assert result["program_loaded_or_running"] == "not_verified"
    assert robot.files["md:MAIN.LS"] == PROPOSED
    assert Path(result["backup_path"]).read_bytes() == ORIGINAL
    assert Path(result["readback_path"]).read_bytes() == PROPOSED
    with pytest.raises(ValueError, match="consumed"):
        bridge.commit_upload(plan["plan_id"], plan["proposed_sha256"])
    audit = [json.loads(line) for line in (config.state_dir / "audit.jsonl").read_text().splitlines()]
    assert [row["action"] for row in audit][-2:] == ["upload_started", "upload_verified"]


def test_stale_file_rejected_at_prepare_and_commit(setup):
    bridge, robot, _ = setup
    with pytest.raises(ValueError, match="changed"):
        bridge.prepare_upload("r1", "md:MAIN.LS", "MAIN.LS", "0" * 64)
    plan = prepare(bridge)
    robot.files["md:MAIN.LS"] = b"another engineer's version"
    with pytest.raises(RuntimeError, match="not_attempted"):
        bridge.commit_upload(plan["plan_id"], plan["proposed_sha256"])
    assert robot.uploads == 0


@pytest.mark.parametrize("failure", ["fail_upload", "corrupt_upload"])
def test_uncertain_uploads_are_not_retried_or_rolled_back(setup, failure):
    bridge, robot, _ = setup
    plan = prepare(bridge)
    setattr(robot, failure, True)
    with pytest.raises(RuntimeError, match="uncertain") as error:
        bridge.commit_upload(plan["plan_id"], plan["proposed_sha256"])
    assert "secret-password" not in str(error.value)
    assert robot.uploads == 1
    assert Path(plan["backup_path"]).read_bytes() == ORIGINAL
    with pytest.raises(ValueError, match="consumed"):
        bridge.commit_upload(plan["plan_id"], plan["proposed_sha256"])
    assert robot.uploads == 1


def test_cancelled_expired_and_restarted_plans_cannot_commit(setup, monkeypatch):
    bridge, robot, config = setup
    plan = prepare(bridge)
    bridge.cancel_upload(plan["plan_id"])
    with pytest.raises(ValueError):
        bridge.commit_upload(plan["plan_id"], plan["proposed_sha256"])
    plan = prepare(bridge)
    restarted = RobotBridge(config, lambda _: robot)
    with pytest.raises(ValueError):
        restarted.commit_upload(plan["plan_id"], plan["proposed_sha256"])
    future = time.monotonic() + 3601
    monkeypatch.setattr("fanuc_workbench.bridge.service.time.monotonic", lambda: future)
    with pytest.raises(ValueError, match="expired"):
        bridge.commit_upload(plan["plan_id"], plan["proposed_sha256"])
    assert robot.uploads == 0


def test_transfer_limit_preserves_no_partial_output(setup):
    bridge, robot, config = setup
    limited = RobotBridge(replace(config, max_file_bytes=3), lambda _: robot)
    with pytest.raises(RuntimeError, match="max_file_bytes"):
        limited.read_file("r1", "md:MAIN.LS")
    assert not list(config.state_dir.rglob("original.ls"))


def test_controller_service_shared_operations_and_temp_cleanup(setup):
    _, robot, config = setup
    service = ControllerService(config.robots["r1"].profile, lambda _: robot)
    local = config.source_dir / "MAIN.LS"
    assert service.test_connection() == {"md:": 2}
    assert "Reviewed" in service.compare_file(local, "md:MAIN.LS")
    row = RestoreCandidate(local, "MAIN.LS", "Programs (ASCII)", "md:MAIN.LS", False)
    assert "Reviewed" in service.preview_restore([row])
    assert service.upload_file(local, "md:MAIN.LS") == "md:MAIN.LS"
    assert robot.files["md:MAIN.LS"] == PROPOSED
    assert robot.connects == robot.closes == 4


def test_config_uses_environment_secrets_and_sftp_defaults(tmp_path, monkeypatch):
    config_path = tmp_path / "bridge.json"
    data = {"state_dir": "evidence", "source_dir": "edits", "robots": {"r1": {
        "profile": {"host": "192.0.2.1", "transport": "sftp"}, "password_env": "FANUC_TEST_SECRET"}}}
    config_path.write_text(json.dumps(data))
    config = BridgeConfig.load(config_path)
    assert not config.allow_writes
    assert config.robots["r1"].profile.port == 22
    with pytest.raises(ValueError, match="not set"):
        config.robots["r1"].connection_profile()
    monkeypatch.setenv("FANUC_TEST_SECRET", "test-only-secret")
    assert config.robots["r1"].connection_profile().password == "test-only-secret"
    assert "test-only-secret" not in json.dumps(RobotBridge(config).list_robots())
    data["allow_writes"] = "false"
    config_path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="boolean"):
        BridgeConfig.load(config_path)


@pytest.mark.parametrize("transport", ["ftp", "sftp"])
def test_builtin_transports_enforce_size_during_transfer(tmp_path, transport):
    import io
    from fanuc_workbench.backends.ftp import FanucFtpBackend
    from fanuc_workbench.backends.sftp import FanucSftpBackend

    class FtpWire:
        def pwd(self): return "md:"
        def cwd(self, value): pass
        def retrbinary(self, command, receive):
            assert command == "RETR MAIN.LS"
            for chunk in (b"abc", b"def", b"ghi"):
                receive(chunk)

    class SftpWire:
        def open(self, remote, mode): return io.BytesIO(b"abcdefghi")

    if transport == "ftp":
        backend = FanucFtpBackend(RobotProfile("r", "unused"))
        backend.ftp = FtpWire()
    else:
        backend = FanucSftpBackend(RobotProfile("r", "unused"))
        backend.sftp = SftpWire()
    local = tmp_path / "capture.bin"
    with pytest.raises(TransferLimitError):
        download_bounded(backend, "md:MAIN.LS", local, 5)
    assert not local.exists()
    download_bounded(backend, "md:MAIN.LS", local, 9)
    assert local.read_bytes() == b"abcdefghi"


def test_audit_failure_before_upload_fails_closed(setup, monkeypatch):
    bridge, robot, _ = setup
    plan = prepare(bridge)
    audit = bridge._audit

    def fail_start(action, **fields):
        if action == "upload_started":
            raise OSError("disk full")
        return audit(action, **fields)

    monkeypatch.setattr(bridge, "_audit", fail_start)
    with pytest.raises(RuntimeError, match="not_attempted"):
        bridge.commit_upload(plan["plan_id"], plan["proposed_sha256"])
    assert robot.uploads == 0 and robot.files["md:MAIN.LS"] == ORIGINAL


def test_bounded_download_does_not_clobber_or_delete_existing_file(tmp_path):
    local = tmp_path / "existing.ls"
    local.write_bytes(b"preserve this local source")
    with pytest.raises(FileExistsError):
        download_bounded(MemoryRobot(), "md:MAIN.LS", local, 1024)
    assert local.read_bytes() == b"preserve this local source"


def test_shared_services_do_not_import_gui():
    result = subprocess.run([sys.executable, "-c",
                             "import sys; from fanuc_workbench.services.controller import ControllerService; "
                             "from fanuc_workbench.bridge.mcp_server import create_server; "
                             "assert not any(name.startswith('PySide6') for name in sys.modules)"],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_mcp_stdio_handshake_schemas_and_readonly_policy(tmp_path):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    path = tmp_path / "config.json"
    path.write_text(json.dumps({"state_dir": "evidence", "source_dir": "edits", "robots": {}}))

    async def scenario():
        params = StdioServerParameters(command=sys.executable,
                                       args=["-m", "fanuc_workbench.bridge.mcp_server", "--config", str(path)],
                                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            listed = await session.list_tools()
            tools = {tool.name: tool for tool in listed.tools}
            assert set(tools) == {"list_robots", "list_files", "read_file", "collect_logs",
                                  "prepare_upload", "commit_upload", "cancel_upload"}
            assert tools["read_file"].annotations.readOnlyHint
            assert tools["commit_upload"].annotations.destructiveHint
            assert tools["commit_upload"].annotations.idempotentHint is False
            result = await session.call_tool("list_robots", {})
            assert not result.isError
            assert json.loads(result.content[0].text) == {"allow_writes": False, "robots": []}
            denied = await session.call_tool("read_file", {"robot_id": "unknown", "path": "md:MAIN.LS"})
            assert denied.isError

    asyncio.run(scenario())
