"""Verified dependency intake rejects incompatible SDKs and damaged inventories."""
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import runpy
import zipfile

import pytest

verify = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/build_robot_plugin.py"))["verify_framework_wheel"]


def wheel_case(tmp_path, defect=None):
    wheel = tmp_path / "universal_codex_tools-0.15.0-py3-none-any.whl"
    metadata = "Name: universal-codex-tools\nVersion: 0.15.0\nRequires-Dist: mcp==2.3.0\nRequires-Dist: pytest==9.1.1; extra == \"dev\"\n\n"
    if defect == "sdk": metadata = metadata.replace("mcp==2.3.0", "mcp==1.30.0")
    entries = {"universal_tools/framework.py": b"# Synthetic fixture, no framework implementation\n",
               "universal_codex_tools-0.15.0.dist-info/METADATA": metadata.encode()}
    record = "universal_codex_tools-0.15.0.dist-info/RECORD"
    rows = [[name, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode(), str(len(raw))]
            for name, raw in entries.items()]
    if defect == "record": rows[0][1] = "sha256=wrong"
    rows.append([record, "", ""])
    stream = io.StringIO(); csv.writer(stream).writerows(rows); entries[record] = stream.getvalue().encode()
    if defect == "extra": entries["unlisted.py"] = b"# unlisted"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, raw in entries.items(): archive.writestr(name, raw)
    (tmp_path / "packaging").mkdir()
    lock = {"package": "universal-codex-tools", "version": "0.15.0", "wheel": wheel.name,
            "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(), "dependencies": ["mcp==2.3.0"]}
    (tmp_path / "packaging/framework.lock.json").write_text(json.dumps(lock))
    return wheel


def test_verified_framework_intake_ignores_only_optional_dev_dependencies(tmp_path):
    assert verify(tmp_path, wheel_case(tmp_path))["version"] == "0.15.0"


@pytest.mark.parametrize("defect", ["sdk", "record", "extra", "outer_hash"])
def test_framework_intake_refuses_unreviewed_or_inconsistent_wheel(tmp_path, defect):
    wheel = wheel_case(tmp_path, defect)
    if defect == "outer_hash": wheel.write_bytes(wheel.read_bytes() + b"changed")
    with pytest.raises(ValueError): verify(tmp_path, wheel)
