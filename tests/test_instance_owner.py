"""Lease/drain ordering with inert objects; no MCP process or controller starts."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from fanuc_workbench.bridge import instance_owner


def policy(tmp_path, name="policy.json", state="state"):
    path = tmp_path / name
    path.write_text(json.dumps({"state_dir": state, "source_dir": "edits", "robots": {}}))
    return path


def test_owner_refuses_second_worker_before_factory_and_releases_after_drain(tmp_path):
    config = policy(tmp_path)
    alias = policy(tmp_path, "another-policy.json")
    opened, draining, release = [], threading.Event(), threading.Event()

    class Inert:
        def __init__(self, config, **kwargs):
            opened.append(config.state_dir)

        def close(self):
            draining.set()
            assert release.wait(5)

    def hold():
        with instance_owner.owned_mcp_bridge(config, factory=Inert):
            pass

    with ThreadPoolExecutor(max_workers=1) as executor:
        owner = executor.submit(hold)
        try:
            assert draining.wait(5)
            with pytest.raises(SystemExit, match="already has an MCP worker"):
                with instance_owner.owned_mcp_bridge(alias, factory=Inert):
                    pytest.fail("A second owner was admitted")
            assert len(opened) == 1
        finally:
            release.set()
        owner.result(timeout=5)
    with instance_owner.owned_mcp_bridge(alias, factory=Inert):
        pass
    assert len(opened) == 2


def test_failed_start_releases_without_constructing_a_second_worker(tmp_path):
    config = policy(tmp_path)
    def fail(*args, **kwargs):
        raise ValueError("inert construction failure")
    for _ in range(2):
        with pytest.raises(ValueError, match="construction failure"):
            with instance_owner.owned_mcp_bridge(config, factory=fail):
                pass


def test_failed_drain_keeps_lease_until_process_exit(tmp_path):
    config = policy(tmp_path)
    class Inert:
        def __init__(self, *args, **kwargs):
            pass
        def close(self):
            raise ValueError("inert drain failure")
    retained = len(instance_owner._uncertain_leases)
    try:
        with pytest.raises(ValueError, match="drain failure"):
            with instance_owner.owned_mcp_bridge(config, factory=Inert):
                pass
        assert len(instance_owner._uncertain_leases) == retained + 1
        with pytest.raises(SystemExit, match="already has an MCP worker"):
            with instance_owner.owned_mcp_bridge(config, factory=Inert):
                pytest.fail("Uncertain native drain admitted another owner")
    finally:
        # This test has only inert objects. Never clear real ownership/evidence.
        while len(instance_owner._uncertain_leases) > retained:
            instance_owner._uncertain_leases.pop().__exit__(None, None, None)
