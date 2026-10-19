import asyncio
import json
import os
from pathlib import Path
import sys

from fanuc_workbench.bridge.extension_release import load_release
from fanuc_workbench.bridge.extensions_cli import install_builtin
from fanuc_workbench.extensions.programs_v1_1_0 import feature
from test_program_logic import listing,observation
from test_program_review import inventory


def test_same_mcp_session_reloads_all_six_capabilities_and_can_roll_back(tmp_path):
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    policy=tmp_path/'policy.json'
    policy.write_text(json.dumps({'state_dir':'state','source_dir':'edits','robots':{},'allow_writes':False}))
    fixture=tmp_path/'edits/fixture';fixture.mkdir(parents=True)
    path=listing(fixture,body='1: WAIT DI[1]=ON ;\n2: CALL CHILD ;')
    listing(fixture,'CHILD','1: END ;')
    (fixture/'before.LS').write_bytes(path.read_bytes())
    (fixture/'after.LS').write_bytes(path.read_bytes().replace(b'DI[1]',b'DI[2]'))
    inventory(fixture,mappings=['DI[1]'])
    rows=[observation(path,timestamp=1,value=False),observation(path,timestamp=2,value=True)]
    (fixture/'observations.json').write_text(json.dumps({'format':'fanuc-diagnostic-observations','schema_version':1,'observations':rows}))
    (fixture/'validation.json').write_text(json.dumps({'format':'fanuc-native-offline-validation','schema_version':1,'status':'compile_failed'}))
    assert feature.self_test()
    async def scenario():
        params=StdioServerParameters(command=sys.executable,args=['-B','-m','fanuc_workbench.bridge.mcp_server','--config',str(policy)],
            env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':str(Path(__file__).resolve().parents[1])})
        async with stdio_client(params) as (reader,writer),ClientSession(reader,writer) as session:
            await session.initialize()
            tools=[tool.model_dump(by_alias=True) for tool in (await session.list_tools()).tools]
            async def call(name,args):
                response=await session.call_tool(name,args)
                assert not response.is_error,response
                return json.loads(response.content[0].text)
            imported=await call('import_workspace',{'source_folder':'fixture'})
            staged=install_builtin(tmp_path/'state/extensions','program-logic')
            release,_=load_release(tmp_path/'state/extensions/releases/workbench.programs/1.1.0',staged['manifest_sha256'])
            assert len(release['operations'])==7
            assert not (await call('reload_extensions',{}))['mcp_tools_changed']
            async def operation(name,**arguments):
                desc=await call('describe_operation',{'operation_id':'workbench.programs.'+name})
                args={'operation_id':desc['id'],'revision':desc['revision'],'arguments':{'artifact_id':imported['artifact_id'],**arguments}}
                return (await call('run_read_operation',args))['result'],args
            result,request=await operation('trace',paths=['MAIN.LS'],view='accesses')
            assert result['items'][0]['token']=='DI[1]'
            result,_=await operation('explain_wait',path='MAIN.LS',observation_path='observations.json',observation_index=0)
            assert result['status']=='condition_unsatisfied_at_sample'
            result,_=await operation('triggered_captures',paths=['MAIN.LS'],observation_path='observations.json',signals=['DI[1]'],before=1,after=1)
            assert result['total']==1 and not result['items'][0]['post_complete']
            result,_=await operation('semantic_compare',before_path='before.LS',after_path='after.LS')
            assert result['category_counts']=={'instruction':1}
            result,_=await operation('dependency_review',paths=['MAIN.LS','CHILD.LS'],entry_programs=['MAIN'],inventory_path='inventory.json')
            assert result['explicit_dependencies_accounted_for']
            result,_=await operation('review_validation',source_path='MAIN.LS',report_path='validation.json')
            assert not result['preserved_checks_pass']
            result,_=await operation('dependencies',paths=['MAIN.LS','CHILD.LS'],entry_programs=['MAIN'])
            assert result['reachable_programs']==2
            assert len(tools)==48 and [tool.model_dump(by_alias=True) for tool in (await session.list_tools()).tools]==tools
            assert (await session.call_tool('run_read_operation',{**request,'revision':'stale'})).is_error
            assert (await session.call_tool('run_read_operation',{**request,'arguments':{**request['arguments'],'paths':['../MAIN.LS']}})).is_error
            install_builtin(tmp_path/'state/extensions','programs')
            await call('reload_extensions',{})
            assert (await session.call_tool('describe_operation',{'operation_id':'workbench.programs.trace'})).is_error
            assert [tool.model_dump(by_alias=True) for tool in (await session.list_tools()).tools]==tools
    asyncio.run(scenario())
