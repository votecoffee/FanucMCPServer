from pathlib import Path

import pytest

from fanuc_workbench.parsers.comments import CommentRecord
from fanuc_workbench.services.live_comments import build_comset_url, target_for_record


def record(kind: str, identifier: str, **meta):
    return CommentRecord(kind=kind, identifier=identifier, comment="", value="", path=Path("x"), metadata=meta)


def test_comset_targets():
    assert target_for_record(record("Numeric Register", "R[12]", index=12)).function_code == 1
    pr = target_for_record(record("Position Register", "PR[7]", group=1, index=7))
    assert (pr.function_code, pr.index) == (3, 7)
    di = target_for_record(record("I/O", "DIN[3]", io_type="DIN", index=3))
    assert (di.function_code, di.index) == (8, 3)


def test_multigroup_pr_live_edit_rejected():
    with pytest.raises(ValueError):
        target_for_record(record("Position Register", "PR[2,7]", group=2, index=7))


def test_comset_url_encoding():
    target = target_for_record(record("Numeric Register", "R[5]", index=5))
    url = build_comset_url("192.168.1.10", target, "Part Count / A")
    assert url == "http://192.168.1.10/karel/ComSet?sComment=Part%20Count%20%2F%20A&sIndx=5&sFc=1"
