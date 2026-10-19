import json
from types import SimpleNamespace

from fanuc_workbench.core.models import RobotProfile
from fanuc_workbench.ui.program_diagnostics import ProgramDiagnosticsPanel
from fanuc_workbench import program_tools
from test_desktop_preferences import desktop
from test_program_logic import listing,observation


class Immediate:
    def start(self,worker):worker.run()


def owner(root):
    access=SimpleNamespace(profile=RobotProfile('Synthetic','192.0.2.1'))
    return SimpleNamespace(workspace=root,thread_pool=Immediate(),native_tab=SimpleNamespace(
        selected=lambda:'r1',bridge=SimpleNamespace(config=SimpleNamespace(robots={'r1':access}))))


def test_panel_uses_shared_trace_and_wait_services_without_mutating_inputs(desktop,tmp_path):
    path=listing(tmp_path);before=path.read_bytes()
    (tmp_path/'observations.json').write_text(json.dumps({'format':'fanuc-diagnostic-observations','schema_version':1,'observations':[observation(path)]}))
    panel=ProgramDiagnosticsPanel(owner(tmp_path));panel.paths.setText('MAIN.LS');panel.mode.setCurrentIndex(1);panel.analyze()
    assert not panel.busy and panel.result['items'][0]['token']=='DI[1]'
    panel.observation.setText('observations.json');panel.mode.setCurrentIndex(3);panel.analyze()
    assert panel.result['status']=='condition_unsatisfied_at_sample' and not panel.result['controller_contacted']
    assert path.read_bytes()==before
    panel.close()


def test_arming_native_capture_starts_no_polling_or_controller_calls(desktop,tmp_path):
    panel=ProgramDiagnosticsPanel(owner(tmp_path));panel.task_name.setText('T1');panel.capture_signals.setText('DI[1]')
    panel.arm_capture()
    assert panel.capture_session.armed and panel.capture_session.recorder.received==0
    assert panel.capture_session.source_id
    panel.disarm_capture();assert not panel.capture_session.armed and 'Disarmed' in panel.capture_label.text()
    panel.close()


def test_offline_cli_reports_trace_and_dependency_requirements(tmp_path,capsys):
    listing(tmp_path)
    result=program_tools.main(['--workspace',str(tmp_path),'trace','MAIN.LS','--view','accesses'])
    assert result['items'][0]['token']=='DI[1]'
    printed=json.loads(capsys.readouterr().out)
    assert printed==result
    result=program_tools.main(['--workspace',str(tmp_path),'dependencies','MAIN.LS','--entry','MAIN','--view','missing'])
    assert result['missing_count']==1 and result['items'][0]['resolution']=='missing_io_mapping'
