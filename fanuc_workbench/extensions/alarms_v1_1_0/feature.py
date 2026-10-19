"""Self-contained alarm analysis and comparison; original MIT code."""
from pathlib import Path

from .comparison import compare_alarms, self_test as comparison_self_test
from .parser import analyze_alarms, self_test as parser_self_test


def self_test():
    return parser_self_test() and comparison_self_test()


def handle(operation_id, arguments, context):
    if operation_id == 'workbench.alarms.analyze':
        options = {key: value for key, value in arguments.items() if key not in {'artifact_id', 'path'}}
        return analyze_alarms(Path(context['log']['file']), **options)
    if operation_id == 'workbench.alarms.compare':
        excluded = {'baseline_artifact_id', 'baseline_path', 'candidate_artifact_id', 'candidate_path'}
        options = {key: value for key, value in arguments.items() if key not in excluded}
        return compare_alarms(Path(context['baseline']['file']), Path(context['candidate']['file']), **options)
    raise ValueError('Unknown alarm operation')
