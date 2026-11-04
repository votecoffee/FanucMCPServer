"""Full evidence analysis with bounded response serialization."""
from pathlib import Path

import pytest

from fanuc_workbench.core.models import PositionRecord
from fanuc_workbench.services import workspace_api as api


def test_unfiltered_positions_serializes_only_requested_page(monkeypatch):
    rows = [PositionRecord("SYNTHETIC", index, "1", values={"X": float(index)})
            for index in range(8000)]
    monkeypatch.setattr(api.workspace, "scan_positions", lambda root: rows)
    converted = []
    original = api.asdict

    def counted(row):
        converted.append(row.index)
        return original(row)

    monkeypatch.setattr(api, "asdict", counted)
    result = api.WorkspaceService.analyze(Path("evidence"), "positions", offset=7995, limit=5)
    assert result == {"analysis": "positions", "items": [original(row) for row in rows[7995:]],
                      "total": 8000, "offset": 7995, "next_offset": None, "evidence_only": True}
    assert converted == list(range(7995, 8000))


def test_filter_preserves_serialized_matching_totals_and_cursor(monkeypatch):
    rows = [PositionRecord("MATCH" if index % 2 else "OTHER", index, "1") for index in range(20)]
    monkeypatch.setattr(api.workspace, "scan_positions", lambda root: rows)
    # Match the public dictionary representation, including its quotes and key.
    query = "'PROGRAM': 'MATCH'"
    expected = [row for row in api.serial(rows) if query.casefold() in str(row).casefold()]
    result = api.WorkspaceService.analyze(Path("evidence"), "positions", offset=3, limit=2, query=query)
    assert result == {"analysis": "positions", **api.page(expected, 3, 2), "evidence_only": True}
    assert result["total"] == 10 and result["next_offset"] == 5


def test_complete_summary_and_analysis_errors_are_preserved(monkeypatch):
    summary = {"all_files": 8000, "nested": {"source": Path("synthetic/last.ls"), "groups": {2, 1}}}
    monkeypatch.setattr(api.diagnostics, "health_summary", lambda root: summary)
    result = api.WorkspaceService.analyze(Path("evidence"), "health", offset=1, limit=1)
    assert result["total"] == 2 and result["next_offset"] is None
    assert result["items"] == [{"name": "nested", "value": {"source": str(Path("synthetic/last.ls")),
                                                            "groups": [1, 2]}}]
    with pytest.raises(ValueError, match="Use offset >= 0"):
        api.WorkspaceService.analyze(Path("evidence"), "health", offset=-1)

    def fail_after_scan(root):
        raise ValueError("late evidence failure")

    monkeypatch.setattr(api.workspace, "scan_positions", fail_after_scan)
    with pytest.raises(ValueError, match="late evidence failure"):
        api.WorkspaceService.analyze(Path("evidence"), "positions", limit=5)
