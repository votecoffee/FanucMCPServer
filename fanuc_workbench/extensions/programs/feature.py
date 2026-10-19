"""Bounded LS dependency evidence; original MIT code, see LICENSE.txt."""
from __future__ import annotations

from collections import Counter
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re


MAX_FILE_BYTES, MAX_TOTAL_BYTES, MAX_LINES, MAX_REFERENCES = 1024 * 1024, 16 * 1024 * 1024, 50000, 4096
_NAME = r'[A-Z0-9_]{1,64}'
_FLAGS = re.I | re.ASCII
_HEADER = re.compile(r'\s*/PROG\s+(' + _NAME + r')\s*', _FLAGS)
_LINE = re.compile(r'\s*([0-9]{1,6})\s*:\s*(.*)', _FLAGS)
_COMMAND = re.compile(r'^(?:IF\b[^,]*,\s*)?(CALL|RUN)\b\s*(.*)$', _FLAGS)
_TOKEN = re.compile(r'\b(CALL|RUN)\b', _FLAGS)
_DIRECT = re.compile(r'(' + _NAME + r')\s*(?:\([^()]*\))?', _FLAGS)


def _options(paths, entry_programs, view, offset, limit):
    if not isinstance(paths, list) or not 1 <= len(paths) <= 128:
        raise ValueError('Select 1-128 explicit relative LS paths')
    for value in paths:
        if (not isinstance(value, str) or not 1 <= len(value) <= 240
                or any(ord(char) < 32 or char in '\\:*?"<>|' for char in value)
                or PurePosixPath(value).is_absolute() or '..' in PurePosixPath(value).parts
                or value != PurePosixPath(value).as_posix()
                or any(part.endswith(('.', ' ')) for part in value.split('/'))
                or any(part.split('.', 1)[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                    *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))} for part in value.split('/'))
                or not value.upper().endswith('.LS')):
            raise ValueError('Use normalized relative LS paths without redirects or traversal')
    if len({path.casefold() for path in paths}) != len(paths):
        raise ValueError('Selected paths must be distinct ignoring case')
    if (not isinstance(entry_programs, list) or len(entry_programs) > 16
            or any(not isinstance(name, str) or re.fullmatch(_NAME, name, _FLAGS) is None for name in entry_programs)
            or len({name.upper() for name in entry_programs}) != len(entry_programs)):
        raise ValueError('Supply up to 16 distinct explicit entry program names, or an empty list')
    if view not in ('programs', 'references', 'issues'):
        raise ValueError('Choose programs, references or issues view')
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Use offset >= 0 and limit 1-100')


def _mask(text):
    """Hide quoted text and nested argument/description content, preserving positions."""
    result, stack, quote = [], [], None
    for index, char in enumerate(text):
        if quote is not None:
            result.append(' ')
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
            result.append(' ' if stack else '#')
        elif char == '!' and not stack:
            result.extend(' ' * (len(text) - index))
            break
        elif char in '[(':
            result.append(char if not stack else ' ')
            stack.append(char)
        elif char in '])':
            if not stack or stack.pop() != {']': '[', ')': '('}[char]:
                return None
            result.append(char if not stack else ' ')
        else:
            result.append(char if not stack else ' ')
    return None if quote is not None or stack else ''.join(result)


def _reference(code):
    command = _COMMAND.fullmatch(code.strip())
    if command is not None:
        kind, tail = command[1].upper(), command[2].strip()
        direct = _DIRECT.fullmatch(tail)
        if direct is not None:
            return kind, direct[1].upper(), 'direct'
        if re.fullmatch(r'SR\s*\[[^\[\]]*\]\s*(?:\([^()]*\))?', tail, _FLAGS):
            return kind, None, 'dynamic'
        return kind, None, 'unsupported_target'
    token = _TOKEN.search(code)
    return (token[1].upper(), None, 'unsupported_context') if token else None


