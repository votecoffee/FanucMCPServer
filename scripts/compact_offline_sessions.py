"""Retain departed offline receipts outside the live admission directory.

Run with the installed app's Python. Process inspection happens outside the
admission lock; short batches recheck exact receipt bytes before moving them.
No process is terminated and no native/controller journal is recovered.
"""
from pathlib import Path
import sys


TERMINAL = {"closed", "parent-exited", "reconciled-dead", "failed"}


def compact(root, api, driver, *, limit=4096, batch_size=8):
    if type(limit) is not int or not 1 <= limit <= 100000:
        raise ValueError("Use a receipt limit between 1 and 100000")
    if type(batch_size) is not int or not 1 <= batch_size <= 8:
        raise ValueError("Use a batch size between 1 and 8")
    root = api["ordinary"](Path(root), directory=True)
    directory, history = root / "sessions", root / "sessions/history"
    api["ordinary"](directory, directory=True)
    # Keep the same directory and lock protocol as old and new launchers.
    with api["locked"](directory / "admission.lock"):
        history.mkdir(exist_ok=True)
        api["ordinary"](history, directory=True)
    result = {"moved": [], "unverifiable_retained": [], "changed_retained": [], "ineligible": 0,
        "batch_size": batch_size, "process_control": False, "native_recovery": False}
    candidates = []
    inventory, inventory_checked = None, False
    with api["session_reader"](directory) as read:
        for path in list(directory.glob("*.json")):
            try:
                raw = read(path)
            except FileNotFoundError:
                continue  # Another fenced compaction already retained it.
            prior = api["parse"](raw)
            if (prior.get("state") not in TERMINAL or prior.get("profile") != "standalone-offline"
                    or prior.get("native_intents") is not False or prior.get("app_created_children") != []):
                result["ineligible"] += 1
                continue
            # No shared admission lock while querying the OS. A positively
            # departed exact generation cannot revive before the later CAS.
            state = driver.state(prior["identity"])
            if state == "unknown" and prior["identity"].get("host_id") == getattr(driver, "host_id", None):
                if not inventory_checked:
                    inventory = api.get("process_creation_times", lambda: None)()
                    inventory_checked = True
                observed = (inventory or {}).get(prior["identity"]["pid"])
                if observed is not None and observed > int(prior["identity"]["created"]):
                    state = "different"
            if state not in {"absent", "different"}:
                result["unverifiable_retained"].append({"path": path.name, "state": state, "sha256": api["digest"](raw)})
                continue
            candidates.append((path, raw))
            if len(candidates) >= limit:
                break
    for offset in range(0, len(candidates), batch_size):
        with (api["locked"](directory / "admission.lock"), api["session_reader"](directory) as read,
                api["session_reader"](history) as archived):
            for path, raw in candidates[offset:offset + batch_size]:
                if path.parent != directory:
                    raise ValueError("Receipt escaped the owned session directory")
                try:
                    current = read(path)
                except FileNotFoundError:
                    continue
                if current != raw:
                    result["changed_retained"].append(path.name)
                    continue
                checksum = api["digest"](raw)
                destination = history / (path.stem + "-" + checksum + ".json")
                if destination.parent != history:
                    raise ValueError("Receipt escaped retained history")
                if destination.exists():
                    if archived(destination) != raw:
                        raise ValueError("Retained history differs from its receipt")
                    path.unlink()
                else:
                    path.rename(destination)
                result["moved"].append({"original": path.name, "retained": destination.name,
                    "bytes": len(raw), "sha256": checksum})
    result["retained_bytes"] = sum(row["bytes"] for row in result["moved"])
    return result


def main():
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__ + "\nUsage: compact_offline_sessions.py --app-root PATH [--limit 4096]")
        return
    # Delay non-bootstrap imports until the installed manifest selects its
    # verified import closure. Never load checkout packages into that runtime.
    if "--app-root" not in sys.argv or sys.argv.index("--app-root") + 1 >= len(sys.argv):
        raise SystemExit("Supply --app-root using the installed app's python/python.exe")
    root = Path(sys.argv[sys.argv.index("--app-root") + 1]).absolute()
    import runpy
    api = runpy.run_path(str(root / "StartMcp.py"), run_name="fanuc_history_bootstrap")
    selection, manifest, runtime = api["selected"](root)
    api["configure_packages"](runtime, manifest)
    import argparse
    import json
    from universal_tools.windows_resources import ProcessDriver
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=4096)
    args = parser.parse_args()
    if args.app_root.absolute() != root:
        raise ValueError("Selected application root changed")
    marker = api["parse"](api["read"](root / "application.json"))
    if marker != {"schema_version": 1, "app": "fanuc-mcp-server"}:
        raise ValueError("Root is not an owned FANUC installation")
    identifier = api["uuid"].uuid4().hex
    receipt = root / "receipts" / ("history-compaction-" + identifier + ".json")
    api["ordinary"](receipt.parent, directory=True)
    report = {"schema_version": 1, "operation": "compact-terminal-offline-history", "selection": selection, "state": "started"}
    api["atomic"](receipt, api["encoded"](report))
    try:
        report["result"] = compact(root, api, ProcessDriver(), limit=args.limit)
        report["state"] = "complete"
    except BaseException as exc:
        report.update(state="interrupted", error_type=type(exc).__name__)
        raise
    finally:
        api["atomic"](receipt, api["encoded"](report))
    print(json.dumps({"receipt": str(receipt), "moved": len(report["result"]["moved"]),
        "unverifiable_retained": len(report["result"]["unverifiable_retained"]),
        "retained_bytes": report["result"]["retained_bytes"]}))


if __name__ == "__main__":
    main()
