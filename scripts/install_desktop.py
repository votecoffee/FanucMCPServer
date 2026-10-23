"""Install the desktop in a dedicated venv. Launch only when --launch is selected."""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv-dir", type=Path, default=Path(__file__).resolve().parents[1] / ".venv")
    parser.add_argument("--no-shortcut", action="store_true", help="Do not create a Windows Start menu shortcut")
    parser.add_argument("--launch", action="store_true", help="Launch the desktop after successful installation")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]; target = args.venv_dir.resolve()
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    environment = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
    environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    try:
        if not python.is_file():
            if target.exists() and any(target.iterdir()): raise ValueError("Select an empty directory or an existing Python venv")
            subprocess.run([sys.executable, "-B", "-m", "venv", str(target)], check=True, env=environment)
        elif not (target / "pyvenv.cfg").is_file():
            raise ValueError("Select a dedicated Python venv")
        subprocess.run([str(python), "-B", "-m", "pip", "uninstall", "--yes", "fanuc-workbench"], check=True, env=environment)
        subprocess.run([str(python), "-B", "-m", "pip", "install", "--no-compile", "-e", str(root)], check=True, env=environment)
        if not args.no_shortcut:
            subprocess.run([str(python), "-B", "-m", "fanuc_workbench.desktop_install"], check=True, env=environment)
        if args.launch:
            subprocess.run([str(python), "-B", "-m", "fanuc_workbench.app"], check=True, env=environment)
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr); return 1
    return 0


if __name__ == "__main__": raise SystemExit(main())
