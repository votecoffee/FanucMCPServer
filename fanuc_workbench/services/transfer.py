from __future__ import annotations

from pathlib import Path

from fanuc_workbench.backends.base import RobotBackend
from fanuc_workbench.backends.ftp import FanucFtpBackend
from fanuc_workbench.backends.sftp import FanucSftpBackend, _clean


class TransferLimitError(ValueError):
    pass


def download_bounded(backend: RobotBackend, remote: str, local: Path, maximum: int) -> None:
    """Bound bytes while receiving on the existing FTP/SFTP transports.

    Custom RobotBackend implementations must supply download_bounded themselves.
    A post-download size check alone would not bound network or disk consumption.
    """
    local.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with local.open("xb") as stream:
            created = True
            if isinstance(backend, FanucFtpBackend):
                parent, name = backend._split_parent(remote)
                total = 0
                def receive(chunk: bytes) -> None:
                    nonlocal total
                    total += len(chunk)
                    if total > maximum:
                        raise TransferLimitError("Remote file exceeds max_file_bytes")
                    stream.write(chunk)

                with backend._cwd(parent):
                    backend._require().retrbinary(f"RETR {name}", receive)
            elif isinstance(backend, FanucSftpBackend):
                with backend._r().open(_clean(remote), "rb") as source:
                    remaining = maximum
                    while chunk := source.read(min(65536, remaining + 1)):
                        if len(chunk) > remaining:
                            raise TransferLimitError("Remote file exceeds max_file_bytes")
                        stream.write(chunk)
                        remaining -= len(chunk)
            elif callable(getattr(backend, "download_bounded", None)):
                # Custom backends receive an exclusively reserved destination.
                stream.close()
                backend.download_bounded(remote, local, maximum)
                if local.stat().st_size > maximum:
                    raise TransferLimitError("Remote file exceeds max_file_bytes")
            else:
                raise TypeError("This backend does not implement bounded downloads")
    except BaseException:
        if created:
            local.unlink(missing_ok=True)
        raise
