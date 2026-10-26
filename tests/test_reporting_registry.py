"""Real Windows directory permissions and peer-preserving status publication."""
from concurrent.futures import ThreadPoolExecutor
import os

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows reporting directory contract")


def test_prepared_registry_never_rewrites_populated_peer_acls(tmp_path, monkeypatch):
    import win32security
    from fanuc_workbench.bridge import reporting_registry as module
    from universal_tools.management import ManagedStatus, Registration
    directory = tmp_path / "registry"
    owner = module.ReportingRegistry(directory)
    owner.prepare()
    peer = directory / "instances/retained-peer.json"
    peer.write_bytes(b'preserve exact peer bytes')
    before = bytes(win32security.GetFileSecurity(str(peer), win32security.DACL_SECURITY_INFORMATION))
    owner.close()
    def forbidden(*_):
        pytest.fail("Existing directory permissions must be verified without rewriting")
    monkeypatch.setattr(win32security, "SetNamedSecurityInfo", forbidden)
    monkeypatch.setattr(win32security, "SetSecurityInfo", forbidden)
    def publish(index):
        registry = module.ReportingRegistry(directory)
        try:
            registry.prepare()
            row = Registration(app_id="fanuc-test", display_name="Synthetic", instance_id=f"{index:032x}",
                pid=os.getpid(), started_at=1, endpoint="http://127.0.0.1:1/v1/management",
                token="synthetic-test-token-for-local-fixture", last_status=ManagedStatus())
            registry.publish(row)
            registry.prepare()
        finally:
            registry.close()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(publish, range(8)))
    assert peer.read_bytes() == b'preserve exact peer bytes'
    assert bytes(win32security.GetFileSecurity(str(peer), win32security.DACL_SECURITY_INFORMATION)) == before


def test_unexpected_permissions_refuse_without_repair(tmp_path):
    import ntsecuritycon
    import win32security
    from fanuc_workbench.bridge.reporting_registry import ReportingRegistry
    directory = tmp_path / "registry"
    first = ReportingRegistry(directory)
    first.prepare(); first.close()
    path = directory / "instances"
    descriptor = win32security.GetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION)
    acl = descriptor.GetSecurityDescriptorDacl()
    acl.AddAccessAllowedAce(win32security.ACL_REVISION, ntsecuritycon.FILE_GENERIC_READ,
                           win32security.CreateWellKnownSid(win32security.WinWorldSid))
    win32security.SetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, acl, None)
    before = bytes(win32security.GetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION))
    registry = ReportingRegistry(directory)
    try:
        with pytest.raises(ValueError, match="permissions"):
            registry.prepare()
    finally:
        registry.close()
    assert bytes(win32security.GetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION)) == before


def test_wrong_owner_refuses_even_when_dacl_is_correct(tmp_path, monkeypatch):
    import win32security
    from fanuc_workbench.bridge.reporting_registry import ReportingRegistry
    directory = tmp_path / "registry"
    first = ReportingRegistry(directory)
    first.prepare(); first.close()
    real = win32security.GetSecurityInfo
    def wrong_owner(*args):
        descriptor = real(*args)
        # Pure descriptor mutation: never change a filesystem owner or privileges.
        descriptor.SetSecurityDescriptorOwner(win32security.CreateWellKnownSid(win32security.WinWorldSid), False)
        return descriptor
    monkeypatch.setattr(win32security, "GetSecurityInfo", wrong_owner)
    registry = ReportingRegistry(directory)
    try:
        with pytest.raises(ValueError, match="owner"):
            registry.prepare()
    finally:
        registry.close()


def test_junction_in_declared_path_is_refused_without_touching_target(tmp_path):
    import subprocess
    from fanuc_workbench.bridge.reporting_registry import ReportingRegistry
    target = tmp_path / "target"
    first = ReportingRegistry(target)
    first.prepare(); first.close()
    peer = target / "instances/peer.txt"
    peer.write_bytes(b'preserve')
    link = tmp_path / "junction"
    result = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    assert result.returncode == 0, result.stderr
    registry = ReportingRegistry(link)
    try:
        with pytest.raises(ValueError, match="ordinary"):
            registry.prepare()
        assert peer.read_bytes() == b'preserve'
    finally:
        registry.close()
        link.rmdir()  # Remove this empty junction only; preserve its target.


def test_reporting_directory_identity_is_held_until_close(tmp_path):
    from fanuc_workbench.bridge.reporting_registry import ReportingRegistry
    directory = tmp_path / "registry"
    registry = ReportingRegistry(directory)
    registry.prepare()
    with pytest.raises(OSError):
        directory.rename(tmp_path / "renamed")
    registry.close()
    directory.rename(tmp_path / "renamed")


