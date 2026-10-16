"""Offline workspace analyses hosted in a replaceable feature process."""
from pathlib import Path

from fanuc_workbench.services.workspace_api import ANALYSES, WorkspaceService


def self_test():
    return len(ANALYSES) == 20


def handle(operation_id, arguments, context):
    kind = operation_id.removeprefix("workbench.analysis.")
    if kind not in ANALYSES:
        raise ValueError("Unknown workspace analysis")
    return WorkspaceService.analyze(Path(context["workspace"]["root"]), kind,
        arguments.get("offset", 0), arguments.get("limit", 100), arguments.get("query", ""))
