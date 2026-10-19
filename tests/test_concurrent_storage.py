import json
import os
import subprocess
import sys
import time

from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.service import RobotBridge
from fanuc_workbench.bridge.storage import RobotLocks


def test_controller_lock_is_exclusive_across_processes_with_a_bounded_wait(tmp_path):
    code='''
from pathlib import Path
import sys
from fanuc_workbench.bridge.storage import RobotBusyError,RobotLocks
try:
    with RobotLocks(Path(sys.argv[1])).acquire('test-controller',timeout=.05):
        raise AssertionError('Controller lock was acquired by two processes')
except RobotBusyError:
    print('busy')
'''
    with RobotLocks(tmp_path/'locks').acquire('test-controller'):
        result=subprocess.run([sys.executable,'-B','-c',code,str(tmp_path/'locks')],
            capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert result.stdout.strip()=='busy'


def test_concurrent_process_audits_remain_complete_and_readable(tmp_path):
    code='''
from pathlib import Path
import sys
from fanuc_workbench.bridge.config import BridgeConfig
from fanuc_workbench.bridge.service import RobotBridge
root=Path(sys.argv[1])
with RobotBridge(BridgeConfig(root/'state',root/'edits')) as bridge:
    for index in range(5):
        bridge._audit('synthetic_event',writer=sys.argv[2],index=index,payload='x'*100000)
'''
    processes=[subprocess.Popen([sys.executable,'-B','-c',code,str(tmp_path),name],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}) for name in ['first','second']]
    try:
        with RobotBridge(BridgeConfig(tmp_path/'state',tmp_path/'edits')) as reader:
            deadline=time.monotonic()+15
            while any(process.poll() is None for process in processes):
                assert time.monotonic()<deadline
                assert all(len(row['payload'])==100000 for row in reader.operations.audit(limit=20)['items'])
                time.sleep(.01)
        for process in processes:
            _,errors=process.communicate(timeout=3)
            assert process.returncode==0,errors
    finally:
        for process in processes:
            if process.poll() is None:process.kill();process.communicate(timeout=3)
    rows=[json.loads(line) for line in (tmp_path/'state/audit.jsonl').read_text().splitlines()]
    assert len(rows)==10
    assert {(row['writer'],row['index']) for row in rows}=={(name,index) for name in ['first','second'] for index in range(5)}
