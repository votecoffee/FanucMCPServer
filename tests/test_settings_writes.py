import json

import pytest

from fanuc_workbench.core import settings
from fanuc_workbench.core.models import RobotProfile


@pytest.mark.parametrize('kind', ['profiles', 'preferences'])
@pytest.mark.parametrize('failure', ['serialization', 'flush', 'replace'])
def test_failed_settings_save_preserves_original_and_cleans_temporary_files(tmp_path, monkeypatch, kind, failure):
    monkeypatch.setattr(settings.QStandardPaths, 'writableLocation', lambda _: str(tmp_path))
    store = settings.SettingsStore()
    store.save_profiles([RobotProfile('Original robot', '192.0.2.1')])
    store.update_preferences(robot_policy_path='private-policy.json', last_selected_robot={'name': 'Original robot'})
    original = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    def fail(*_):
        raise OSError('Synthetic settings write failure')

    if failure == 'flush':
        monkeypatch.setattr(settings.os, 'fsync', fail)
    elif failure == 'replace':
        monkeypatch.setattr(settings.os, 'replace', fail)
    with pytest.raises(ValueError if failure == 'serialization' else OSError):
        if kind == 'profiles':
            store.save_profiles([RobotProfile('Replacement', '192.0.2.2',
                timeout=float('nan') if failure == 'serialization' else 10)])
        else:
            store.update_preferences(layout={'width': float('nan') if failure == 'serialization' else 700})
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == original


def test_successful_atomic_save_keeps_passwords_out_and_unrelated_preferences(tmp_path, monkeypatch):
    monkeypatch.setattr(settings.QStandardPaths, 'writableLocation', lambda _: str(tmp_path))
    store = settings.SettingsStore()
    store.update_preferences(robot_policy_path='private-policy.json', layout={'tables': {}})
    robot = RobotProfile('Robot résumé', '192.0.2.1', password='synthetic-secret')
    store.save_profiles([robot]); store.remember_profile(robot, connected=True)
    assert store.load_profiles()[0].name == 'Robot résumé'
    assert store.load_profiles()[0].password == ''
    assert store.preferred_profile(store.load_profiles()).host == robot.host
    assert store.preferences()['robot_policy_path'] == 'private-policy.json'
    assert store.preferences()['layout'] == {'tables': {}}
    assert 'synthetic-secret' not in store.path.read_text() + store.preferences_path.read_text()
    assert json.loads(store.path.read_text())['profiles'][0]['name'] == robot.name
    assert sorted(p.name for p in tmp_path.iterdir()) == ['desktop.json', 'profiles.json']