def _read(root, relative, remaining):
    path = root / relative
    if path.resolve() != path.absolute() or not path.resolve().is_relative_to(root):
        raise ValueError('Selected LS file contains a redirected path')
    maximum = min(MAX_FILE_BYTES, remaining)
    with path.open('rb') as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError('Selected LS input exceeds the per-file or total byte bound')
    try:
        text, encoding = data.decode('utf-8-sig'), 'utf-8'
    except UnicodeDecodeError:
        try:
            text, encoding = data.decode('cp1252'), 'cp1252'
        except UnicodeDecodeError as error:
            raise ValueError('Unsupported LS text encoding') from error
    if '\x00' in text:
        raise ValueError('LS text contains NUL bytes')
    lines = []
    for line in io.StringIO(text):
        line = line.rstrip('\r\n')
        if len(lines) >= MAX_LINES or len(line.encode('utf-8')) > 2048:
            raise ValueError('LS input exceeds line count or decoded line byte bound')
        lines.append(line)
    return lines, {'path': relative, 'source_sha256': hashlib.sha256(data).hexdigest(),
                   'source_bytes': len(data), 'encoding': encoding, 'lines_total': len(lines)}


def _inspect(lines, file):
    issues, references = [], []

    def issue(line, code, source=''):
        issues.append({'path': file['path'], 'line_number': line, 'code': code, 'source': source})

    headers = [(index, _HEADER.fullmatch(line)) for index, line in enumerate(lines)
               if re.match(r'\s*/PROG\b', line, _FLAGS)]
    mn = [index for index, line in enumerate(lines) if line.strip().upper() == '/MN']
    end = [index for index, line in enumerate(lines) if line.strip().upper() == '/END']
    name = headers[0][1][1].upper() if len(headers) == 1 and headers[0][1] else None
    structure = bool(name and len(mn) == len(end) == 1 and headers[0][0] < mn[0] < end[0])
    if not structure:
        issue(0, 'program_sections_unsupported')
    file.update({'program': name, 'definition_status': 'indexed' if structure else 'excluded_structure'})
    if mn:
        stop = next((index for index in range(mn[0] + 1, len(lines)) if lines[index].lstrip().startswith('/')), len(lines))
        if stop < len(lines) and lines[stop].strip().upper() not in ('/POS', '/END'):
            file['definition_status'] = 'excluded_structure'
            issue(stop + 1, 'mn_boundary_unsupported', lines[stop])
        for index in range(mn[0] + 1, stop):
            raw = lines[index]
            if not raw.strip() or raw.lstrip().startswith('!'):
                continue
            line = _LINE.fullmatch(raw)
            if line is None:
                issue(index + 1, 'numbered_statement_required', raw)
                continue
            masked = _mask(line[2])
            if masked is None:
                issue(index + 1, 'lexical_structure_unsupported', raw)
                continue
            code = masked.rstrip()
            if not code:
                continue
            if not code.endswith(';') or ';' in code[:-1]:
                issue(index + 1, 'single_terminated_statement_required', raw)
                continue
            reference = _reference(code[:-1])
            if reference is not None:
                kind, target, target_kind = reference
                references.append({'path': file['path'], 'program': name, 'line_number': index + 1,
                    'tp_line_number': int(line[1]), 'kind': kind, 'target': target,
                    'target_kind': target_kind, 'source': raw})
    return references, issues


