import copy
import math

import pytest

from fanuc_workbench.services.native_values import (fingerprint, program_name, propose_register,
                                                    real32, register_matches, register_target)


@pytest.mark.parametrize('target', ['R[0]', 'R[1,2]', 'PR[9,1]', 'R[1];RUN X', 'DO[1]', 'r[1]'])
def test_native_targets_are_explicit_and_narrow(target):
    with pytest.raises(ValueError):
        register_target(target)


def test_register_identity_and_real_precision_are_reviewable():
    assert register_target('PR[2,42]') == ('PR', 2, 42)
    assert register_target('R[999]') == ('R', 1, 999)
    before = {'kind': 'R', 'group': 1, 'index': 999, 'type': 'integer', 'value': 0}
    proposed = propose_register(before, {'type': 'real', 'value': 1.2})
    assert proposed['value'] == real32(1.2) != 1.2
    assert before['value'] == 0 and fingerprint(proposed) != fingerprint(before)
    assert not register_matches({**proposed, 'value': 1.2}, proposed)


@pytest.mark.parametrize('value', [True, 2**31, -(2**31)-1, 1.5, '1'])
def test_native_integer_validation(value):
    with pytest.raises(ValueError):
        propose_register({'kind': 'R', 'type': 'integer', 'value': 0}, {'type': 'integer', 'value': value})


@pytest.mark.parametrize('value', [True, float('inf'), float('nan'), 1e100, '1'])
def test_native_reals_are_finite_and_bounded(value):
    with pytest.raises(ValueError):
        real32(value)


def test_pr_proposals_preserve_all_metadata_and_use_declared_axis_tolerance():
    before = {'kind': 'PR', 'group': 2, 'index': 42, 'initialized': True, 'native_type': 9,
        'representation': 'joint', 'user_frame': 255, 'user_tool': 255, 'values': {'J1': 1.0, 'J2': 2.0}}
    original = copy.deepcopy(before)
    proposed = propose_register(before, {'values': {'J1': 10.0}})
    assert before == original and proposed['values'] == {'J1': 10.0, 'J2': 2.0}
    actual = {**proposed, 'values': {'J1': 10.000001, 'J2': 2.0}}
    assert register_matches(actual, proposed)
    assert not register_matches({**actual, 'user_frame': 0}, proposed)
    assert not register_matches({**actual, 'values': {'J1': 10.001, 'J2': 2.0}}, proposed)
    for change in [{'values': {'X': 1}}, {'values': {'J1': 2}, 'user_frame': 1}, {'values': {}}]:
        with pytest.raises(ValueError):
            propose_register(before, change)
    with pytest.raises(ValueError):
        propose_register({**before, 'initialized': False}, {'values': {'J1': 1}})


@pytest.mark.parametrize('name', ['../MAIN', 'MAIN.LS', 'TOO_LONG_NAME', 'DCS_TEST', 'lower', 'X;RUN'])
def test_program_targets_do_not_become_commands_or_paths(name):
    with pytest.raises(ValueError):
        program_name(name)
