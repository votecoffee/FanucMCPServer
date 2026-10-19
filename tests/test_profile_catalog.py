from dataclasses import replace

import pytest

from fanuc_workbench.core.models import RobotProfile
from fanuc_workbench.services.profiles import ProfileCatalog, normalized_profile, profile_key, profile_signature


def test_catalog_edits_are_isolated_and_rename_replaces_the_original():
    original = RobotProfile('Robot A', '192.0.2.1', password='session-only', timeout=45)
    catalog = ProfileCatalog(iter([original]))
    catalog.update(0, replace(original, name='Renamed robot'))
    assert original.name == 'Robot A'
    assert len(catalog.profiles) == 1 and catalog.profiles[0].name == 'Renamed robot'
    catalog.profiles[0].roots.append('mc:')
    assert catalog.profiles[0].roots == original.roots == ['md:']
    first = catalog.duplicate(0); second = catalog.duplicate(0)
    assert [p.name for p in catalog.profiles] == ['Renamed robot', 'Renamed robot copy', 'Renamed robot copy 2']
    assert catalog.profiles[first].timeout == 45 and catalog.profiles[second].password == 'session-only'
    catalog.remove(first)
    assert len(catalog.validated_profiles()) == 2


@pytest.mark.parametrize('changes', [
    {'name': ''}, {'host': ''}, {'host': 'ftp://192.0.2.1'}, {'host': 'robot name'},
    {'transport': 'http'}, {'port': 0}, {'port': True}, {'timeout': float('nan')},
    {'timeout': 0}, {'roots': ['md:\n']},
])
def test_catalog_rejects_invalid_connections_without_mutating_the_list(changes):
    catalog = ProfileCatalog([RobotProfile('Original', '192.0.2.1')])
    candidate = replace(catalog.profiles[0], **changes)
    with pytest.raises(ValueError):
        catalog.update(0, candidate)
    assert catalog.profiles[0].name == 'Original' and catalog.profiles[0].host == '192.0.2.1'


def test_unique_names_and_normalized_connection_fields():
    catalog = ProfileCatalog([RobotProfile('Robot', '192.0.2.1')])
    with pytest.raises(ValueError, match='unique name'):
        catalog.add(RobotProfile(' ROBOT ', '192.0.2.2'))
    profile = normalized_profile(RobotProfile(' IPv6 robot ', ' ::1 ', transport='SFTP', port=2222, username='', roots=[]))
    assert profile.name == 'IPv6 robot' and profile.host == '::1'
    assert profile.transport == 'sftp' and profile.port == 2222 and profile.username == 'anonymous'
    assert profile.roots == ['md:']


def test_remembered_success_follows_rename_but_does_not_claim_connection_to_a_new_host():
    original = RobotProfile('Original', '192.0.2.1')
    saved = {'last_connected_robot': profile_key(original), 'robot_policy_path': 'private-policy.json'}
    catalog = ProfileCatalog([original])
    catalog.update(0, replace(original, name='Renamed'))
    updates = catalog.preference_updates(saved, 0)
    assert updates['last_connected_robot']['name'] == 'Renamed'
    catalog.update(0, replace(original, host='192.0.2.2'))
    assert catalog.preference_updates(saved, 0)['last_connected_robot'] is None
    catalog.remove(0)
    assert catalog.preference_updates(saved, None) == {'last_connected_robot': None, 'last_selected_robot': None}
    assert saved['robot_policy_path'] == 'private-policy.json'


def test_endpoint_signature_excludes_password_but_includes_transfer_settings():
    profile = RobotProfile('Robot', '192.0.2.1', password='session-only')
    assert profile_signature(profile) == profile_signature(replace(profile, password='different'))
    assert 'session-only' not in profile_signature(profile)
    for changes in ({'host': '192.0.2.2'}, {'port': 2121}, {'transport': 'sftp'}, {'roots': ['mc:']}, {'timeout': 60}):
        assert profile_signature(profile) != profile_signature(replace(profile, **changes))