def analyze_dependencies(root: Path, paths: list[str], *, entry_programs: list[str],
                         view='programs', offset=0, limit=100):
    _options(paths, entry_programs, view, offset, limit)
    root = Path(root).resolve()
    files, references, issues, total_bytes, total_lines = [], [], [], 0, 0
    for relative in sorted(paths):
        lines, file = _read(root, relative, MAX_TOTAL_BYTES - total_bytes)
        total_bytes += file['source_bytes']
        total_lines += file['lines_total']
        if total_lines > MAX_LINES:
            raise ValueError('Selected LS inputs exceed 50000 total lines')
        found, diagnostic = _inspect(lines, file)
        references.extend(found)
        issues.extend(diagnostic)
        files.append(file)
        if len(references) > MAX_REFERENCES:
            raise ValueError('Selected LS inputs exceed 4096 call/run references')
    index = {}
    for file in files:
        if file['program'] is not None:
            index.setdefault(file['program'], []).append(file)
    for definitions in index.values():
        if len(definitions) > 1:
            for file in definitions:
                if file['definition_status'] == 'indexed':
                    file['definition_status'] = 'ambiguous'
                issues.append({'path': file['path'], 'line_number': 0, 'code': 'duplicate_program_definition', 'source': ''})
    entries = [name.upper() for name in entry_programs]
    graph = {name: set() for name, definitions in index.items()
             if len(definitions) == 1 and definitions[0]['definition_status'] == 'indexed'}
    if any(name not in graph for name in entries):
        raise ValueError('Every entry program must identify exactly one indexed definition')
    by_path = {file['path']: file for file in files}
    incoming = Counter()
    for reference in references:
        matches = index.get(reference['target'], [])
        resolution = ('unresolved_target' if reference['target'] is None else
                      'not_in_selection' if not matches else 'ambiguous' if len(matches) > 1 else
                      'excluded_definition' if matches[0]['definition_status'] != 'indexed' else 'resolved')
        reference.update({'resolution': resolution, 'matching_definitions': len(matches),
            'resolved_path': matches[0]['path'] if resolution == 'resolved' else None,
            'included_in_static_graph': False})
        source = by_path[reference['path']]
        if resolution == 'resolved' and source['definition_status'] == 'indexed':
            graph[reference['program']].add(reference['target'])
            reference['included_in_static_graph'] = True
            incoming[reference['target']] += 1
        if resolution != 'resolved':
            issues.append({'path': reference['path'], 'line_number': reference['line_number'],
                'code': 'target_' + resolution, 'source': reference['source']})
    reachable, pending = set(), list(entries)
    while pending:
        name = pending.pop()
        if name not in reachable:
            reachable.add(name)
            pending.extend(graph[name] - reachable)
    outgoing = Counter(reference['path'] for reference in references)
    for file in files:
        unique = file['definition_status'] == 'indexed'
        file.update({'references': outgoing[file['path']],
            'incoming_resolved_call_sites': incoming[file['program']] if unique else None,
            'outgoing_resolved_targets': len(graph[file['program']]) if unique else None,
            'reachable_in_static_graph': file['program'] in reachable if entries and unique else None})
    issues.sort(key=lambda row: (row['path'], row['line_number'], row['code']))
    witness = [{key: file[key] for key in ('path', 'source_bytes', 'source_sha256')} for file in files]
    selected = {'programs': files, 'references': references, 'issues': issues}[view]
    return {'selection_sha256': hashlib.sha256(json.dumps(witness, ensure_ascii=True, sort_keys=True,
            separators=(',', ':')).encode('utf-8')).hexdigest(),
        'files_selected': len(files), 'source_bytes': total_bytes, 'lines_total': total_lines,
        'indexed_unique_programs': len(graph), 'ambiguous_program_names': sum(len(value) > 1 for value in index.values()),
        'excluded_files': sum(file['definition_status'] == 'excluded_structure' for file in files),
        'reference_count': len(references), 'reference_kinds': dict(Counter(row['kind'] for row in references)),
        'resolution_counts': dict(Counter(row['resolution'] for row in references)),
        'issue_counts': dict(Counter(row['code'] for row in issues)),
        'static_graph_edges': sum(len(value) for value in graph.values()), 'entry_programs': entries,
        'reachable_programs': len(reachable) if entries else None,
        'view': view, 'total': len(selected), 'offset': offset,
        'next_offset': offset + limit if offset + limit < len(selected) else None,
        'items': selected[offset:offset + limit],
        'dependency_completeness_verified': False, 'runtime_reachability_verified': False,
        'compiler_validation_performed': False, 'controller_program_inventory_verified': False,
        'controller_contacted': False, 'controller_written': False, 'evidence_only': True}


def self_test():
    return _reference(_mask('CALL SUB(R[1:CALL OTHER]);')[:-1]) == ('CALL', 'SUB', 'direct')


def handle(operation_id, arguments, context):
    if operation_id != 'workbench.programs.dependencies':
        raise ValueError('Unknown programs operation')
    options = {key: value for key, value in arguments.items() if key != 'artifact_id'}
    return analyze_dependencies(Path(context['workspace']['root']), **options)
