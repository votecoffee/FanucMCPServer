from __future__ import annotations
import json,time
from datetime import datetime,timedelta
from pathlib import Path
from fanuc_workbench.core.models import RobotProfile
from fanuc_workbench.backends.factory import create_backend
from fanuc_workbench.backends.sftp import FanucSftpBackend
from fanuc_workbench.services.monitoring import WatchSpec,WorkspaceSampleProvider,TrendRecorder,TimelineRecorder,CycleRecorder,CycleEvent,profile_cycle,compare_cycle_profiles
from fanuc_workbench.services.commissioning import create_baseline,compare_baseline,load_checklist,save_checklist
from fanuc_workbench.services.robot_lint import lint_workspace
from fanuc_workbench.services.inventory import option_inventory
from fanuc_workbench.services.ethernetip import scan_ethernetip
from fanuc_workbench.services.handshake import HandshakeSignal,analyze_handshake
from fanuc_workbench.services.transforms import transform_pose_between_frames
from fanuc_workbench.services.roboguide import stage_backup_for_roboguide
from fanuc_workbench.services.snapshots import capture_snapshot,compare_snapshots
from fanuc_workbench.services.fleet import FleetManager,FleetRobot
from fanuc_workbench.plugins.sdk import SDK_API_VERSION,PluginManifest,ServiceRegistry

def make_ws(tmp_path:Path):
    root=tmp_path/'ws';root.mkdir()
    (root/'NUMREG.VA').write_text("[1] = 42 'Part Count'\n[2] = 3 'Recipe'\n")
    (root/'POSREG.VA').write_text("[1,1] = 'Pick'\nUF: 3, UT: 2, X: 100.0, Y: 200.0, Z: 300.0, W: 0.0, P: 0.0, R: 90.0\n")
    (root/'IOSTATE.DG').write_text('DIN[1] ON PartPresent\nDOUT[1] OFF Clamp\n')
    (root/'MAIN.LS').write_text('/PROG MAIN\n/ATTR\nCOMMENT = "Main";\n/MN\n 1: CALL MISSING ;\n 2: UFRAME_NUM=3 ;\n 3: JMP LBL[55] ;\n/POS\n/END\n')
    (root/'EIP.DG').write_text('EtherNet/IP Adapter CONNECTION 1 IP=192.168.1.10 SLOT=1 INPUT SIZE=32 OUTPUT SIZE=32 RPI=32 ms RUNNING\nKAREL option active\nOPC UA enabled\n')
    (root/'backup_manifest.json').write_text(json.dumps({'robot_name':'R1','host':'192.168.1.10','created_at':'2026-08-13T08:00:00','roots':['md:'],'records':[]}))
    return root

def test_workspace_watch_and_recorders(tmp_path):
    root=make_ws(tmp_path);p=WorkspaceSampleProvider(root);s1=p.sample([WatchSpec('R[1]','count'),WatchSpec('PR[1]','pick'),WatchSpec('DI[1]','present')])
    assert s1.values['count']=='42';assert s1.values['pick']['X']==100.0;assert s1.values['present']=='ON'
    tr=TrendRecorder();tr.add(s1);csvp=tmp_path/'trend.csv';tr.export_csv(csvp);assert 'count' in csvp.read_text()
    tl=TimelineRecorder();tl.add(s1);root.joinpath('IOSTATE.DG').write_text('DIN[1] OFF PartPresent\nDOUT[1] OFF Clamp\n');s2=p.sample([WatchSpec('DI[1]','present')]);tl.add(s2);assert tl.events[-1].new=='OFF'

def test_cycle_profile_compare():
    a=[CycleEvent(0,'START'),CycleEvent(1,'PICK'),CycleEvent(3,'PLACE'),CycleEvent(6,'END')]
    b=[CycleEvent(0,'START'),CycleEvent(1,'PICK'),CycleEvent(4,'PLACE'),CycleEvent(8,'END')]
    pa,pb=profile_cycle(a),profile_cycle(b);assert pa['PICK']==2;rows=dict((k,d) for k,x,y,d in compare_cycle_profiles(pa,pb));assert rows['PICK']==1

def test_baseline_checklist_and_lint(tmp_path):
    root=make_ws(tmp_path);base=create_baseline(root,tmp_path/'baselines');assert base.is_dir();root.joinpath('NUMREG.VA').write_text("[1] = 99 'Part Count'\n");assert compare_baseline(base,root)
    items=load_checklist(root/'no.json');items[0].done=True;save_checklist(root/'check.json',items);assert load_checklist(root/'check.json')[0].done
    issues=lint_workspace(root);assert any(x.code=='MISSING_CALL' for x in issues);assert any(x.code=='MISSING_LABEL' for x in issues)

def test_inventory_and_eip(tmp_path):
    root=make_ws(tmp_path);inv=option_inventory(root);assert next(x for x in inv if x.option=='KAREL').status=='Evidence found';rows=scan_ethernetip(root);assert rows and rows[0].role=='Adapter' and rows[0].ip=='192.168.1.10' and rows[0].input_size=='32'

def test_handshake_analyzer():
    from fanuc_workbench.services.monitoring import TimelineEvent
    rows=[HandshakeSignal('Start/Ack','DI1','','PLC→Robot','DO1')];events=[TimelineEvent(10,'DI1','OFF','ON'),TimelineEvent(11,'DO1','OFF','ON')];result=analyze_handshake(events,rows);assert result[0][2]=='OK'

def test_frame_transform_roundtrip():
    p=[100,50,20,10,5,30];old=[200,0,0,0,0,90];new=[0,300,0,0,10,0]
    q=transform_pose_between_frames(p,old,new);back=transform_pose_between_frames(q,new,old)
    for x,y in zip(p,back):assert abs(x-y)<1e-6

def test_sftp_factory_and_roboguide_stage(tmp_path):
    prof=RobotProfile('R','1.2.3.4',port=22,transport='sftp');assert isinstance(create_backend(prof),FanucSftpBackend)
    root=make_ws(tmp_path);out=stage_backup_for_roboguide(root,tmp_path/'stage');assert (out/'roboguide_handoff.json').exists() and (out/'MAIN.LS').exists()

def test_snapshot_compare(tmp_path):
    root=make_ws(tmp_path);a=capture_snapshot(root,tmp_path/'snaps');time.sleep(1.05);root.joinpath('NUMREG.VA').write_text("[1] = 42 'Production Count'\n");b=capture_snapshot(root,tmp_path/'snaps');ch=compare_snapshots(a,b);assert any('comment' in x[0] for x in ch)

def test_fleet_due_and_sdk(tmp_path):
    mgr=FleetManager(tmp_path/'fleet.json');fr=FleetRobot(RobotProfile('R','host'),interval_minutes=1);assert mgr.due(fr)
    reg=ServiceRegistry();reg.register('x',123);assert reg.get('x')==123;assert PluginManifest('id','Title').api_version==SDK_API_VERSION
