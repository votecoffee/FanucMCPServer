import asyncio
import json
import os
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize('supports_confirmation', [False, True])
def test_native_commit_requires_client_confirmation_and_exact_prepared_hash(tmp_path, supports_confirmation):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.types import ElicitResult
    repo = Path(__file__).resolve().parents[1]
    config = tmp_path / 'bridge.json'
    config.write_text(json.dumps({'state_dir': 'state', 'source_dir': 'edits', 'allow_writes': True,
        'approval_mode': 'elicitation', 'robots': {'r1': {'profile': {'host': '192.0.2.1'},
            'native_provider': 'frrobot', 'native_register_targets': ['R[999]']}}}))
    script = tmp_path / 'server.py'
    script.write_text('''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[3])
from test_robot_bridge import MemoryRobot
from test_native_workflow import FakeNative
from fanuc_workbench.bridge.service import RobotBridge
from fanuc_workbench.bridge.mcp_server import create_server
class Fake(FakeNative):
    def call(self, operation, **args):
        result = super().call(operation, **args)
        if operation == 'write_register': Path(sys.argv[2]).write_text('native write')
        return result
backend=MemoryRobot()
with RobotBridge.from_config(Path(sys.argv[1]), lambda _: backend) as bridge:
    api=Fake(backend)
    bridge.native.factory=lambda profile: api
    create_server(bridge).run(transport='stdio')
''')
    marker = tmp_path / 'written'
    async def scenario():
        decisions, messages = iter([False, True]), []
        async def confirm(context, params):
            messages.append(params.message)
            return ElicitResult(action='accept', content={'approve': next(decisions)})
        options = {'elicitation_callback': confirm} if supports_confirmation else {}
        params = StdioServerParameters(command=sys.executable,
            args=['-B', str(script), str(config), str(marker), str(repo / 'tests')],
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(repo)})
        async with stdio_client(params) as (r, w), ClientSession(r, w, **options) as session:
            await session.initialize()
            async def call(name, args):
                result = await session.call_tool(name, args)
                assert not result.is_error, result
                return json.loads(result.content[0].text)
            op = await call('describe_operation', {'operation_id': 'core.registers.read'})
            captured = (await call('run_read_operation', {'operation_id': op['id'], 'revision': op['revision'],
                'arguments': {'robot_id': 'r1', 'target': 'R[999]'}}))['result']
            op = await call('describe_operation', {'operation_id': 'core.registers.update'})
            plan = await call('prepare_change', {'operation_id': op['id'], 'revision': op['revision'],
                'arguments': {'robot_id': 'r1', 'target': 'R[999]', 'expected_sha256': captured['sha256'],
                'change': {'type': 'integer', 'value': 1}}})
            args = {'plan_id': plan['plan_id'], 'approved_sha256': plan['proposed_sha256']}
            if supports_confirmation:
                denied = await session.call_tool('commit_change', args)
                assert denied.is_error
            else:
                from mcp.shared.exceptions import MCPError
                with pytest.raises(MCPError, match='Elicitation not supported'):
                    await session.call_tool('commit_change', args)
            assert not marker.exists()
            if supports_confirmation:
                accepted = await call('commit_change', args)
                assert accepted['status'] == 'verified' and marker.exists()
                assert all('R[999]' in message and plan['proposed_sha256'] in message for message in messages)
    asyncio.run(scenario())
