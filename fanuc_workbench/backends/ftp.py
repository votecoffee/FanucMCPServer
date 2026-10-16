from __future__ import annotations

from contextlib import contextmanager
from ftplib import FTP, error_perm
from pathlib import Path, PurePosixPath

from fanuc_workbench.core.models import RemoteEntry, RobotProfile
from .base import RobotBackend


def _join_remote(parent: str, child: str) -> str:
    if parent.endswith(":"):
        return f"{parent}{child}"
    return f"{parent.rstrip('/')}/{child}"


class FanucFtpBackend(RobotBackend):
    def __init__(self, profile: RobotProfile) -> None:
        self.profile = profile
        self.ftp: FTP | None = None

    def connect(self) -> None:
        if self.ftp is not None:
            return
        ftp = FTP()
        try:
            ftp.connect(self.profile.host, self.profile.port, timeout=self.profile.timeout)
            ftp.login(self.profile.username, self.profile.password)
            ftp.set_pasv(True)
        except BaseException:
            ftp.close()
            raise
        self.ftp = ftp

    def close(self) -> None:
        if self.ftp is None:
            return
        try:
            self.ftp.quit()
        except Exception:
            try:
                self.ftp.close()
            except Exception:
                pass
        self.ftp = None

    def _require(self) -> FTP:
        if self.ftp is None:
            raise RuntimeError("FTP backend is not connected")
        return self.ftp

    @contextmanager
    def _cwd(self, remote_path: str):
        ftp = self._require()
        previous = ftp.pwd()
        ftp.cwd(remote_path)
        try:
            yield ftp
        finally:
            try:
                ftp.cwd(previous)
            except Exception:
                pass

    def list_dir(self, remote_path: str) -> list[RemoteEntry]:
        ftp = self._require()
        entries: list[RemoteEntry] = []
        with self._cwd(remote_path):
            try:
                for name, facts in ftp.mlsd():
                    if name in {".", ".."}:
                        continue
                    typ = facts.get("type", "")
                    entries.append(
                        RemoteEntry(
                            name=name,
                            path=_join_remote(remote_path, name),
                            is_dir=typ == "dir",
                            size=int(facts["size"]) if facts.get("size", "").isdigit() else None,
                            modified=facts.get("modify"),
                        )
                    )
                return sorted(entries, key=lambda e: (not e.is_dir, e.name.lower()))
            except Exception:
                pass

            # FANUC FTP implementations vary; NLST + a CWD probe is a useful fallback.
            for raw in ftp.nlst():
                name = PurePosixPath(raw.rstrip("/")).name or raw
                if name in {".", ".."}:
                    continue
                is_dir = False
                here = ftp.pwd()
                try:
                    ftp.cwd(name)
                    is_dir = True
                except Exception:
                    is_dir = False
                finally:
                    try:
                        ftp.cwd(here)
                    except Exception:
                        pass
                size = None
                if not is_dir:
                    try:
                        size = ftp.size(name)
                    except Exception:
                        pass
                entries.append(RemoteEntry(name=name, path=_join_remote(remote_path, name), is_dir=is_dir, size=size))
        return sorted(entries, key=lambda e: (not e.is_dir, e.name.lower()))

    def _split_parent(self, remote_path: str) -> tuple[str, str]:
        if ":" in remote_path and "/" not in remote_path:
            idx = remote_path.rfind(":")
            return remote_path[: idx + 1], remote_path[idx + 1 :]
        if "/" in remote_path:
            parent, name = remote_path.rsplit("/", 1)
            return parent or "/", name
        return ".", remote_path

    def download(self, remote_path: str, local_path: Path) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        parent, name = self._split_parent(remote_path)
        with self._cwd(parent):
            with local_path.open("wb") as fh:
                self._require().retrbinary(f"RETR {name}", fh.write)

    def upload(self, local_path: Path, remote_path: str) -> None:
        parent, name = self._split_parent(remote_path)
        with self._cwd(parent):
            with local_path.open("rb") as fh:
                self._require().storbinary(f"STOR {name}", fh)

    def file_exists(self, remote_path: str) -> bool:
        parent, name = self._split_parent(remote_path)
        try:
            with self._cwd(parent):
                try:
                    self._require().size(name)
                    return True
                except Exception:
                    return name in {PurePosixPath(x).name for x in self._require().nlst()}
        except (error_perm, OSError):
            return False
