from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class RobotProfile:
    name: str
    host: str
    port: int = 21
    username: str = "anonymous"
    password: str = ""
    roots: list[str] = field(default_factory=lambda: ["md:"])
    timeout: float = 10.0
    transport: str = "ftp"
    sftp_trust_unknown_host: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # Never persist passwords by default.
        data["password"] = ""
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RobotProfile":
        return cls(
            name=str(data.get("name", "Robot")),
            host=str(data.get("host", "")),
            port=int(data.get("port", 21)),
            username=str(data.get("username", "anonymous")),
            password=str(data.get("password", "")),
            roots=list(data.get("roots") or ["md:"]),
            timeout=float(data.get("timeout", 10.0)),
            transport=str(data.get("transport", "ftp")),
            sftp_trust_unknown_host=bool(data.get("sftp_trust_unknown_host", False)),
        )


@dataclass(slots=True)
class RemoteEntry:
    name: str
    path: str
    is_dir: bool
    size: int | None = None
    modified: str | None = None


@dataclass(slots=True)
class BackupRecord:
    remote_path: str
    local_path: str
    size: int | None = None
    kind: str = "file"
    ascii_companion_of: str | None = None
    error: str | None = None


@dataclass(slots=True)
class BackupManifest:
    robot_name: str
    host: str
    created_at: str
    roots: list[str]
    records: list[BackupRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "robot_name": self.robot_name,
            "host": self.host,
            "created_at": self.created_at,
            "roots": self.roots,
            "records": [asdict(r) for r in self.records],
            "warnings": self.warnings,
        }


@dataclass(slots=True)
class ProgramInfo:
    path: Path
    program_name: str
    comment: str


@dataclass(slots=True)
class PositionRecord:
    program: str
    index: int
    group: str
    user_frame: str = ""
    user_tool: str = ""
    config: str = ""
    values: dict[str, float | str] = field(default_factory=dict)
