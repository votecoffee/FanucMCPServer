import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from fanuc_workbench.bridge.extension_release import load_registry, load_release
from fanuc_workbench.bridge.extensions_cli import install_builtin
from fanuc_workbench.extensions.programs import feature
from fanuc_workbench.services.program_analysis import analyze_dependencies


def listing(root, path, name, statements='', encoding='utf-8'):
    selected = root / path
    selected.parent.mkdir(parents=True, exist_ok=True)
    text = f'/PROG {name}\n/ATTR\nCOMMENT = "CALL FALSE";\n/MN\n{statements}\n/POS\n/END\n'
    selected.write_bytes(text.encode(encoding))
    return selected


def analyze(root, paths, **options):
    return analyze_dependencies(root, paths, **{'entry_programs': [], **options})


def test_header_names_drive_resolution_explicit_entries_and_cycle_reachability(tmp_path):
    first = listing(tmp_path, 'different.LS', 'MAIN', '1: CALL CHILD;\n2: RUN CHILD;\n3: CALL ABSENT;')
    second = listing(tmp_path, 'sub/other.ls', 'CHILD', '1: CALL MAIN;')
    listing(tmp_path, 'unused.LS', 'SPARE')
    original = [first.read_bytes(), second.read_bytes()]
    result = analyze(tmp_path, ['unused.LS', 'sub/other.ls', 'different.LS'], entry_programs=['main'])
    rows = {row['program']: row for row in result['items']}
    assert rows['MAIN']['reachable_in_static_graph'] and rows['CHILD']['reachable_in_static_graph']
    assert rows['SPARE']['reachable_in_static_graph'] is False
    assert rows['MAIN']['outgoing_resolved_targets'] == 1 and rows['CHILD']['incoming_resolved_call_sites'] == 2
    assert result['static_graph_edges'] == 2 and result['reference_count'] == 4 and result['reachable_programs'] == 2
    assert result['resolution_counts'] == {'resolved': 3, 'not_in_selection': 1}
    assert rows['MAIN']['source_sha256'] == hashlib.sha256(original[0]).hexdigest()
    assert first.read_bytes() == original[0] and second.read_bytes() == original[1]
    assert not result['runtime_reachability_verified'] and not result['dependency_completeness_verified']
    assert not result['compiler_validation_performed'] and not result['controller_contacted']


@pytest.mark.parametrize('statement,target,kind', [
    ('CALL child;', 'CHILD', 'direct'), ('run child;', 'CHILD', 'direct'),
    ('IF R[1:CALL FALSE]=1,CALL CHILD;', 'CHILD', 'direct'),
    ('IF (R[1]=1),RUN CHILD;', 'CHILD', 'direct'),
    ('CALL CHILD(R[1],PR[1,2],\'CALL FALSE\');', 'CHILD', 'direct'),
    ('CALL RUN;', 'RUN', 'direct'), ('CALL CHILD; ! CALL FALSE', 'CHILD', 'direct'),
    ('CALL SR[1:CALL FALSE];', None, 'dynamic'), ('CALL SR[1](R[2]);', None, 'dynamic'),
    ('CALL CHILD.EXTRA;', None, 'unsupported_target'), ('CALL CHILD OTHER;', None, 'unsupported_target'),
    ('CALL "FALSE" CHILD;', None, 'unsupported_target'), ('CALL CHILD"FALSE";', None, 'unsupported_target'),
    ('SELECT R[1]=1,CALL CHILD;', None, 'unsupported_context'),
])
def test_supported_static_subset_and_unresolved_forms(tmp_path, statement, target, kind):
    listing(tmp_path, 'test.LS', 'TEST', '1: ' + statement)
    result = analyze(tmp_path, ['test.LS'], view='references')
    assert result['total'] == 1
    row = result['items'][0]
    assert row['target'] == target and row['target_kind'] == kind
    assert row['line_number'] == 5 and row['tp_line_number'] == 1 and row['source'] == '1: ' + statement


