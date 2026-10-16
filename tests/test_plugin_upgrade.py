import importlib.util
import json
import subprocess
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
