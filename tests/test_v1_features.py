from pathlib import Path
import json

from fanuc_workbench.parsers.data import parse_numeric_registers, parse_position_registers, set_numeric_register_value, set_position_register
from fanuc_workbench.services.compare import compare_workspaces
from fanuc_workbench.services.xref import build_xref, program_dependencies, unused_programs, register_usage, validate_ls
from fanuc_workbench.services.restore import restore_candidates
from fanuc_workbench.services.project import RobotProject, ProjectRobot
from fanuc_workbench.services.diagnostics import scan_alarms, mastering_evidence, dcs_evidence
from fanuc_workbench.services.documentation import export_all


def make_ws(root: Path, variant=1):
    root.mkdir(parents=True, exist_ok=True)
    (root/'MAIN.LS').write_text(
        '/PROG MAIN\n/ATTR\nCOMMENT = "Main";\n/MN\n'
        '  1: R[1]=R[2]+1 ;\n  2: DO[1]=ON ;\n  3: CALL PICK ;\n  4: UFRAME_NUM=3 ;\n'
        '/POS\nP[1:"HOME"]{\n GP1:\n UF : 3, UT : 2, CONFIG : \'F U T, 0, 0, 0\',\n X = 1.000 mm, Y = 2.000 mm, Z = 3.000 mm, W = 0 deg, P = 0 deg, R = 0 deg\n};\n/END\n', encoding='ascii')
    (root/'PICK.LS').write_text('/PROG PICK\n/ATTR\nCOMMENT = "Pick";\n/MN\n 1: PR[1]=LPOS ;\n/POS\n/END\n',encoding='ascii')
    (root/'OLD.LS').write_text('/PROG OLD\n/ATTR\nCOMMENT = "Old";\n/MN\n/POS\n/END\n',encoding='ascii')
    (root/'NUMREG.VA').write_text(f"[1] = {10+variant} 'Count'\n[2] = 2 'Addend'\n",encoding='ascii')
    (root/'POSREG.VA').write_text("[1,1] = 'Pick pos'\n Group: 1 UF: 3 UT: 2 Config: F U T, 0, 0, 0\n X: 100.000 Y: 200.000 Z: 300.000\n W: 0.000 P: 0.000 R: 90.000\n",encoding='ascii')
    (root/'FRAME.DG').write_text('Tool Frame\n1 2 3 4 5 6 TCP\nUser Frame\n10 20 30 0 0 0 Fixture\n',encoding='ascii')
    (root/'IOSTATE.DG').write_text('DIN[1] OFF Part Present\nDOUT[1] ON Clamp Cmd\n',encoding='ascii')
    (root/'ERRHIST.LS').write_text('2026/08/13 07:00:00 SRVO-105 Door open\n',encoding='ascii')
    (root/'MASTER.DG').write_text('$DMR_GRP[1].$MASTER_COUN = 12345\n',encoding='ascii')
    (root/'DCSCFG.DG').write_text('DCS SIGNATURE 1234\nCPC zone 1\n',encoding='ascii')
    (root/'MAIN.TP').write_bytes(b'fake-tp')
    (root/'NUMREG.VR').write_bytes(b'fake-vr')
    (root/'POSREG.VR').write_bytes(b'fake-vr')
    manifest={'robot_name':'R01','host':'10.0.0.1','created_at':'2026-08-13T07:00:00','roots':['md:'],'records':[
        {'remote_path':'md:MAIN.TP','local_path':str(root/'MAIN.TP')}, {'remote_path':'md:NUMREG.VR','local_path':str(root/'NUMREG.VR')},
        {'remote_path':'md:POSREG.VR','local_path':str(root/'POSREG.VR')} ]}
    (root/'backup_manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    return root


def test_data_editors(tmp_path):
    ws=make_ws(tmp_path/'ws')
    nr=parse_numeric_registers(ws/'NUMREG.VA')[0]; nr.comment='Count'; set_numeric_register_value(nr,'99')
    assert parse_numeric_registers(ws/'NUMREG.VA')[0].value=='99'
    pr=parse_position_registers(ws/'POSREG.VA')[0]
    assert pr.values['X']==100.0 and pr.user_frame=='3'
    set_position_register(pr,comment='New Pick',values={'X':123.456},user_frame='4')
    pr2=parse_position_registers(ws/'POSREG.VA')[0]
    assert pr2.comment=='New Pick' and pr2.values['X']==123.456 and pr2.user_frame=='4'


def test_compare_and_analysis(tmp_path):
    a=make_ws(tmp_path/'a',1); b=make_ws(tmp_path/'b',2)
    changes=compare_workspaces(a,b)
    assert any(c.relative_path.endswith('NUMREG.VA') and c.status=='Modified' for c in changes)
    refs=build_xref(a); assert any(r.token=='R[1]' for r in refs); assert any(r.token=='DO[1]' for r in refs)
    graph=program_dependencies(a); assert graph['MAIN']=={'PICK'}
    assert 'OLD' in unused_programs(a)
    usage=register_usage(a); assert usage['R[1]']['Write']>=1
    assert validate_ls(a/'MAIN.LS',a)==[]


def test_restore_project_diagnostics_docs(tmp_path):
    ws=make_ws(tmp_path/'ws')
    rows=restore_candidates(ws)
    assert next(r for r in rows if r.path.name=='MAIN.TP').default_selected
    assert not next(r for r in rows if r.path.name=='NUMREG.VA').default_selected
    project=RobotProject('Cell',tmp_path/'proj'); project.save(); project.upsert_robot(ProjectRobot('R01','10.0.0.1')); copied=project.add_backup('R01',ws)
    assert copied.exists() and RobotProject.load(project.path).robots[0].backups
    package=project.deployment_package(copied,tmp_path/'packages'); assert (package/'deployment_manifest.json').exists()
    assert scan_alarms(ws)[0].code=='SRVO-105'; assert mastering_evidence(ws); assert dcs_evidence(ws)
    out=tmp_path/'reports'; generated=export_all(ws,out,'test')
    assert {p.suffix for p in generated}=={'.html','.xlsx','.pdf'} and all(p.stat().st_size>100 for p in generated)

def test_semantic_ls_diff_ignores_tp_line_numbers(tmp_path):
    a=tmp_path/'a'; b=tmp_path/'b'; a.mkdir(); b.mkdir()
    (a/'P.LS').write_text('/PROG P\n/ATTR\nDATE = 01-01-24;\n/MN\n  1: R[1]=1 ;\n/POS\n/END\n',encoding='ascii')
    (b/'P.LS').write_text('/PROG P\n/ATTR\nDATE = 08-13-26;\n/MN\n 99: R[1]=1 ;\n/POS\n/END\n',encoding='ascii')
    rows=compare_workspaces(a,b)
    assert len(rows)==1 and rows[0].status=='Metadata-only' and rows[0].semantic


def test_joint_position_register(tmp_path):
    p=tmp_path/'POSREG.VA'
    p.write_text("[1,7] = 'Joint pose'\n Group: 1\n J1: 1.000 J2: 2.000 J3: 3.000 J4: 4.000 J5: 5.000 J6: 6.000\n",encoding='ascii')
    row=parse_position_registers(p)[0]
    assert row.representation=='Joint' and row.values['J6']==6.0
    set_position_register(row,values={'J2':22.5})
    assert parse_position_registers(p)[0].values['J2']==22.5
