"""Local operator policy authoring; never exposed as a chat write tool."""
import json
from pathlib import Path
import re
import tempfile

from fanuc_workbench.bridge.access_settings import load_access_settings
from fanuc_workbench.bridge.config import BridgeConfig, remote_path
from fanuc_workbench.core.models import RobotProfile


def policy_document(profile: RobotProfile, *, robot_id: str, read_only=True, password_env='',
                    writable_files=(), creatable_files=(), native_provider='disabled',
                    native_register_targets=(), native_programs=(), comment_service_url='', comment_targets=()):
    if type(read_only) is not bool:
        raise ValueError('Read-only mode must be a boolean')
    if not isinstance(password_env, str) or (password_env and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',password_env) is None):
        raise ValueError('Use a password environment-variable name, not a password')
    fields = {'writable_files': writable_files, 'creatable_files': creatable_files,
              'native_register_targets': native_register_targets, 'native_programs': native_programs,
              'comment_targets': comment_targets}
    for key, values in fields.items():
        if (not isinstance(values, (list, tuple)) or len(values) > 256
                or any(not isinstance(value,str) or not value or value != value.strip() for value in values)
                or len({value.casefold() for value in values}) != len(values)):
            raise ValueError(f'{key} requires distinct exact targets, at most 256')
    for value in writable_files:
        name = remote_path(value).rsplit('/',1)[-1].rsplit(':',1)[-1]
        if Path(name).suffix.upper() not in {'.LS','.KL','.TP','.PC','.TXT','.CSV'} or any(
                token in name.upper() for token in ('DCS','MASTER','SYSMAC','SYSVARS','SAFETY')):
            raise ValueError('Writable files must be supported application/program or TXT/CSV files')
    if (native_register_targets or native_programs) and native_provider != 'frrobot':
        raise ValueError('Native targets require the explicit frrobot provider')
    if comment_targets and not comment_service_url:
        raise ValueError('Description targets require an explicit controller HTTP(S) origin')
    if not read_only and not any(fields.values()):
        raise ValueError('Add at least one exact write target before enabling writes')
    entry = {'profile': profile.to_dict(), **{key:list(value) for key,value in fields.items()},
             'native_provider': native_provider}
    if password_env:
        entry['password_env'] = password_env
    if comment_service_url:
        entry['comment_service_url'] = comment_service_url
    document = {'state_dir': 'robot-state', 'source_dir': 'robot-edits', 'allow_writes': not read_only,
                'approval_mode': 'elicitation', 'robots': {robot_id: entry}}
    # Reuse the actual policy validator without opening any controller or runtime.
    with tempfile.TemporaryDirectory(prefix='fanuc_policy_') as directory:
        temporary = Path(directory)/'policy.json'
        temporary.write_text(json.dumps(document),encoding='utf-8')
        BridgeConfig.load(temporary)
    return document


def create_policy(path: Path, document: dict):
    path = Path(path).expanduser().absolute()
    if path.suffix.lower() != '.json':
        raise ValueError('Choose a JSON policy filename')
    data = json.dumps(document,indent=2,allow_nan=False) + '\n'
    with tempfile.TemporaryDirectory(prefix='fanuc_policy_') as directory:
        temporary = Path(directory)/'policy.json'
        temporary.write_text(data,encoding='utf-8')
        BridgeConfig.load(temporary)
    # Exclusive creation protects existing policies, including concurrently created files.
    with path.open('x',encoding='utf-8') as stream:
        stream.write(data)
    return load_access_settings(path)
