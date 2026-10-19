"""Operator-only staging; activation uses the already-running host's reload tool."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.extension_release import install_release, load_registry


BUILTINS = {'analysis': 'analysis', 'vqlog': 'vqlog', 'snpx': 'snpx_v1_3_0', 'recording': 'recording_v1_2_0', 'alarms': 'alarms_v1_1_0', 'programs': 'programs', 'program-logic': 'programs_v1_1_0'}


def install_builtin(root: Path, name: str):
    if name not in BUILTINS:
        raise ValueError("Unknown bundled feature")
    bundled = Path(__file__).resolve().parents[1] / 'extensions'
    # Keep the standard analyses when staging into a brand-new registry.
    install_release(root, bundled / 'analysis', bootstrap=True, packaged=True)
    return install_release(root, bundled / BUILTINS[name], packaged=True)


def main():
    parser = argparse.ArgumentParser(description="Stage trusted local FanucMCPServer feature releases (no robot contact)")
    parser.add_argument("--config", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="Read the selected local registry without activating code")
    install = commands.add_parser("install", help="Validate, preserve and select a local release; does not activate it")
    install.add_argument("source", type=Path)
    install.add_argument("--python", type=Path, help="Separate local feature interpreter with its own dependencies")
    builtin = commands.add_parser('install-builtin', help='Stage a bundled feature without activating code')
    builtin.add_argument('name', choices=sorted(BUILTINS))
    args = parser.parse_args()
    root = BridgeConfig.load(args.config.resolve()).state_dir / "extensions"
    if args.command == "install":
        result = install_release(root, args.source.resolve(), args.python)
    elif args.command == 'install-builtin':
        result = install_builtin(root, args.name)
    else:
        result = {"extensions": load_registry(root) if (root / "registry.json").exists() else [],
                  "controller_contacted": False, "code_activated": False}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
