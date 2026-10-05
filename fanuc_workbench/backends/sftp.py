from __future__ import annotations
from pathlib import Path, PurePosixPath
from fanuc_workbench.backends.base import RobotBackend
from fanuc_workbench.core.models import RemoteEntry, RobotProfile


def _clean(path: str) -> str:
    # FANUC device syntax may be exposed by SFTP as md:/... or /md/...
    return path.replace('\\','/')

class FanucSftpBackend(RobotBackend):
    """Secure file backend for controllers exposing an SFTP server.

    Paramiko is imported lazily so older controllers/users can run without it.
    Host-key policy defaults to reject unknown keys; profiles can opt into TOFU.
    """
    def __init__(self, profile: RobotProfile):
        self.profile=profile; self.client=None; self.sftp=None
    def connect(self):
        if self.sftp is not None:return
        try: import paramiko
        except ImportError as exc: raise RuntimeError('SFTP requires the optional paramiko package.') from exc
        c=paramiko.SSHClient(); c.load_system_host_keys()
        if getattr(self.profile,'sftp_trust_unknown_host',False): c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        else: c.set_missing_host_key_policy(paramiko.RejectPolicy())
        c.connect(self.profile.host,port=self.profile.port or 22,username=self.profile.username,password=self.profile.password or None,
                  timeout=self.profile.timeout,look_for_keys=True,allow_agent=True)
        self.client=c; self.sftp=c.open_sftp()
    def close(self):
        try:
            if self.sftp:self.sftp.close()
        finally:
            self.sftp=None
            if self.client:
                try:self.client.close()
                finally:self.client=None
    def _r(self):
        if self.sftp is None: raise RuntimeError('SFTP backend is not connected')
        return self.sftp
    def list_dir(self,remote_path):
        import stat
        out=[]
        for a in self._r().listdir_attr(_clean(remote_path)):
            if a.filename in {'.','..'}:continue
            p=f"{remote_path.rstrip('/')}/{a.filename}" if not remote_path.endswith(':') else remote_path+a.filename
            out.append(RemoteEntry(a.filename,p,stat.S_ISDIR(a.st_mode),None if stat.S_ISDIR(a.st_mode) else int(a.st_size),str(int(a.st_mtime))))
        return sorted(out,key=lambda x:(not x.is_dir,x.name.lower()))
    def download(self,remote_path,local_path:Path):
        local_path.parent.mkdir(parents=True,exist_ok=True); self._r().get(_clean(remote_path),str(local_path))
    def upload(self,local_path:Path,remote_path): self._r().put(str(local_path),_clean(remote_path))
    def file_exists(self,remote_path):
        try:self._r().stat(_clean(remote_path));return True
        except Exception:return False
