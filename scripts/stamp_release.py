"""Stamp an explicitly selected New York release once; never called by a build."""
import argparse
import json
from pathlib import Path
import re
import runpy


def stamp(source, value):
    source = Path(source)
    versions = runpy.run_path(str(source / "fanuc_workbench/version.py"))
    if versions["display_timestamp"](value) != value:
        raise ValueError("Supply the fully padded YY.MMDD.HHmmss stamp selected in America/New_York")
    canonical = versions["canonical_timestamp"](value)
    changes = {}
    for relative in ("pyproject.toml", "packaging/bridge/pyproject.toml", "fanuc_workbench/version.py"):
        path = source / relative
        pattern = rb'(?m)^' + (b'DISPLAY_VERSION' if relative.endswith("version.py") else b'version') + rb' = "[^"]+"'
        replacement = (f'DISPLAY_VERSION = "{value}"' if relative.endswith("version.py") else f'version = "{canonical}"').encode()
        updated, count = re.subn(pattern, lambda _: replacement, path.read_bytes())
        if count != 1:
            raise ValueError("Expected one release field in " + relative)
        changes[path] = updated
    plugin = source / "plugins/fanuc-mcp-server/plugin.json"
    json.loads(plugin.read_text(encoding="utf-8"))
    updated, count = re.subn(rb'"version": "[^"]+"', lambda _: f'"version": "{canonical}"'.encode(), plugin.read_bytes())
    if count != 1:
        raise ValueError("Expected one plugin release field")
    changes[plugin] = updated
    for path, payload in changes.items():
        path.write_bytes(payload)
    return runpy.run_path(str(source / "fanuc_workbench/version.py"))["release_metadata"]()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="One fixed YY.MMDD.HHmmss value in America/New_York")
    args = parser.parse_args()
    print(json.dumps(stamp(Path(__file__).resolve().parents[1], args.version), indent=2))


if __name__ == "__main__":
    main()
