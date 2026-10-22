"""App-selected protocol profiles; cached discovery is never write authority."""
from universal_tools.extensions import ExtensionServer


class FanucServer(ExtensionServer):
    def __init__(self, *args, enable_discovery_cache=False, **kwargs):
        self.enable_discovery_cache = enable_discovery_cache
        super().__init__(*args, **kwargs)

    async def _handle_list_tools(self, ctx, params):
        result = await super()._handle_list_tools(ctx, params)
        if self.enable_discovery_cache and ctx.protocol_version == "2026-07-28":
            result.ttl_ms, result.cache_scope = 2000, "private"
        return result


def feature_report(*, tasks=False, skills=True, protocol_preview=False):
    return {
        "evidence_scope": "synthetic/package/protocol; host UI and controller acceptance are separate",
        "guidance": {"state": "enabled-and-tested", "ordinary_readers": True, "skills": skills},
        "discovery_cache": {"state": "preview-validation-pending" if protocol_preview else "blocked-pending-validation",
                            "scope": "modern connection-private tool discovery", "ttl_ms": 2000,
                            "legacy": "missing hints disable caching; explicit refresh remains required"},
        "catalog_notifications": {"state": "preview-validation-pending", "scope": "modern tools/resources; events are refetch hints"},
        "progress_cancel": {"state": "validation-pending", "scope": "wait_job and durable job status; cooperative cancellation only"},
        "artifact_resources": {"state": "preview-validation-pending" if protocol_preview and skills else "blocked-pending-validation",
                               "scope": "app-local immutable artifact manifests and hash-bound bounded ranges",
                               "fallback": "list_artifacts, artifact_files and read_artifact remain available"},
        "tasks": {"state": "preview-validation-pending" if tasks else "blocked", "scope": "explicit backup/recording jobs only",
                  "owner": "trusted frontend; installed stdio identity is process-scoped",
                  "reason": None if tasks else "App-owned protocol preview is disabled or no trusted principal; generic host routing does not forward Tasks",
                  "restart": "Store survival is not job survival; reconnect needs the same trusted principal or app reconciliation"},
        "modern_downstream": {"state": "blocked", "reason": "Legacy-only publication guard remains until resolver/profile negative acceptance completes"},
        "replicas_oauth_apps": {"state": "outside-app-workflow", "enabled": False},
        "controller_contacted": False,
    }
