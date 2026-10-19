from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from fanuc_workbench.core.models import RobotProfile


def remote_path(value: str) -> str:
    """Accept explicit FANUC device paths or absolute SFTP paths only."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("An explicit remote path is required")
    if any(ord(c) < 32 or ord(c) == 127 or c in "\\*?%" for c in value):
        raise ValueError("Remote paths cannot contain controls, backslashes or wildcards")
    device = re.match(r"^([A-Za-z][A-Za-z0-9]*:)(.*)$", value)
    if device:
        prefix, suffix = device.groups()
        prefix = prefix.lower()
        suffix = suffix.removeprefix("/")
    elif value.startswith("/"):
        prefix, suffix = "/", value[1:]
    else:
        raise ValueError("Use a device path such as md:MAIN.LS or an absolute SFTP path")
    if suffix and any(p in {"", ".", ".."} or ":" in p for p in suffix.split("/")):
        raise ValueError("Remote path contains an ambiguous or traversing component")
    return prefix + suffix


def under_root(path: str, root: str) -> bool:
    return path == root or path.startswith(root if root.endswith((":", "/")) else root + "/")


@dataclass(frozen=True)
class RobotAccess:
    profile: RobotProfile
    password_env: str | None = None
    writable_files: tuple[str, ...] = ()
    comment_service_url: str | None = None
    comment_targets: tuple[str, ...] = ()
    creatable_files: tuple[str, ...] = ()
    comment_max_bytes: int = 16
    comment_encoding: str = "ascii"
    native_provider: str = "disabled"
    native_register_targets: tuple[str, ...] = ()
    native_programs: tuple[str, ...] = ()

    def connection_profile(self) -> RobotProfile:
        # Resolve secrets only at connection time; never return them through tools.
        data = self.profile.to_dict()
        if self.password_env:
            if self.password_env not in os.environ:
                raise ValueError(f"Required password environment variable is not set: {self.password_env}")
            data["password"] = os.environ[self.password_env]
        return RobotProfile.from_dict(data)


@dataclass(frozen=True)
class BridgeConfig:
    state_dir: Path
    source_dir: Path
    robots: dict[str, RobotAccess] = field(default_factory=dict)
    allow_writes: bool = False
    max_file_bytes: int = 16 * 1024 * 1024
    plan_ttl_seconds: int = 900
    approval_mode: str = "chat"
    max_backup_files: int = 5000
    max_backup_bytes: int = 512 * 1024 * 1024

    @property
    def read_only(self) -> bool:
        """Whether controller writes are disabled; local artifacts remain writable."""
        return not self.allow_writes

    @classmethod
    def load(cls, path: Path) -> "BridgeConfig":
        path = path.resolve()
        data = json.loads(path.read_text(encoding="utf-8"))
        if type(data.get("allow_writes", False)) is not bool:
            raise ValueError("allow_writes must be a JSON boolean")
        robots = {}
        for robot_id, item in data.get("robots", {}).items():
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", robot_id):
                raise ValueError("Robot IDs must contain only letters, numbers, underscores or hyphens")
            profile_data = dict(item["profile"])
            if profile_data.get("password"):
                raise ValueError("Use password_env instead of storing passwords in bridge JSON")
            transport = profile_data.get("transport", "ftp")
            if transport not in {"ftp", "sftp"}:
                raise ValueError("Transport must be ftp or sftp")
            profile_data.setdefault("port", 22 if transport == "sftp" else 21)
            profile = RobotProfile.from_dict(profile_data)
            if not profile.host.strip() or not 1 <= profile.port <= 65535 or not 0 < profile.timeout <= 120:
                raise ValueError("Profile requires a host, valid port and timeout in (0, 120]")
            if profile.sftp_trust_unknown_host:
                raise ValueError("The bridge requires verified SFTP host keys")
            roots = profile_data.get("roots", ["md:"])
            if not isinstance(roots, list) or not roots:
                raise ValueError("At least one explicit remote root is required")
            profile.roots = [remote_path(r.rstrip("/") if r != "/" else r) for r in roots]
            writable = tuple(remote_path(p) for p in item.get("writable_files", []))
            if any(not any(under_root(p, r) for r in profile.roots) for p in writable):
                raise ValueError("Writable files must stay inside configured remote roots")
            creatable = tuple(remote_path(p) for p in item.get("creatable_files", []))
            if any(not any(under_root(p, r) for r in profile.roots) for p in creatable):
                raise ValueError("Creatable files must stay inside configured remote roots")
            if any(p.startswith("md:") or Path(p).suffix.upper() not in {".TXT", ".CSV"} for p in creatable):
                raise ValueError("Creation is limited to TXT/CSV storage files outside MD:")
            comment_url = item.get("comment_service_url")
            if comment_url:
                parsed = urlsplit(comment_url)
                if (parsed.scheme not in {"http", "https"} or parsed.hostname != profile.host.casefold()
                        or (parsed.port is not None and not 1 <= parsed.port <= 65535)
                        or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
                    raise ValueError("Comment service URL must be an HTTP(S) origin on this configured robot host")
            maximum_comment = item.get("comment_max_bytes", 16)
            comment_encoding = item.get("comment_encoding", "ascii")
            if type(maximum_comment) is not int or not 1 <= maximum_comment <= 256:
                raise ValueError("comment_max_bytes must be an integer between 1 and 256")
            if comment_encoding not in {"ascii", "latin-1", "utf-8"}:
                raise ValueError("comment_encoding must be ascii, latin-1 or utf-8")
            targets = tuple(item.get("comment_targets", []))
            if any(not isinstance(target, str) or not re.fullmatch(r"(?:R|PR|DIN|DOUT|GIN|GOUT|AIN|AOUT|RI|RO|FLG)\[[1-9][0-9]{0,5}\]", target)
                   for target in targets):
                raise ValueError("Comment targets must be explicit supported register or I/O identifiers")
            from fanuc_workbench.services.native_values import register_target, program_name
            native_provider = item.get("native_provider", "disabled")
            if native_provider not in {"disabled", "frrobot"}:
                raise ValueError("Native provider must be disabled or frrobot")
            native_targets, native_programs = item.get("native_register_targets", []), item.get("native_programs", [])
            if (not isinstance(native_targets, list) or len(native_targets) > 256
                    or not isinstance(native_programs, list) or len(native_programs) > 256):
                raise ValueError("Native policy requires bounded lists of exact targets")
            for target in native_targets:
                register_target(target)
            for name in native_programs:
                program_name(name)
            robots[robot_id] = RobotAccess(profile, item.get("password_env"), writable, comment_url, targets,
                creatable, maximum_comment, comment_encoding, native_provider, tuple(native_targets), tuple(native_programs))

        def local_dir(key: str) -> Path:
            value = Path(data[key])
            return (value if value.is_absolute() else path.parent / value).resolve()

        maximum = data.get("max_file_bytes", 16 * 1024 * 1024)
        ttl = data.get("plan_ttl_seconds", 900)
        if type(maximum) is not int or not 1 <= maximum <= 64 * 1024 * 1024:
            raise ValueError("max_file_bytes must be between 1 and 67108864")
        if type(ttl) is not int or not 1 <= ttl <= 3600:
            raise ValueError("plan_ttl_seconds must be between 1 and 3600")
        approval = data.get("approval_mode", "chat")
        if approval not in {"chat", "elicitation"}:
            raise ValueError("approval_mode must be chat or elicitation")
        file_limit = data.get("max_backup_files", 5000)
        byte_limit = data.get("max_backup_bytes", 512 * 1024 * 1024)
        if type(file_limit) is not int or not 1 <= file_limit <= 20000:
            raise ValueError("max_backup_files must be between 1 and 20000")
        if type(byte_limit) is not int or not 1 <= byte_limit <= 2 * 1024 * 1024 * 1024:
            raise ValueError("max_backup_bytes must be between 1 and 2147483648")
        return cls(local_dir("state_dir"), local_dir("source_dir"), robots,
                   data.get("allow_writes", False), maximum, ttl, approval, file_limit, byte_limit)
