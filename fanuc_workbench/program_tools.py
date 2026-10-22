"""Offline program analysis and local validation CLI; no controller client."""
import argparse
import json
from pathlib import Path
from fanuc_workbench.version import add_version_argument

from fanuc_workbench.services.program_analysis import (trace_logic,explain_wait,triggered_captures,
    semantic_compare,dependency_review)
from fanuc_workbench.extensions.programs_v1_1_0.diagnostics import observations
from fanuc_workbench.services.program_packages import build_change_package
from fanuc_workbench.services.native_validation import validate_offline,review_validation


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    add_version_argument(parser)
    parser.add_argument('--workspace',type=Path,default=Path.cwd())
    commands=parser.add_subparsers(dest='command',required=True)
    trace=commands.add_parser('trace');trace.add_argument('paths',nargs='+');trace.add_argument('--view',default='instructions',choices=['instructions','accesses','signals','edges','issues'])
    wait=commands.add_parser('wait');wait.add_argument('path');wait.add_argument('observations');wait.add_argument('--index',type=int,default=-1);wait.add_argument('--max-age',type=float,default=5)
    capture=commands.add_parser('capture');capture.add_argument('paths',nargs='+');capture.add_argument('--observations',required=True);capture.add_argument('--before',type=int,default=20);capture.add_argument('--after',type=int,default=20);capture.add_argument('--signals',nargs='*',default=[]);capture.add_argument('--view',choices=['captures','observations'],default='captures');capture.add_argument('--capture-index',type=int,default=0)
    compare=commands.add_parser('compare');compare.add_argument('before_path');compare.add_argument('after_path')
    deps=commands.add_parser('dependencies');deps.add_argument('paths',nargs='+');deps.add_argument('--entry',nargs='+',required=True);deps.add_argument('--inventory');deps.add_argument('--view',choices=['requirements','missing','issues'],default='requirements')
    package=commands.add_parser('package');package.add_argument('paths',nargs='+');package.add_argument('--entry',nargs='+',required=True);package.add_argument('--inventory');package.add_argument('--destination',type=Path,required=True);package.add_argument('--allow-incomplete',action='store_true')
    compiler=commands.add_parser('compile');compiler.add_argument('source',type=Path);compiler.add_argument('--destination',type=Path,required=True);compiler.add_argument('--maketp',type=Path,required=True);compiler.add_argument('--printtp',type=Path,required=True);compiler.add_argument('--configuration',type=Path,required=True);compiler.add_argument('--robot-context',type=Path,required=True);compiler.add_argument('--version',required=True);compiler.add_argument('--timeout',type=float,default=30)
    review=commands.add_parser('validation');review.add_argument('source_path');review.add_argument('report_path');review.add_argument('--compiled-path');review.add_argument('--readback-path')
    for command in (trace,capture,compare,deps):
        command.add_argument('--offset',type=int,default=0);command.add_argument('--limit',type=int,default=20)
    args=parser.parse_args(argv);root=args.workspace
    if args.command=='trace': result=trace_logic(root,args.paths,view=args.view,offset=args.offset,limit=args.limit)
    elif args.command=='wait':
        rows,digest=observations(root,args.observations)
        if not rows or not -len(rows)<=args.index<len(rows): parser.error('Observation index is unavailable')
        result=explain_wait(root,args.path,rows[args.index],max_age_seconds=args.max_age)
        result['observation_file_sha256']=digest
    elif args.command=='capture': result=triggered_captures(root,args.paths,args.observations,before=args.before,after=args.after,signals=args.signals,view=args.view,capture_index=args.capture_index,offset=args.offset,limit=args.limit)
    elif args.command=='compare': result=semantic_compare(root,args.before_path,args.after_path,offset=args.offset,limit=args.limit)
    elif args.command=='dependencies': result=dependency_review(root,args.paths,entry_programs=args.entry,inventory_path=args.inventory,view=args.view,offset=args.offset,limit=args.limit)
    elif args.command=='package': result=build_change_package(root,args.paths,args.destination,entry_programs=args.entry,inventory_path=args.inventory,allow_incomplete=args.allow_incomplete)
    elif args.command=='compile': result=validate_offline(args.source,args.destination,maketp=args.maketp,printtp=args.printtp,configuration=args.configuration,robot_context=args.robot_context,version=args.version,timeout=args.timeout)
    else: result=review_validation(root,args.source_path,args.report_path,compiled_path=args.compiled_path,readback_path=args.readback_path)
    print(json.dumps(result,indent=2,allow_nan=False))
    return result


if __name__=='__main__': main()
