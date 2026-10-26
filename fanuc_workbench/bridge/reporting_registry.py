"""Read-verify prepared Windows reporting directories without reapplying ACLs.

Private creation and the first retained handle are one native operation. Existing
permissions are verified without repair through retained, non-reparse handles.
"""
import threading
from pathlib import Path

from universal_tools.management import ManagementRegistry
from fanuc_workbench.bridge.reporting_directory import open_private_directory


class ReportingRegistry(ManagementRegistry):
    def __init__(self, directory):
        # resolve() would hide a pre-existing junction before it can be refused.
        self.directory = Path(directory).absolute()
        self._handles = {}
        self._lock = threading.RLock()
        self._closed = False

    @staticmethod
    def _identity(handle):
        import win32con
        import win32file
        info = win32file.GetFileInformationByHandle(handle)
        if not info[0] & win32con.FILE_ATTRIBUTE_DIRECTORY or info[0] & win32con.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("Reporting directory must be an ordinary directory")
        return info[4], info[8], info[9]  # Volume and full file index, from the handle.

    @staticmethod
    def _verify(handle):
        import ntsecuritycon
        import win32api
        import win32con
        import win32security

        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        finally:
            token.Close()
        expected = {win32security.ConvertSidToStringSid(sid) for sid in
                    (user, win32security.CreateWellKnownSid(win32security.WinLocalSystemSid))}
        descriptor = win32security.GetSecurityInfo(handle, win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION | win32security.OWNER_SECURITY_INFORMATION)
        if descriptor.GetSecurityDescriptorOwner() != user:
            raise ValueError("Existing reporting directory owner differs")
        control, _ = descriptor.GetSecurityDescriptorControl()
        acl = descriptor.GetSecurityDescriptorDacl()
        if not control & win32security.SE_DACL_PROTECTED or acl is None or acl.GetAceCount() != len(expected):
            raise ValueError("Existing reporting directory permissions are not private")
        observed = set()
        flags = win32con.OBJECT_INHERIT_ACE | win32con.CONTAINER_INHERIT_ACE
        for index in range(acl.GetAceCount()):
            ace = acl.GetAce(index)
            if (len(ace) != 3 or ace[0] != (win32security.ACCESS_ALLOWED_ACE_TYPE, flags)
                    or ace[1] != ntsecuritycon.FILE_ALL_ACCESS):
                raise ValueError("Existing reporting directory permissions differ")
            observed.add(win32security.ConvertSidToStringSid(ace[2]))
        if observed != expected:
            raise ValueError("Existing reporting directory principals differ")

    def _pin(self, path, *, private=False):
        import ntsecuritycon
        import win32con
        import win32file
        if path not in self._handles:
            if private:
                handle, _created = open_private_directory(self._handles[path.parent][0], path.name)
            else:
                handle = win32file.CreateFile(str(path), ntsecuritycon.FILE_READ_ATTRIBUTES |
                    ntsecuritycon.FILE_LIST_DIRECTORY,
                    win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE, None, win32con.OPEN_EXISTING,
                    win32file.FILE_FLAG_BACKUP_SEMANTICS | win32file.FILE_FLAG_OPEN_REPARSE_POINT, None)
            try:
                identity = self._identity(handle)
                if private:
                    self._verify(handle)
            except BaseException:
                handle.Close()
                raise
            self._handles[path] = handle, identity
        else:
            handle, identity = self._handles[path]
            if self._identity(handle) != identity:
                raise ValueError("Reporting directory identity changed")
            if private:
                self._verify(handle)

    def prepare(self):
        with self._lock:
            if self._closed:
                raise RuntimeError("Reporting registry is closed")
            for path in reversed(self.directory.parents):
                self._pin(path)
            for path in (self.directory, self.directory / "instances"):
                self._pin(path, private=True)

    def close(self):
        with self._lock:
            self._closed = True
            for handle, _ in self._handles.values():
                handle.Close()
            self._handles.clear()
