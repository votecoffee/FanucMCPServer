"""Reloadable program diagnostics, retaining the original dependency operation."""
from pathlib import Path

from . import base
from .diagnostics import Condition,explain_wait,observations,triggered_captures
from .logic import trace_logic
from .review import semantic_compare,dependency_review
from .validation import review_validation


def handle(operation_id,arguments,context):
    if operation_id=='workbench.programs.dependencies': return base.handle(operation_id,arguments,context)
    options={key:value for key,value in arguments.items() if key!='artifact_id'}
    root=Path(context['workspace']['root'])
    if operation_id=='workbench.programs.explain_wait':
        rows,digest=observations(root,options.pop('observation_path'))
        index=options.pop('observation_index',-1)
        if type(index) is not int or not -len(rows)<=index<len(rows): raise ValueError('Observation index is unavailable')
        result=explain_wait(root,observation=rows[index],**options)
        result['observation_file_sha256']=digest
        return result
    handlers={'workbench.programs.trace':trace_logic,'workbench.programs.triggered_captures':triggered_captures,
              'workbench.programs.semantic_compare':semantic_compare,'workbench.programs.dependency_review':dependency_review,
              'workbench.programs.review_validation':review_validation}
    if operation_id not in handlers: raise ValueError('Unknown program diagnostics operation')
    return handlers[operation_id](root,**options)


def self_test():
    return base.self_test() and Condition('(DI[1]=ON AND R[1]>=2)',{'values':{'DI[1]':True,'R[1]':2}}).evaluate()[0] is True
