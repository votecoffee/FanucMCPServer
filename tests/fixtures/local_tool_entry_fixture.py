"""Source-only test bootstrap for the real background entrypoint.

Use only with inert non-executable test data. It does not install or alter the
normal app runtime; all dependency and source paths are explicit fixture inputs.
"""
import argparse
import hashlib
from pathlib import Path
import site
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--framework-wheel", type=Path, required=True)
    parser.add_argument("--framework-sha256", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--sdk-site", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    if hashlib.sha256(args.framework_wheel.read_bytes()).hexdigest() != args.framework_sha256:
        raise ValueError("Fixture dependency changed")
    site.addsitedir(str(args.sdk_site))
    sys.path.insert(0, str(args.framework_wheel))
    sys.path.insert(0, str(args.source_root))
    from fanuc_workbench.bridge.local_tool_worker import main as worker
    worker(["--request", str(args.request), "--sha256", args.sha256])


if __name__ == "__main__":
    main()
