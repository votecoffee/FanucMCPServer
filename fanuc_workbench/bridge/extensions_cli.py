"""Operator-only staging; activation uses the already-running host's reload tool."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.extension_release import install_release, load_registry


def main():
    parser = argparse.ArgumentParser(description="Stage trusted local Workbench feature releases (no robot contact)")
    parser.add_argument("--config", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="Read the selected local registry without activating code")
    install = commands.add_parser("install", help="Validate, preserve and select a local release; does not activate it")
    install.add_argument("source", type=Path)
    install.add_argument("--python", type=Path, help="Separate local feature interpreter with its own dependencies")
    args = parser.parse_args()
    root = BridgeConfig.load(args.config.resolve()).state_dir / "extensions"
    if args.command == "install":
        result = install_release(root, args.source.resolve(), args.python)
    else:
        result = {"extensions": load_registry(root) if (root / "registry.json").exists() else [],
                  "controller_contacted": False, "code_activated": False}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
