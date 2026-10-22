import asyncio
import json
import os
from pathlib import Path
import sys

import pytest

from test_robot_bridge import ORIGINAL,PROPOSED,digest


@pytest.mark.parametrize('approve_both',[True,False])
def test_concurrent_mcp_approvals_remain_bound_to_their_plans(tmp_path,approve_both):
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.types import ElicitResult
    source=tmp_path/'edits';source.mkdir();(source/'MAIN.LS').write_bytes(PROPOSED)
    policy=tmp_path/'policy.json'
    policy.write_text(json.dumps({'state_dir':'state','source_dir':'edits','allow_writes':True,
        'approval_mode':'elicitation','robots':{name:{'profile':{'host':host},'writable_files':['md:MAIN.LS']}
        for name,host in [('r1','192.0.2.1'),('r2','192.0.2.2')]}}))
    program=tmp_path/'server.py'
    program.write_text('''
from pathlib import Path
import sys,threading
from fanuc_workbench.bridge.mcp_server import create_server
from fanuc_workbench.bridge.service import RobotBridge
from test_robot_bridge import MemoryRobot
robots={host:MemoryRobot() for host in ['192.0.2.1','192.0.2.2']}
if sys.argv[2]=='both':
    barrier=threading.Barrier(2)
    for robot in robots.values():
        original=robot.upload
        def upload(local,remote,original=original):
            barrier.wait(timeout=3)
            original(local,remote)
        robot.upload=upload
with RobotBridge.from_config(Path(sys.argv[1]),lambda profile:robots[profile.host]) as bridge:
    create_server(bridge).run(transport='stdio')
''')
    async def scenario():
        messages=[]
        async def confirm(ctx,params):
            name='r1' if "'robot_id': 'r1'" in params.message else 'r2'
            assert "'robot_id': '"+name+"'" in params.message
            assert digest(PROPOSED) in params.message
            messages.append(name)
            return ElicitResult(action='accept',content={'approve':approve_both or name=='r1'})
        repo=Path(__file__).resolve().parents[1]
        params=StdioServerParameters(command=sys.executable,args=['-B',str(program),str(policy),'both' if approve_both else 'one'],
            env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':os.pathsep.join([str(repo),str(repo/'tests')])})
        async with stdio_client(params) as (reader,writer),ClientSession(reader,writer,elicitation_callback=confirm) as session:
            await session.initialize()
            before=[tool.model_dump(by_alias=True) for tool in (await session.list_tools()).tools]
            plans=[]
            for name in ('r1','r2'):
                result=await session.call_tool('prepare_upload',{'robot_id':name,'path':'md:MAIN.LS',
                    'source_file':'MAIN.LS','expected_sha256':digest(ORIGINAL)})
                assert not result.is_error
                plans.append(json.loads(result.content[0].text))
            results=await asyncio.gather(*(session.call_tool('commit_change',{
                'plan_id':plan['plan_id'],'approved_sha256':plan['proposed_sha256']}) for plan in plans))
            assert not results[0].is_error
            assert results[1].is_error is (not approve_both)
            assert sorted(messages)==['r1','r2']
            for name in ('r1','r2'):
                result=await session.call_tool('read_file',{'robot_id':name,'path':'md:MAIN.LS'})
                assert not result.is_error
                expected=PROPOSED if approve_both or name=='r1' else ORIGINAL
                assert json.loads(result.content[0].text)['sha256']==digest(expected)
            described=await session.call_tool('describe_operation',{'operation_id':'core.requests.status'})
            descriptor=json.loads(described.content[0].text)
            status=await session.call_tool('run_read_operation',{'operation_id':descriptor['id'],
                'revision':descriptor['revision'],'arguments':{}})
            assert not status.is_error and json.loads(status.content[0].text)['result']['recent']
            assert before==[tool.model_dump(by_alias=True) for tool in (await session.list_tools()).tools]
            assert len(before)==55
    asyncio.run(scenario())
    audit=[json.loads(line) for line in (tmp_path/'state/audit.jsonl').read_text().splitlines()]
    writes=[row for row in audit if row['action']=='upload_started']
    assert {row['robot_id'] for row in writes}==({'r1','r2'} if approve_both else {'r1'})
    assert all(len(row['request_id'])==32 for row in writes)
