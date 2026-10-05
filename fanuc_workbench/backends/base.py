from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from fanuc_workbench.core.models import RemoteEntry


class RobotBackend(ABC):
    """Controller transport abstraction.

    Add future implementations for HTTP/CGI, KAREL bridges, SDKs, or controller-
    generation-specific APIs without changing the GUI or backup service.
    """

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def list_dir(self, remote_path: str) -> list[RemoteEntry]: ...

    @abstractmethod
    def download(self, remote_path: str, local_path: Path) -> None: ...

    @abstractmethod
    def upload(self, local_path: Path, remote_path: str) -> None: ...

    @abstractmethod
    def file_exists(self, remote_path: str) -> bool: ...

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
