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
    (path/'.agents/plugins/marketplace.json').write_text(json.dumps({'name':'fanuc-workbench-local',
        'plugins':[{'name':'fanuc-workbench'}]}))
    (path/'plugin/plugin.json').write_text(json.dumps({'name':'fanuc-workbench','version':version}))
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
            return SimpleNamespace(stdout=json.dumps({'marketplaces':[{'name':'fanuc-workbench-local','root':str(old)}]}))
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
        return SimpleNamespace(stdout=json.dumps({'marketplaces':[{'name':'fanuc-workbench-local',
            'root':str(tmp_path/'unrelated source')}]}))
    monkeypatch.setattr(module.subprocess,'run',run)
    with pytest.raises(ValueError,match='unrecognized'):
        module.register_plugin('codex',new,{})
    assert len(calls)==1


def test_setup_remembers_custom_configuration_across_upgrade(tmp_path, monkeypatch):
    module = load_setup()
    root = tmp_path / 'new bundle'
    wheels = root / 'plugin/wheels'
    wheels.mkdir(parents=True)
    (wheels / 'fanuc_workbench_bridge-0.7.0.dev0-py3-none-any.whl').write_bytes(b'fake wheel')
    module.__file__ = str(root / 'setup.py')
    monkeypatch.setattr(module, 'verify_bundle', lambda _: None)
    monkeypatch.setattr(module.venv, 'EnvBuilder', lambda **_: SimpleNamespace(create=lambda _: None))
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **k: SimpleNamespace(
        stdout=json.dumps({'tool_count': 42, 'configured_robots': 2})))
    runtime = tmp_path / 'stable runtime'
    config = tmp_path / 'custom robot policy.json'
    original = b'{"state_dir":"retained-evidence","source_dir":"retained-edits","robots":{}}\r\n'
    config.write_bytes(original)
    monkeypatch.setattr(sys, 'argv', ['setup.py', '--runtime-dir', str(runtime), '--config', str(config)])
    module.main()
    monkeypatch.setattr(sys, 'argv', ['setup.py', '--runtime-dir', str(runtime)])
    module.main()
    assert json.loads((runtime / 'installation.json').read_text())['config'] == str(config.resolve())
    assert json.loads((root / 'plugin/runtime.json').read_text())['config'] == str(config.resolve())
    assert config.read_bytes() == original and not (runtime / 'robot_bridge.local.json').exists()


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