@pytest.mark.parametrize('statement', [
    '! CALL FALSE;', 'MESSAGE[\'CALL FALSE\'];', 'R[1:CALL FALSE]=1;',
    'SR[1]=\'CALL FALSE\';', 'MESSAGE["RUN FALSE"];', 'MESSAGE[\'It\'\'s CALL FALSE\'];',
])
def test_comments_strings_and_bracket_descriptions_do_not_create_dependencies(tmp_path, statement):
    listing(tmp_path, 'test.LS', 'TEST', '1: ' + statement)
    assert analyze(tmp_path, ['test.LS'])['reference_count'] == 0


@pytest.mark.parametrize('statement,issue', [
    ('CALL CHILD', 'numbered_statement_required'),
    ('1: CALL CHILD', 'single_terminated_statement_required'),
    ('1: CALL CHILD; CALL OTHER;', 'single_terminated_statement_required'),
    ('1: CALL CHILD( R[1];', 'lexical_structure_unsupported'),
    ('1: MESSAGE[\'CALL FALSE];', 'lexical_structure_unsupported'),
    ('1: R[1)=1;', 'lexical_structure_unsupported'),
])
def test_unsupported_structure_is_explicit_instead_of_creating_partial_edges(tmp_path, statement, issue):
    listing(tmp_path, 'test.LS', 'TEST', statement)
    result = analyze(tmp_path, ['test.LS'], view='issues')
    assert result['reference_count'] == 0 and result['issue_counts'] == {issue: 1}
    assert result['items'][0]['line_number'] == 5 and result['items'][0]['source'] == statement


def test_duplicate_and_excluded_definitions_cannot_be_resolved_or_used_as_entries(tmp_path):
    listing(tmp_path, 'main.LS', 'MAIN', '1: CALL CHILD;\n2: CALL BROKEN;')
    listing(tmp_path, 'one.LS', 'CHILD')
    listing(tmp_path, 'two.LS', 'child')
    (tmp_path / 'broken.LS').write_text('/PROG BROKEN\n/MN\n1: CALL MAIN;\n')
    paths = ['main.LS', 'one.LS', 'two.LS', 'broken.LS']
    result = analyze(tmp_path, paths, entry_programs=['MAIN'], view='references')
    rows = {row['target']: row for row in result['items']}
    assert rows['CHILD']['resolution'] == 'ambiguous' and rows['CHILD']['matching_definitions'] == 2
    assert rows['BROKEN']['resolution'] == 'excluded_definition'
    assert rows['MAIN']['resolution'] == 'resolved' and not rows['MAIN']['included_in_static_graph']
    assert result['static_graph_edges'] == 0 and result['ambiguous_program_names'] == 1 and result['excluded_files'] == 1
    for name in ['CHILD', 'BROKEN', 'ABSENT']:
        with pytest.raises(ValueError, match='entry'):
            analyze(tmp_path, paths, entry_programs=[name])
    # A malformed duplicate still makes a matching name ambiguous.
    listing(tmp_path, 'valid.LS', 'BROKEN')
    assert analyze(tmp_path, paths + ['valid.LS'], view='references')['resolution_counts']['ambiguous'] == 2


@pytest.mark.parametrize('text', [
    '/MN\n1: CALL CHILD;\n/END', '/PROG TEST BAD\n/MN\n/END',
    '/PROG TEST\n/PROG OTHER\n/MN\n/END', '/PROG TEST\n/MN\n/MN\n/END',
    '/END\n/PROG TEST\n/MN', '/PROG TEST\n/MN\n/UNKNOWN\n/END',
], ids=['missing-header', 'header-suffix', 'two-headers', 'two-bodies', 'end-order', 'unknown-section'])
def test_incomplete_sections_remain_excluded_without_filename_fallback(tmp_path, text):
    (tmp_path / 'invented.LS').write_text(text)
    result = analyze(tmp_path, ['invented.LS'])
    assert result['indexed_unique_programs'] == 0 and result['excluded_files'] == 1
    assert result['items'][0]['definition_status'] == 'excluded_structure'
    assert result['items'][0]['program'] != 'INVENTED'