def test_open_identity_drift_is_refused(tmp_path, monkeypatch):
    import win32file
    from fanuc_workbench.bridge.reporting_registry import ReportingRegistry
    registry = ReportingRegistry(tmp_path / "registry")
    registry.prepare()
    real = win32file.GetFileInformationByHandle
    def drift(handle):
        observed = list(real(handle))
        observed[9] ^= 1  # Inject a different full file index, never change the filesystem.
        return tuple(observed)
    monkeypatch.setattr(win32file, "GetFileInformationByHandle", drift)
    try:
        with pytest.raises(ValueError, match="identity"):
            registry.prepare()
    finally:
        registry.close()


def test_offline_reporter_uses_verified_registry_and_stops_registration(tmp_path):
    from fanuc_workbench.bridge.management import ServiceReporting
    from fanuc_workbench.bridge.reporting_registry import ReportingRegistry
    from universal_tools.management import ManagementClient
    class Bridge:
        offline_metadata = True
        def management_activity(self):
            return {"active_operations": 0, "review_plans": 0,
                    "owned_resources": {"active": 0, "limit": 0, "uncertain": 0}}
    directory = tmp_path / "registry"
    reporting = ServiceReporting(Bridge(), registry=directory).start()
    try:
        assert isinstance(reporting.agent.registry, ReportingRegistry)
        assert reporting.status().state == "running"
        assert not reporting.status().restart_allowed
    finally:
        reporting.close()
    assert not reporting.agent.registry._handles
    assert ManagementClient(directory).snapshot()["services"][0]["status"]["state"] == "stopped"


def test_permissions_are_rechecked_after_successful_prepare(tmp_path):
    import ntsecuritycon
    import win32security
    from fanuc_workbench.bridge.reporting_registry import ReportingRegistry
    directory = tmp_path / "registry"
    registry = ReportingRegistry(directory)
    registry.prepare()
    descriptor = win32security.GetFileSecurity(str(directory), win32security.DACL_SECURITY_INFORMATION)
    acl = descriptor.GetSecurityDescriptorDacl()
    acl.AddAccessAllowedAce(win32security.ACL_REVISION, ntsecuritycon.FILE_GENERIC_READ,
                           win32security.CreateWellKnownSid(win32security.WinWorldSid))
    win32security.SetNamedSecurityInfo(str(directory), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, acl, None)
    try:
        with pytest.raises(ValueError, match="permissions"):
            registry.prepare()
    finally:
        registry.close()


def test_unexpected_reporting_permissions_are_visible_and_do_not_block_service(tmp_path, capsys):
    import win32security
    from fanuc_workbench.bridge.management import report_service
    # An ordinary, inherited directory is not a prepared private registry.
    directory = tmp_path / "registry"
    directory.mkdir()
    before = bytes(win32security.GetFileSecurity(str(directory), win32security.DACL_SECURITY_INFORMATION))
    class Bridge:
        offline_metadata = True
        def management_activity(self):
            return {"active_operations": 0, "review_plans": 0,
                    "owned_resources": {"active": 0, "limit": 0, "uncertain": 0}}
    with report_service(Bridge(), registry=directory) as reporting:
        assert reporting is None
        reached_body = True
    assert reached_body and "tray reporting unavailable" in capsys.readouterr().err
    assert bytes(win32security.GetFileSecurity(str(directory), win32security.DACL_SECURITY_INFORMATION)) == before


def test_created_directory_cannot_be_replaced_before_first_verification(tmp_path, monkeypatch):
    import win32security
    from fanuc_workbench.bridge import reporting_registry as module
    directory = tmp_path / "created"
    registry = module.ReportingRegistry(directory)
    opened = module.open_private_directory
    observed = []
    def at_native_return(parent, name):
        handle, created = opened(parent, name)
        try:
            if created:
                # The kernel returned an already-private, non-delete-shared
                # handle. This boundary precedes the registry's first verify.
                registry._verify(handle)
                identity = registry._identity(handle)
                path = directory if name == directory.name else directory / name
                with pytest.raises(OSError):
                    path.rename(path.with_name(path.name + "-displaced"))
                assert registry._identity(handle) == identity
                observed.append((name, created))
            return handle, created
        except BaseException:
            handle.Close()
            raise
    monkeypatch.setattr(module, "open_private_directory", at_native_return)
    def forbidden(*_):
        pytest.fail("Creation must never use a path or handle ACL rewrite")
    monkeypatch.setattr(win32security, "SetNamedSecurityInfo", forbidden)
    monkeypatch.setattr(win32security, "SetSecurityInfo", forbidden)
    try:
        registry.prepare()
        assert observed == [(directory.name, True), ("instances", True)]
    finally:
        registry.close()


