"""Operator-editable policy fields. Runtime validators remain authoritative."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyField:
    key: str
    label: str
    kind: str = "text"
    default: object = ""
    minimum: float = 0
    maximum: float = 0
    choices: tuple = ()
    hint: str = ""


F = PolicyField
SERVER_FIELDS = (
    F("allow_writes", "Allow reviewed controller writes", "bool", False),
    F("write_scope", "Controller write scope", "choice", "allowlisted", choices=("allowlisted", "all_supported"),
      hint="all_supported permits supported targets for every configured robot without maintaining exact write lists. Read roots, providers, supported write classes and proposal verification still apply."),
    F("approval_mode", "Approval method", "choice", "chat", choices=("chat", "elicitation"),
      hint="chat requires exact proposal approval in the calling workflow; elicitation additionally requires the client confirmation form."),
    F("plan_ttl_seconds", "Prepared-change lifetime (seconds)", "int", 900, 1, 3600),
    F("max_file_bytes", "Maximum file size (bytes)", "int", 16777216, 1, 67108864),
    F("max_backup_files", "Maximum files per backup", "int", 5000, 1, 20000),
    F("max_backup_bytes", "Maximum backup size (bytes)", "int", 536870912, 1, 2147483648),
    F("max_concurrent_requests", "Concurrent request workers", "int", 4, 1, 16),
    F("max_pending_requests", "Queued requests", "int", 16, 0, 128),
    F("robot_lock_timeout_seconds", "Controller lock wait (seconds)", "float", 15, 0, 60),
    F("state_dir", "Evidence / state directory", default="robot-state", hint="Relative paths resolve beside this policy file."),
    F("source_dir", "Local edit directory", default="robot-edits", hint="Relative paths resolve beside this policy file."),
)
PROFILE_FIELDS = (
    F("name", "Display name", default="Robot"), F("host", "Controller host"),
    F("transport", "File transport", "choice", "ftp", choices=("ftp", "sftp")),
    F("port", "File-service port", "int", 21, 1, 65535),
    F("timeout", "Transport deadline (seconds)", "float", 10, .001, 120),
    F("username", "File-service username", default="anonymous"),
    F("roots", "Allowed remote read roots", "lines", ("md:",), hint="One explicit device or absolute SFTP path per line."),
)
CONNECTION_FIELDS = (
    F("password_env", "File password environment variable", "optional"),
    F("native_provider", "Native API provider", "choice", "disabled", choices=("disabled", "frrobot")),
    F("sample_provider", "Live I/O sample provider", "choice", "auto", choices=("auto", "snpx")),
)
WRITE_FIELDS = (
    F("writable_files", "Replace files: exact remote paths", "lines", ()),
    F("creatable_files", "Create TXT/CSV storage files: exact paths", "lines", ()),
    F("native_register_targets", "Native R/PR write targets", "lines", ()),
    F("native_programs", "Native program deployment names", "lines", ()),
    F("comment_service_url", "Description service origin", "optional"),
    F("comment_targets", "Description write targets", "lines", ()),
    F("comment_max_bytes", "Description limit (encoded bytes)", "int", 16, 1, 256),
    F("comment_encoding", "Description encoding", "choice", "ascii", choices=("ascii", "latin-1", "utf-8")),
)
HTTP_FIELDS = (
    F("http_read_url", "HTTP file-read origin", "optional"),
    F("http_read_files", "HTTP read files: exact paths", "lines", ()),
    F("http_read_username", "HTTP username"),
    F("http_read_password_env", "HTTP password environment variable", "optional"),
)
SNPX_FIELDS = (
    F("port", "SNPX port", "int", 60008, 1, 65535),
    F("io_targets", "Exact I/O read targets", "lines", (), hint="One DI/DO/RI/RO/UI/UO/GI/GO/AI/AO target per line."),
    F("word_ranges", "Allowed memory word ranges", "ranges", (), hint="One address,count pair per line, for example 1,2."),
    F("io_word_formats", "Group/analog decoding", "formats", {}, hint="One target=format per line, for example GI[1]=u16 or AI[1]=i16."),
)
HOST_FIELDS = (
    F("enabled", "Enable this app service", "bool", True),
    F("allow_writes", "Permit the host write channel", "bool", False,
      hint="Local preparation, staging, jobs and reloads also use this channel. Robot write policy remains separate."),
    F("allowed_write_tools", "Allowed outer write tools", "nullable_lines", None,
      hint="Exact allowlist: empty permits no writes. Unchecked restriction preserves legacy service-wide permission."),
    F("read_only_tools", "Legacy read-only tool classifications", "lines", (), hint="Use only app-owner reviewed classifications; this does not authorize robot changes."),
    F("max_argument_bytes", "Maximum tool arguments (UTF-8 bytes)", "int", 262144, 1, 67108864),
    F("max_argument_depth", "Maximum argument nesting depth", "int", 64, 1, 128,
      hint="Checked before schema validation, write claims and controller dispatch."),
    F("max_argument_nodes", "Maximum argument nodes", "int", 100000, 1, 1000000,
      hint="Object keys count as nodes. Independent of the UTF-8 byte limit."),
    F("timeout_seconds", "Host call deadline (seconds)", "float", 20, .2, 300),
    F("session_mode", "Session ownership mode", "choice", "transient", choices=("exclusive", "shared", "transient"),
      hint="FANUC review plans require a retained session. Exclusive is the assessed mode; transient loses plans, and shared does not isolate chat-owned plans."),
    F("lease_seconds", "Exclusive context lifetime (seconds)", "float", 900, 5, 86400),
    F("resource_id", "Cross-registration resource identity", "optional"),
)
HOST_SERVER_FIELDS = (
    F("max_request_body_bytes", "Host HTTP request limit (bytes)", "int", 10485760, 1, 134217728),
    F("port", "Host loopback port", "int", 8768, 1024, 65535),
    F("token", "Host authentication token", "secret", hint="Changing this token requires reconnecting every host frontend."),
)

BUILT_IN_GUARDS = """These checks are part of the supported protocol rather than policy switches:
• Exact source/proposal hashes, single-use change plans, commit preflight and verified readback.
• Explicit robot IDs, confined paths, supported write classes and verified SFTP host keys.
• Controller/API identity checks, native program-load checks and register metadata checks.
• No motion/program execution, automatic write retry or automatic rollback.
• Supported numeric bounds, protocol framing, payload/schema integrity and immutable feature hashes.
• App-host authentication, literal loopback binding, context ownership and recovery barriers.

The policy controls configure access, exact-list or broad supported-target scope, approval method, expiry and resource limits
within those contracts. Editing local policy does not authorize a robot change or update an
already loaded MCP/native session. Save, reconnect/reload deliberately, then retrieve fresh
evidence and prepare a new change. Host edits require a stopped host and reconciled work.
"""