def test_views_pagination_selection_hash_and_empty_explicit_entry_list(tmp_path):
    listing(tmp_path, 'main.LS', 'MAIN', '1: CALL CHILD;\n2: CALL SR[1];')
    listing(tmp_path, 'child.LS', 'CHILD')
    result = analyze(tmp_path, ['main.LS', 'child.LS'], limit=1)
    assert result['total'] == 2 and result['next_offset'] == 1 and result['reachable_programs'] is None
    assert result['items'][0]['reachable_in_static_graph'] is None
    next_page = analyze(tmp_path, ['child.LS', 'main.LS'], offset=1, limit=1)
    refs = analyze(tmp_path, ['main.LS', 'child.LS'], view='references', limit=1)
    assert next_page['next_offset'] is None and next_page['items'][0]['program'] == 'MAIN'
    assert result['selection_sha256'] == next_page['selection_sha256'] == refs['selection_sha256']
    assert refs['reference_count'] == 2 and refs['next_offset'] == 1
    assert analyze(tmp_path, ['main.LS', 'child.LS'], offset=99)['items'] == []
    (tmp_path / 'not-selected.LS').write_bytes(b'\x00unsupported')
    assert analyze(tmp_path, ['main.LS', 'child.LS'])['files_selected'] == 2


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'cp1252'])
def test_supported_encodings_preserve_sources_and_hashes(tmp_path, encoding):
    path = listing(tmp_path, 'test.LS', 'TEST', '1: CALL CHILD; ! Caf\u00e9', encoding)
    result = analyze(tmp_path, ['test.LS'], view='references')
    assert result['items'][0]['source'].endswith('Caf\u00e9')
    assert analyze(tmp_path, ['test.LS'])['items'][0]['source_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('options', [
    {'paths': []}, {'paths': 'main.LS'}, {'paths': [1]}, {'paths': ['main.LS'] * 129},
    {'paths': ['main.LS', 'MAIN.ls']}, {'paths': ['../main.LS']}, {'paths': ['C:/main.LS']},
    {'paths': ['/main.LS']}, {'paths': ['a\\main.LS']}, {'paths': ['./main.LS']},
    {'paths': ['a//main.LS']}, {'paths': ['a /main.LS']}, {'paths': ['NUL.LS']}, {'paths': ['other.TP']},
    {'entry_programs': None}, {'entry_programs': ['MAIN', 'main']}, {'entry_programs': ['BAD.NAME']},
    {'entry_programs': ['A'] * 17}, {'view': 'unused'}, {'offset': True}, {'offset': -1}, {'limit': 0}, {'limit': 101},
])
def test_invalid_arguments_fail_before_reading_any_files(options):
    arguments = {'paths': ['main.LS'], 'entry_programs': [], **options}
    with pytest.raises(ValueError):
        analyze_dependencies(Path('not-present'), **arguments)


@pytest.mark.parametrize('data', [b'\x00', b'\x81', b'x' * (1024 * 1024 + 1), b'x' * 2049,
                                 ('\u00e9' * 1025).encode(), b'\n' * 50001],
                         ids=['nul', 'encoding', 'file-bytes', 'line-bytes', 'decoded-line-bytes', 'line-count'])
def test_binary_unsupported_encoding_and_bounds_refuse_input(tmp_path, data):
    (tmp_path / 'test.LS').write_bytes(data)
    with pytest.raises(ValueError):
        analyze(tmp_path, ['test.LS'])


def test_redirected_directories_are_refused_before_reading_external_text(tmp_path):
    selected, external = tmp_path / 'selected', tmp_path / 'external'
    selected.mkdir()
    external.mkdir()
    listing(external, 'child.LS', 'CHILD')
    link = selected / 'redirect'
    if os.name == 'nt':
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(external)], check=True, capture_output=True)
    else:
        link.symlink_to(external, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match='redirected'):
            analyze(selected, ['redirect/child.LS'])
    finally:
        if os.name == 'nt':
            link.rmdir()
        else:
            link.unlink()


