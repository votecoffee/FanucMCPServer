import json

from fanuc_workbench.identity import configuration_environment


def test_configuration_prefers_new_name_and_retains_legacy_fallback(monkeypatch):
    monkeypatch.delenv('FANUC_MCP_SERVER_CONFIG', raising=False)
    monkeypatch.delenv('FANUC_WORKBENCH_CONFIG', raising=False)
    assert configuration_environment() is None
    monkeypatch.setenv('FANUC_WORKBENCH_CONFIG', 'old-policy.json')
    assert configuration_environment() == 'old-policy.json'
    monkeypatch.setenv('FANUC_MCP_SERVER_CONFIG', 'new-policy.json')
    assert configuration_environment() == 'new-policy.json'


def test_gui_reads_legacy_profiles_and_preserves_them_when_saving(tmp_path, monkeypatch):
    from fanuc_workbench.core.settings import SettingsStore, QStandardPaths
    current = tmp_path / 'Local' / 'FanucMCPServer'
    old = current.parent / 'FANUC Workbench' / 'profiles.json'
    old.parent.mkdir(parents=True)
    original = b'{"profiles":[{"name":"Existing robot","host":"192.0.2.1"}]}\r\n'
    old.write_bytes(original)
    monkeypatch.setattr(QStandardPaths, 'writableLocation', lambda _: str(current))
    store = SettingsStore()
    profiles = store.load_profiles()
    assert profiles[0].name == 'Existing robot' and profiles[0].host == '192.0.2.1'
    assert not store.path.exists()
    profiles[0].name = 'Updated robot'
    store.save_profiles(profiles)
    assert old.read_bytes() == original
    assert json.loads(store.path.read_text())['profiles'][0]['name'] == 'Updated robot'
    assert store.load_profiles()[0].name == 'Updated robot'
