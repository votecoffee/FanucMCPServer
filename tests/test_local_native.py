"""Source-bound local-native plans with inert toolchain files; no process/server."""
import asyncio
import hashlib
import json
import socket
import subprocess

import pytest

from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.service import RobotBridge
from fanuc_workbench.bridge.mcp_server import create_server
from fanuc_workbench.bridge import offline_profile
from fanuc_workbench.services.local_native import LocalNativeValidation, NativeToolPolicy, TOOLS, INSTRUCTIONS, capture_toolchain
from test_native_validation_offline import setup_tools


@pytest.fixture(autouse=True)
def no_process_or_controller(monkeypatch):
    def denied(*args, **kwargs): pytest.fail("Local-native unit fixture cannot launch or connect")
    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


@pytest.fixture
def local(tmp_path, monkeypatch):
    options = setup_tools(tmp_path)
    document = capture_toolchain(**options)
    manifest = tmp_path / "toolchain.json"; manifest.write_text(json.dumps(document, indent=2))
    policy = tmp_path / "native-tools.json"
    policy.write_text(json.dumps({"schema_version": 1, "toolchains": {"fixture": {
        "manifest": "toolchain.json", "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()}}}))
    config = BridgeConfig(tmp_path / "state", tmp_path / "inputs")
    with RobotBridge(config, lambda *a: pytest.fail("No robot backend")) as bridge:
        source = config.source_dir / "synthetic"; source.mkdir(parents=True)
        raw = b'/PROG MAIN\n/ATTR\n/MN\n   1: ! SYNTHETIC ONLY ;\n/POS\n/END\n'
        (source / "MAIN.LS").write_bytes(raw)
        artifact = bridge.operations.import_workspace("synthetic")["artifact_id"]
        service = LocalNativeValidation(bridge, NativeToolPolicy(policy))
        yield service, artifact, hashlib.sha256(raw).hexdigest(), options, tmp_path


def prepared(local):
    service, artifact, checksum, *_ = local
    return service.prepare(artifact, "MAIN.LS", "fixture", checksum)


def fake_execute(plan, folder):
    target = folder / "validation"; target.mkdir()
    pins = {row["role"]: row["sha256"] for row in plan["toolchain"]["files"]}
    report = {"status": "synthetic_fixture_only", "source_sha256": plan["source_sha256"],
        "compiler_sha256": pins["maketp"], "printer_sha256": pins["printtp"],
        "original_configuration_sha256": pins["configuration"],
        "context_manifest_sha256": hashlib.sha256(json.dumps(plan["toolchain"]["context_manifest"], sort_keys=True).encode()).hexdigest(),
        "toolchain": {"components": [row for row in plan["toolchain"]["files"] if row["role"] == "component"]},
        "core_version": plan["toolchain"]["options"]["version"], "deployment_prepared": False,
        "controller_contacted": False, "controller_written": False}
    (target / "validation.json").write_text(json.dumps(report))
    return report


def test_prepare_review_run_receipt_and_restart_do_not_replay(local, monkeypatch):
    service, *_ = local; plan = prepared(local)
    assert service.review_plan_count == 1 and not plan["deployment_authorized"]
    assert not (service.root / plan["plan_id"] / "validation").exists()
    monkeypatch.setattr(service, "_execute", fake_execute)
    with pytest.raises(ValueError, match="SHA-256"):
        service.run(plan["plan_id"], "0" * 64)
    result = service.run(plan["plan_id"], plan["plan_sha256"])
    assert result["status"] == "completed" and service.review_plan_count == 0
    assert result["validation"]["status"] == "synthetic_fixture_only"
    assert service.bridge.operations.store.get(result["artifact_id"])["kind"] == "native-validation"
    assert not service.status(plan["plan_id"])["runnable"]
    restarted = LocalNativeValidation(service.bridge, service.policy)
    assert restarted.status(plan["plan_id"])["status"] == "completed"
    for instance in (service, restarted):
        with pytest.raises(ValueError, match="consumed"):
            instance.run(plan["plan_id"], plan["plan_sha256"])


@pytest.mark.parametrize("target", ["maketp", "printtp", "configuration", "profile", "context", "context-added", "manifest", "policy", "source", "plan"])
def test_pinned_inputs_refuse_drift_before_native_dispatch(local, monkeypatch, target):
    service, _, _, options, root = local; plan = prepared(local)
    if target in {"maketp", "printtp", "configuration"}: path = options[target]
    elif target == "profile": path = root / "profile/asc2bint.dll"
    elif target == "context": path = root / "source_context/support/config.dat"
    elif target == "context-added": path = root / "source_context/added.txt"
    elif target == "manifest": path = root / "toolchain.json"
    elif target == "policy": path = root / "native-tools.json"
    elif target == "source": path = service.root / plan["plan_id"] / "source.LS"
    else: path = service.root / plan["plan_id"] / "plan.json"
    path.write_bytes(b"changed bytes")
    monkeypatch.setattr(service, "_execute", lambda *a: pytest.fail("Changed inputs cannot execute"))
    with pytest.raises((ValueError, json.JSONDecodeError)):
        service.run(plan["plan_id"], plan["plan_sha256"])
    assert service.status(plan["plan_id"])["status"] == "refused"
    with pytest.raises(ValueError, match="consumed"):
        service.run(plan["plan_id"], plan["plan_sha256"])


def test_unknown_native_outcome_is_preserved_and_not_automatically_retried(local, monkeypatch):
    service, *_ = local; plan = prepared(local); attempts = []
    def failure(*args): attempts.append(True); raise TimeoutError("synthetic lost response")
    monkeypatch.setattr(service, "_execute", failure)
    with pytest.raises(TimeoutError): service.run(plan["plan_id"], plan["plan_sha256"])
    status = service.status(plan["plan_id"])
    assert status["status"] == "uncertain" and status["never_replay"] and not status["runnable"]
    with pytest.raises(ValueError, match="consumed"): service.run(plan["plan_id"], plan["plan_sha256"])
    assert attempts == [True]


def test_lost_owner_dispatch_receipt_is_unverified_without_process_adoption(local):
    service, *_ = local; plan = prepared(local)
    (service.root / plan["plan_id"] / "receipt.json").write_text(json.dumps({"status": "dispatching"}))
    restarted = LocalNativeValidation(service.bridge, service.policy)
    status = restarted.status(plan["plan_id"])
    assert status["status"] == "outcome_unverified" and status["never_replay"] and not status["runnable"]


def test_native_result_hash_mismatch_stays_uncertain(local, monkeypatch):
    service, *_ = local; plan = prepared(local)
    def wrong(*args): result = fake_execute(*args); result["source_sha256"] = "0" * 64; return result
    monkeypatch.setattr(service, "_execute", wrong)
    with pytest.raises(ValueError, match="reviewed"):
        service.run(plan["plan_id"], plan["plan_sha256"])
    assert service.status(plan["plan_id"])["status"] == "uncertain"


def test_native_profile_adds_only_local_compilation_and_keeps_installed_offline_contract(local):
    service, *_ = local
    async def check():
        server = create_server(service.bridge, local_native=service)
        assert server.instructions == INSTRUCTIONS
        assert "can compile saved LS" in server.instructions
        assert "exposes no controller, native utility" not in server.instructions
        names = {row.name for row in await server.list_tools()}
        assert names == offline_profile.TOOLS | TOOLS and len(names) == 20
        assert {row.name for row in await create_server(service.bridge, offline=True).list_tools()} == offline_profile.TOOLS
        for name in ("read_file", "probe_robot", "run_read_operation", "commit_upload", "reload_extensions"):
            assert server._tool_manager.get_tool(name) is None
        result = await server.call_tool("capabilities", {})
        caps = json.loads(result.content[0].text)
        assert caps["profile"] == "standalone-local-native" and caps["native_utilities"]
        assert caps["configured_robots"] == [] and not caps["controller_access"] and not caps["shared_host"]
        assert caps["local_native"]["maximum_jobs"] == 1
        assert server.job_tasks is server.artifact_resources is None
        result = await server.call_tool("list_native_toolchains", {})
        assert json.loads(result.content[0].text)["toolchains"][0]["toolchain_id"] == "fixture"
    asyncio.run(check())
    with pytest.raises(ValueError, match="one explicit"):
        create_server(service.bridge, offline=True, local_native=service)
    with pytest.raises(ValueError, match="Protocol preview"):
        create_server(service.bridge, local_native=service, protocol_preview=True)


def test_native_policy_cannot_turn_on_robots_or_controller_writes(tmp_path):
    with RobotBridge(BridgeConfig(tmp_path / "state", tmp_path / "input", allow_writes=True)) as bridge:
        with pytest.raises(ValueError, match="zero robots"):
            LocalNativeValidation(bridge, None)


@pytest.mark.parametrize("extra", ["environment", "arguments", "enable_controller"])
def test_native_policy_rejects_generic_execution_options(tmp_path, extra):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"schema_version": 1, "toolchains": {}, extra: {}}))
    with pytest.raises(ValueError, match="tool policy"):
        NativeToolPolicy(path)


def test_discard_unused_plan_preserves_files_and_cannot_run_it(local):
    service, *_ = local; plan = prepared(local)
    result = service.discard(plan["plan_id"], plan["plan_sha256"])
    assert result["status"] == "discarded" and service.review_plan_count == 0
    assert (service.root / plan["plan_id"] / "source.LS").is_file()
    with pytest.raises(ValueError, match="consumed"): service.run(plan["plan_id"], plan["plan_sha256"])


def test_busy_pipeline_keeps_the_second_plan_prepared(local, monkeypatch):
    service, *_ = local; first = prepared(local); second = prepared(local)
    def execute(plan, folder):
        with pytest.raises(RuntimeError, match="busy"):
            service.run(second["plan_id"], second["plan_sha256"])
        assert service.status(second["plan_id"])["runnable"]
        return fake_execute(plan, folder)
    monkeypatch.setattr(service, "_execute", execute)
    service.run(first["plan_id"], first["plan_sha256"])
    assert service.active_plan is None and service.review_plan_count == 1


def test_native_cli_refuses_broad_robot_policy_before_state_creation(tmp_path, monkeypatch):
    import sys
    from fanuc_workbench.bridge import mcp_server
    selected = tmp_path / "native.json"
    selected.write_text(json.dumps({"schema_version": 1, "toolchains": {"fixture": {
        "manifest": "unopened.json", "sha256": "a" * 64}}}))
    bridge = tmp_path / "bridge.json"
    bridge.write_text(json.dumps({"state_dir": "never-created", "source_dir": "inputs", "robots": {}, "allow_writes": True}))
    monkeypatch.setattr(sys, "argv", ["server", "--config", str(bridge), "--local-native-policy", str(selected)])
    monkeypatch.setattr(mcp_server, "create_server", lambda *a, **k: pytest.fail("No server startup"))
    with pytest.raises(ValueError, match="zero robots"): mcp_server.main()
    assert not (tmp_path / "never-created").exists()


def test_partial_diagnostics_are_readable_only_after_verified_private_cleanup(local, monkeypatch):
    service, *_ = local; plan = prepared(local)
    def failure(plan, folder):
        logs = folder / "validation/native-tool-fixture"; logs.mkdir(parents=True)
        (logs / "receipt.json").write_text(json.dumps({"state": "uncertain", "cleanup": {"outcome": "terminated"}}))
        (logs / "failure.json").write_text(json.dumps({"error_type": "SyntheticError", "traceback": "inert private diagnostic"}))
        raise RuntimeError("synthetic worker failure")
    monkeypatch.setattr(service, "_execute", failure)
    with pytest.raises(RuntimeError): service.run(plan["plan_id"], plan["plan_sha256"])
    receipt = service.status(plan["plan_id"])
    assert receipt["status"] == "uncertain" and receipt["partial_evidence_available"]
    artifact = service.bridge.operations.store.get(receipt["partial_artifact_id"])
    assert artifact["kind"] == "native-validation-partial"
    assert any(row["path"].endswith("failure.json") for row in artifact["files"])


def test_uncertain_cleanup_does_not_claim_an_immutable_capture(local, monkeypatch):
    service, *_ = local; plan = prepared(local)
    def failure(plan, folder):
        logs = folder / "validation/native-tool-fixture"; logs.mkdir(parents=True)
        (logs / "receipt.json").write_text(json.dumps({"cleanup": {"outcome": "uncertain"}}))
        raise RuntimeError("synthetic cleanup uncertainty")
    monkeypatch.setattr(service, "_execute", failure)
    with pytest.raises(RuntimeError): service.run(plan["plan_id"], plan["plan_sha256"])
    receipt = service.status(plan["plan_id"])
    assert receipt["status"] == "uncertain" and receipt["capture_unverified"]
    assert not receipt["partial_evidence_available"]


def test_expired_plan_is_consumed_without_native_execution(local, monkeypatch):
    service, *_ = local; plan = prepared(local)
    payload, checksum, _ = service.plans[plan["plan_id"]]
    service.plans[plan["plan_id"]] = payload, checksum, 0
    monkeypatch.setattr(service, "_execute", lambda *a: pytest.fail("Expired plan cannot execute"))
    with pytest.raises(ValueError, match="expired"):
        service.run(plan["plan_id"], plan["plan_sha256"])
    assert service.status(plan["plan_id"])["status"] == "refused" and service.active_plan is None


def test_native_and_offline_cli_profiles_are_mutually_exclusive(tmp_path, monkeypatch):
    import sys
    from fanuc_workbench.bridge import mcp_server
    monkeypatch.setattr(sys, "argv", ["server", "--offline", "--local-native-policy", str(tmp_path / "missing.json")])
    with pytest.raises(SystemExit) as exc: mcp_server.main()
    assert exc.value.code == 2


def test_context_change_during_copy_is_refused_before_any_compiler(local, monkeypatch):
    from types import SimpleNamespace
    from fanuc_workbench.bridge import resource_context
    from fanuc_workbench.services import native_validation
    service, _, _, options, root = local; plan = prepared(local)
    monkeypatch.setattr(resource_context, "resource_context", lambda: SimpleNamespace(
        ledger=SimpleNamespace(directory=root / "fixture-registry/resources")))
    snapshot = native_validation._snapshot_context
    def changed(source, destination):
        (source / "support/config.dat").write_bytes(b"changed after initial preflight")
        return snapshot(source, destination)
    monkeypatch.setattr(native_validation, "_snapshot_context", changed)
    with pytest.raises(ValueError, match="Copied native inputs"):
        service.run(plan["plan_id"], plan["plan_sha256"])
    report = json.loads((service.root / plan["plan_id"] / "validation/validation.json").read_text())
    assert not report["compiler_invoked"] and not report["printer_invoked"]
    assert report["status"] == "validation_error"


@pytest.mark.parametrize("field,value", [("original_configuration_sha256", "0" * 64),
    ("context_manifest_sha256", "0" * 64), ("core_version", "V0.00-0"), ("deployment_prepared", True)])
def test_result_must_match_all_reviewed_native_inputs(local, monkeypatch, field, value):
    service, *_ = local; plan = prepared(local)
    def wrong(*args): result = fake_execute(*args); result[field] = value; return result
    monkeypatch.setattr(service, "_execute", wrong)
    with pytest.raises(ValueError, match="reviewed"):
        service.run(plan["plan_id"], plan["plan_sha256"])
    assert service.status(plan["plan_id"])["status"] == "uncertain"


@pytest.mark.parametrize("target", ["component", "context-added", "policy"])
def test_inputs_changed_during_native_work_are_not_attested_preserved(local, monkeypatch, target):
    service, _, _, options, root = local; plan = prepared(local)
    def changed(*args):
        report = fake_execute(*args)
        path = options["components"][0] if target == "component" else root / ("source_context/new.txt" if target == "context-added" else "native-tools.json")
        path.write_bytes(b"synthetic change during work")
        return report
    monkeypatch.setattr(service, "_execute", changed)
    with pytest.raises(ValueError): service.run(plan["plan_id"], plan["plan_sha256"])
    assert service.status(plan["plan_id"])["status"] == "uncertain"


def test_bridge_does_not_attest_clean_close_with_unverified_utility(tmp_path, monkeypatch):
    from fanuc_workbench.bridge import owned_workers
    inventory = owned_workers.WorkerInventory(limit=1)
    identifier = inventory.reserve(object(), "native-tool-worker")
    inventory.update(identifier, "uncertain")
    monkeypatch.setattr(owned_workers, "workers", inventory)
    bridge = RobotBridge(BridgeConfig(tmp_path / "state", tmp_path / "inputs"))
    with pytest.raises(RuntimeError, match="cleanup is incomplete"):
        bridge.close()
    assert not bridge._closed and inventory.snapshot()["uncertain"] == 1
    inventory.release(identifier)  # Inert bookkeeping fixture; no actual resource is adopted or terminated.
    bridge.close()
    assert bridge._closed