def test_empty_parent_cannot_be_replaced_before_child_creation(tmp_path, monkeypatch):
    from fanuc_workbench.bridge import reporting_registry as module
    parent = tmp_path / "parent"
    parent.mkdir()
    registry = module.ReportingRegistry(parent / "registry")
    opened = module.open_private_directory
    attempts = []
    def at_native_entry(handle, name):
        if name == registry.directory.name:
            with pytest.raises(OSError):
                parent.rename(tmp_path / "displaced-parent")
            attempts.append(name)
        return opened(handle, name)
    monkeypatch.setattr(module, "open_private_directory", at_native_entry)
    try:
        registry.prepare()
        assert attempts == [registry.directory.name]
    finally:
        registry.close()


@pytest.mark.parametrize("boundary", ["root", "instances"])
def test_racing_foreign_directory_keeps_its_acl_and_is_refused(tmp_path, monkeypatch, boundary):
    import ntsecuritycon
    import win32security
    from fanuc_workbench.bridge import reporting_registry as module
    directory = tmp_path / "registry"
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    acl = win32security.ACL()
    acl.AddAccessAllowedAceEx(win32security.ACL_REVISION_DS, 3, ntsecuritycon.FILE_ALL_ACCESS,
                             win32security.CreateWellKnownSid(win32security.WinWorldSid))
    win32security.SetNamedSecurityInfo(str(foreign), win32security.SE_FILE_OBJECT,
        win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, acl, None)
    peer = foreign / "retained-peer.txt"
    peer.write_bytes(b'foreign source must remain unchanged')
    before = bytes(win32security.GetFileSecurity(str(foreign), win32security.DACL_SECURITY_INFORMATION))
    opened = module.open_private_directory
    event = []
    def replace_before_kernel_open(parent, name):
        target = directory if boundary == "root" else directory / "instances"
        if not event and name == target.name:
            assert not target.exists()
            foreign.rename(target)
            event.append(target)
        return opened(parent, name)
    monkeypatch.setattr(module, "open_private_directory", replace_before_kernel_open)
    def forbidden(*_):
        pytest.fail("A racing existing directory's ACL must never be repaired")
    monkeypatch.setattr(win32security, "SetNamedSecurityInfo", forbidden)
    monkeypatch.setattr(win32security, "SetSecurityInfo", forbidden)
    registry = module.ReportingRegistry(directory)
    try:
        with pytest.raises(ValueError, match="permissions"):
            registry.prepare()
        assert len(event) == 1
        assert bytes(win32security.GetFileSecurity(str(event[0]), win32security.DACL_SECURITY_INFORMATION)) == before
        assert (event[0] / peer.name).read_bytes() == b'foreign source must remain unchanged'
    finally:
        registry.close()


def test_eight_first_use_creators_share_one_private_directory_identity(tmp_path, monkeypatch):
    import threading
    import win32security
    from fanuc_workbench.bridge import reporting_registry as module
    directory = tmp_path / "café 工具 🚀"
    opened = module.open_private_directory
    barrier = threading.Barrier(8)
    guard = threading.Lock()
    results = []
    def observe(parent, name):
        if name == directory.name:
            barrier.wait(10)
        handle, created = opened(parent, name)
        with guard:
            results.append((name, created, module.ReportingRegistry._identity(handle)))
        return handle, created
    monkeypatch.setattr(module, "open_private_directory", observe)
    def forbidden(*_):
        pytest.fail("Concurrent first use must not rewrite directory ACLs")
    monkeypatch.setattr(win32security, "SetNamedSecurityInfo", forbidden)
    monkeypatch.setattr(win32security, "SetSecurityInfo", forbidden)
    registries = [module.ReportingRegistry(directory) for _ in range(8)]
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda registry: registry.prepare(), registries))
        for name in (directory.name, "instances"):
            group = [row for row in results if row[0] == name]
            assert len(group) == 8 and sum(row[1] for row in group) == 1
            assert len({row[2] for row in group}) == 1
    finally:
        for registry in registries:
            registry.close()


@pytest.mark.parametrize("name", ["..", "parent/child", "dir\\child", "dir:stream", "registry.", "registry ", "NUL", "COM¹.txt"])
def test_native_names_cannot_alias_a_different_win32_publication_path(name):
    from fanuc_workbench.bridge.reporting_directory import open_private_directory
    with pytest.raises(ValueError, match="ordinary"):
        open_private_directory(0, name)  # Refuses the name before using a kernel handle.
