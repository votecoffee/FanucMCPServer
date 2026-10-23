import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def load_setup():
    path=Path(__file__).resolve().parents[1]/'packaging/bridge/setup_bundle.py'
    spec=importlib.util.spec_from_file_location('plugin_upgrade_setup',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bundle(path,version):
    (path/'.agents/plugins').mkdir(parents=True)
    (path/'plugin').mkdir()
    (path/'.agents/plugins/marketplace.json').write_text(json.dumps({'name':'fanuc-mcp-server-local',
        'plugins':[{'name':'fanuc-mcp-server'}]}))
    (path/'plugin/plugin.json').write_text(json.dumps({'name':'fanuc-mcp-server','version':version}))
    (path/'BUNDLE_MANIFEST.json').write_text('{}')


@pytest.mark.parametrize('fail_install',[False,True])
def test_owned_marketplace_move_and_registration_rollback(tmp_path,monkeypatch,fail_install):
    module=load_setup()
    old,new=tmp_path/'old bundle',tmp_path/'new bundle'
    bundle(old,'0.3.0-dev')
    bundle(new,'0.4.0-dev')
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        if command[1:4]==['plugin','marketplace','list']:
            return SimpleNamespace(stdout=json.dumps({'marketplaces':[{'name':'fanuc-mcp-server-local','root':str(old)}]}))
        if command[1:3]==['plugin','add']:
            if fail_install: raise subprocess.CalledProcessError(2,command)
            return SimpleNamespace(stdout=json.dumps({'version':'0.4.0-dev'}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(module.subprocess,'run',run)
    if fail_install:
        with pytest.raises(subprocess.CalledProcessError): module.register_plugin('codex',new,{})
        assert calls[-1][1:5]==['plugin','marketplace','add',str(old.resolve())]
    else:
        module.register_plugin('codex',new,{})
        assert calls[-1][1:3]==['plugin','add']
    assert calls[1][1:4]==['plugin','marketplace','remove']
    assert old.is_dir() and new.is_dir()


def test_foreign_marketplace_is_not_removed(tmp_path,monkeypatch):
    module=load_setup()
    new=tmp_path/'new'
    bundle(new,'0.4.0-dev')
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=json.dumps({'marketplaces':[{'name':'fanuc-mcp-server-local',
            'root':str(tmp_path/'unrelated source')}]}))
    monkeypatch.setattr(module.subprocess,'run',run)
    with pytest.raises(ValueError,match='unrecognized'):
        module.register_plugin('codex',new,{})
    assert len(calls)==1


@pytest.mark.parametrize('legacy_name', [False, True])
def test_explicit_policy_preserved_and_legacy_runtime_never_mutated(tmp_path, monkeypatch, legacy_name):
    module = load_setup()
    root = tmp_path / 'new bundle'
    wheels = root / 'plugin/wheels'
    wheels.mkdir(parents=True)
    (wheels / 'fanuc_mcp_server_bridge-0.7.0.dev0-py3-none-any.whl').write_bytes(b'fake wheel')
    module.__file__ = str(root / 'setup.py')
    monkeypatch.setattr(module, 'verify_bundle', lambda _: None)
    runtime = tmp_path / 'app owned runtime'
    config = tmp_path / 'custom offline policy.json'
    original = b'{"state_dir":"retained-evidence","source_dir":"retained-inputs","robots":{},"allow_writes":false}\r\n'
    config.write_bytes(original)
    before = None
    if legacy_name:
        runtime.mkdir()
        marker = runtime / 'installation.json'
        marker.write_text(json.dumps({'kind': 'fanuc-workbench-runtime', 'config': str(config)}))
        before = marker.read_bytes()
    from fanuc_workbench.services import stable_install
    calls = []
    def install(*args, **kwargs):
        calls.append((args, kwargs))
        return {'connection': {'command':str(runtime / 'python/pythonw.exe'),
            'args':['-I','-B',str(runtime / 'StartMcp.py')],'cwd':str(runtime)}}
    monkeypatch.setattr(stable_install, 'install', install)
    argv = ['setup.py', '--runtime-dir', str(runtime), '--config', str(config), '--register', 'none',
        '--expected-selection-sha256', 'absent', '--management-registry', str(tmp_path / 'registry'),
        '--installed-plugin-version', '26.1022.180303']
    monkeypatch.setattr(sys, 'argv', argv)
    if legacy_name:
        with pytest.raises(ValueError, match='Legacy runtime retained'): module.main()
        assert marker.read_bytes() == before and not calls
    else:
        module.main()
        assert calls[0][0][3] == config and calls[0][1]['expected_selection'] == 'absent'
        # An upgrade supplies both the policy and expected prior selection explicitly.
        argv[argv.index('absent')] = '1' * 64
        monkeypatch.setattr(sys, 'argv', argv)
        module.main()
        assert calls[1][0][3] == config and calls[1][1]['expected_selection'] == '1' * 64
    assert config.read_bytes() == original


def test_config_selection_handles_first_install_legacy_and_explicit_override(tmp_path):
    module = load_setup()
    runtime = tmp_path / 'runtime'
    assert module.select_config(runtime, None, {}) == runtime / 'robot_bridge.local.json'
    legacy = {'kind': 'fanuc-workbench-runtime'}
    with pytest.raises(ValueError, match='supply --config once'):
        module.select_config(runtime, None, legacy)
    runtime.mkdir()
    default = runtime / 'robot_bridge.local.json'
    default.write_text('{}')
    assert module.select_config(runtime, None, legacy) == default
    with pytest.raises(ValueError, match='installed configuration is missing'):
        module.select_config(runtime, None, {**legacy, 'config': str(tmp_path / 'missing.json')})
    with pytest.raises(ValueError, match='installed configuration is missing'):
        module.select_config(runtime, None, {**legacy, 'config': 'relative.json'})
    assert module.select_config(runtime, default, {**legacy, 'config': 'missing.json'}) == default.resolve()
    with pytest.raises(ValueError, match='supplied configuration'):
        module.select_config(runtime, tmp_path / 'missing.json', {})


@pytest.mark.parametrize('reported,accepted', [('26.102.30405', True), ('26.0102.030405', True), ('26.102.30406', False)])
def test_plugin_response_uses_numeric_identity_comparison(tmp_path, monkeypatch, reported, accepted):
    module = load_setup()
    bundle(tmp_path / 'bundle', '26.102.30405')
    def run(command, **kwargs):
        if command[1:4] == ['plugin', 'marketplace', 'list']:
            return SimpleNamespace(stdout='{"marketplaces":[]}')
        return SimpleNamespace(stdout=json.dumps({'version': reported}))
    monkeypatch.setattr(module.subprocess, 'run', run)
    if accepted:
        module.register_plugin('inert-codex', tmp_path / 'bundle', {})
    else:
        with pytest.raises(ValueError, match='different from this bundle'):
            module.register_plugin('inert-codex', tmp_path / 'bundle', {})
