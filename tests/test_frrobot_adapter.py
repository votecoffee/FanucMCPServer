import copy

import pytest

from fanuc_workbench.services.frrobot import ComBinding, FrRobot, VendorCallError


class Binding:
    def __init__(self):
        self.puts, self.calls = [], []
        self.axes = {'Count': 2, 'axes': {1: 1.0, 2: 2.0}}
        self.position = {'IsInitialized': True, 'Type': 9, 'UserFrame': 255, 'UserTool': 255, 'Formats': self.axes}
        self.numeric = {'Type': 16, 'RegLong': 0}
        self.robot = {'IsConnected': True, 'IsOffline': False, 'HostName': '192.0.2.1',
            'RegNumerics': {'Item': {'Value': self.numeric}},
            'RegPositions': {'Item': {'Group': self.position}}, 'Programs': [], 'Tasks': []}

    def get(self, obj, name, *args):
        if name == 'Item' and 'axes' in obj:
            return obj['axes'][args[0]]
        return obj[name]

    def put(self, obj, name, value, *indexes):
        self.puts.append((name, value, indexes))
        if indexes:
            obj['axes'][indexes[0]] = value
        else:
            obj[name] = value
        if name == 'RegFloat':
            obj['Type'] = 17
        elif name == 'RegLong':
            obj['Type'] = 16

    def call(self, obj, name, *args):
        self.calls.append(name)

    def items(self, obj, maximum):
        return obj


def test_native_reads_do_not_set_any_vendor_property():
    binding = Binding()
    api = FrRobot('192.0.2.1', binding=binding, robot=binding.robot)
    assert api.read_register('R[999]')['value'] == 0
    assert api.read_register('PR[1,100]')['values'] == {'J1': 1.0, 'J2': 2.0}
    assert binding.puts == [] and set(binding.calls) == {'Refresh'}


def test_native_r_write_uses_typed_property_and_rechecks_original():
    binding = Binding()
    api = FrRobot('192.0.2.1', binding=binding, robot=binding.robot)
    before = api.read_register('R[999]')
    proposed = {**before, 'value': 1}
    assert api.write_register('R[999]', before, proposed)['status'] == 'verified'
    assert binding.puts == [('RegLong', 1, ())]
    with pytest.raises(ValueError, match='changed'):
        api.write_register('R[999]', before, proposed)
    assert len(binding.puts) == 1


def test_native_pr_write_stages_only_changed_axes_and_updates_once():
    binding = Binding()
    # Joint readings can contain converted values with more precision than REAL32.
    binding.axes['axes'][2] = 9.62761011158101
    api = FrRobot('192.0.2.1', binding=binding, robot=binding.robot)
    before = api.read_register('PR[1,100]')
    proposed = {**before, 'values': {'J1': 5.0, 'J2': before['values']['J2']}}
    assert api.write_register('PR[1,100]', before, proposed)['status'] == 'verified'
    assert [name for name, *_ in binding.puts] == ['AutomaticUpdate', 'Item']
    assert binding.calls.count('Update') == 1
    assert binding.position['UserFrame'] == binding.position['UserTool'] == 255


def test_native_loader_refuses_active_tp_tasks_without_calling_load():
    binding = Binding()
    binding.robot['Tasks'] = [{'Status': 0, 'Name': 'MAIN', 'ProgramType': 1, 'CurLine': 1,
                               'CurProgram': {'Name': 'MAIN'}}]
    api = FrRobot('192.0.2.1', binding=binding, robot=binding.robot)
    with pytest.raises(ValueError, match='active or paused'):
        api.load_program('frozen.LS', 'FWBTEST', None)
    assert 'Load' not in binding.calls and not binding.puts


def test_vendor_failure_preserves_numeric_call_diagnostics_without_vendor_text():
    class Cause(Exception):
        hresult = -2147352567
        excepinfo = (0, 'vendor', 'sensitive controller text', '', 0, -123)
    class Ole:
        def GetIDsOfNames(self, name):
            return 42
        def Invoke(self, *args):
            raise Cause('sensitive controller text')
    binding = object.__new__(ComBinding)
    class Obj:
        _oleobj_ = Ole()
    with pytest.raises(VendorCallError) as caught:
        binding._invoke(Obj(), 'Update', 'call', 1, True)
    assert caught.value.details == {'operation': 'call', 'member': 'Update',
        'hresult': -2147352567, 'vendor_code': -123}
    assert 'sensitive' not in str(caught.value) + str(caught.value.details)