def test_cumulative_byte_line_and_reference_budgets_are_enforced(tmp_path, monkeypatch):
    path = listing(tmp_path, 'one.LS', 'ONE')
    listing(tmp_path, 'two.LS', 'TWO')
    with monkeypatch.context() as patch:
        patch.setattr(feature, 'MAX_TOTAL_BYTES', path.stat().st_size * 2 - 1)
        with pytest.raises(ValueError, match='byte'):
            analyze(tmp_path, ['one.LS', 'two.LS'])
    for name in ('one.LS', 'two.LS'):
        (tmp_path / name).write_text('\n' * 26000)
    with pytest.raises(ValueError, match='50000 total lines'):
        analyze(tmp_path, ['one.LS', 'two.LS'])
    listing(tmp_path, 'refs.LS', 'REFS', '\n'.join(f'{index}: CALL CHILD;' for index in range(4097)))
    with pytest.raises(ValueError, match='4096'):
        analyze(tmp_path, ['refs.LS'])


def test_staged_release_is_self_contained_mit_and_keeps_default_analysis(tmp_path):
    assert feature.self_test()
    staged = install_builtin(tmp_path, 'programs')
    manifest, _ = load_release(tmp_path / 'releases/workbench.programs/1.0.0', staged['manifest_sha256'])
    assert set(manifest['files']) == {'feature.py', 'LICENSE.txt'}
    assert {row['id'] for row in load_registry(tmp_path)} == {'workbench.analysis', 'workbench.programs'}
    assert not staged['controller_contacted'] and staged['status'] == 'staged_only'
    with pytest.raises(ValueError, match='Unknown'):
        feature.handle('other', {}, {})


def test_same_mcp_session_reloads_inspector_and_checks_artifact_paths_and_large_pages(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    policy = tmp_path / 'policy.json'
    policy.write_text(json.dumps({'state_dir': 'state', 'source_dir': 'edits', 'robots': {}, 'allow_writes': False}))
    fixture = tmp_path / 'edits/fixture'
    fixture.mkdir(parents=True)
    listing(fixture, 'main.LS', 'MAIN', '\n'.join(f'{index}: CALL CHILD; !' + '\x01' * 1980 for index in range(100)))
    listing(fixture, 'child.LS', 'CHILD')

    async def scenario():
        params = StdioServerParameters(command=sys.executable,
            args=['-B', '-m', 'fanuc_workbench.bridge.mcp_server', '--config', str(policy)],
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPATH': str(Path(__file__).resolve().parents[1])})
        async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
            await session.initialize()
            tools = [tool.model_dump() for tool in (await session.list_tools()).tools]

            async def call(name, arguments):
                response = await session.call_tool(name, arguments)
                assert not response.isError, response
                return json.loads(response.content[0].text)

            assert (await call('list_operations', {}))['total'] == 34
            imported = await call('import_workspace', {'source_folder': 'fixture'})
            install_builtin(tmp_path / 'state/extensions', 'programs')
            assert not (await call('reload_extensions', {}))['mcp_tools_changed']
            descriptor = await call('describe_operation', {'operation_id': 'workbench.programs.dependencies'})
            request = {'operation_id': descriptor['id'], 'revision': descriptor['revision'], 'arguments': {
                'artifact_id': imported['artifact_id'], 'paths': ['main.LS', 'child.LS'], 'entry_programs': ['MAIN'], 'view': 'references'}}
            result = (await call('run_read_operation', request))['result']
            assert result['reference_count'] == 100 and len(result['items']) == 100 and result['reachable_programs'] == 2
            assert len(json.dumps(result, ensure_ascii=True).encode()) < 2 * 1024 * 1024
            assert (await call('list_operations', {}))['total'] == 35
            assert (await session.call_tool('run_read_operation', {**request, 'revision': 'stale'})).isError
            assert (await session.call_tool('run_read_operation', {**request,
                'arguments': {**request['arguments'], 'paths': ['../outside.LS']}})).isError
            (Path(imported['root']) / 'main.LS').write_text('changed')
            assert (await session.call_tool('run_read_operation', request)).isError
            assert [tool.model_dump() for tool in (await session.list_tools()).tools] == tools and len(tools) == 48
    asyncio.run(scenario())
