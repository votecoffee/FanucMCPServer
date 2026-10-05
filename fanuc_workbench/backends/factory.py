from fanuc_workbench.core.models import RobotProfile
from fanuc_workbench.backends.ftp import FanucFtpBackend
from fanuc_workbench.backends.sftp import FanucSftpBackend

def create_backend(profile: RobotProfile):
    return FanucSftpBackend(profile) if profile.transport.lower()=='sftp' else FanucFtpBackend(profile)
