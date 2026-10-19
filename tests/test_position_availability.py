import json

from fanuc_workbench.parsers.data import parse_position_registers
from fanuc_workbench.services.monitoring import WatchSpec, WorkspaceSampleProvider
from fanuc_workbench.services.workspace_api import WorkspaceService


def test_masked_and_external_axes_preserve_availability_and_config_boundaries(tmp_path):
    path = tmp_path / 'POSREG.VA'
    path.write_text("[2,7] = 'Fixture'\n  Group: 2 Config: F U T, 0, -1, 0\n"
                    "  X: ***** Y: 2.5 Z: -3.0\n  W: 0 P: 0 R: 90 E1: 100.0\n"
                    "[2,8] = 'Joint fixture'\n Group: 2\n J1 = ***** deg J2 = 2 deg E1 = 11 mm\n")
    cart, joint = parse_position_registers(path)
    assert cart.config == 'F U T, 0, -1, 0'
    assert cart.representation == 'Cartesian' and cart.unavailable_axes == ('X',)
    assert 'X' not in cart.values and cart.values['E1'] == 100.0
    assert joint.representation == 'Joint' and joint.unavailable_axes == ('J1',)
    assert joint.values == {'J2': 2.0, 'E1': 11.0}
    result = WorkspaceService.analyze(tmp_path, 'position_registers')
    assert result['items'][0]['unavailable_axes'] == ['X']
    json.dumps(result, allow_nan=False)


def test_duplicate_and_overflow_axes_are_unavailable(tmp_path):
    path = tmp_path / 'POSREG.VA'
    path.write_text("[1,1] = 'Ambiguous'\nX: 1 X: 2 Y: 1e999 Z: 3 W: NaN P: 1e R: ***\n")
    row = parse_position_registers(path)[0]
    assert row.unavailable_axes == ('P', 'R', 'W', 'X', 'Y')
    assert row.values == {'Z': 3.0}


def test_samples_refuse_partial_or_uninitialized_positions_and_invalid_numeric_values(tmp_path):
    (tmp_path / 'POSREG.VA').write_text("[1,1] = 'Masked'\nX: ***** Y: 1\n"
                                      "[1,2] = 'Empty' Uninitialized\n")
    (tmp_path / 'NUMREG.VA').write_text("[1] = ***** 'Masked'\n[2] = 1e9999 'Finite decimal'\n"
                                      "[3] = NaN 'Nonfinite'\n[4] = 42 'Count'\n")
    sample = WorkspaceSampleProvider(tmp_path).sample([WatchSpec(name) for name in
        ['PR[1]', 'PR[2]', 'R[1]', 'R[2]', 'R[3]', 'R[4]']])
    for name in ['PR[1]', 'PR[2]', 'R[1]', 'R[2]', 'R[3]']:
        assert sample.values[name] is None and name in sample.errors
    assert sample.values['R[4]'] == '42' and 'R[4]' not in sample.errors
