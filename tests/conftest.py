"""Every test worker stays outside the real user's management registry."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def private_management_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("FANUC_MCP_MANAGEMENT_REGISTRY", str(tmp_path / "management-registry"))
    monkeypatch.setenv("FANUC_MCP_INSTANCE_ROLE", "validation")
