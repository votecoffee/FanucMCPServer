"""Forward setup arguments to an explicitly selected, previously built FANUC bundle."""
import argparse
from pathlib import Path
import subprocess
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog="Remaining arguments are passed unchanged to setup.py. Use --bundle PATH -- --help for bundle help.")
    parser.add_argument("--bundle", type=Path, required=True)
    args, forwarded = parser.parse_known_args(argv)
    if forwarded[:1] == ["--"]: forwarded = forwarded[1:]
    setup = args.bundle.resolve() / "setup.py"
    if not setup.is_file():
        print("Selected bundle has no setup.py", file=sys.stderr); return 2
    try:
        return subprocess.run([sys.executable, "-B", str(setup), *forwarded], cwd=setup.parent).returncode
    except OSError as exc:
        print(str(exc), file=sys.stderr); return 1


if __name__ == "__main__": raise SystemExit(main())
