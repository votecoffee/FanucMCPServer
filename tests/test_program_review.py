import json

import pytest

from fanuc_workbench.services.program_analysis import semantic_compare,dependency_review
from test_program_logic import listing


def pair(root,before,after,positions_before='',positions_after=''):
    path=listing(root,body=before,positions=positions_before)
    (root/'before.LS').write_bytes(path.read_bytes())
    path=listing(root,body=after,positions=positions_after)
    (root/'after.LS').write_bytes(path.read_bytes())


def categories(root): return semantic_compare(root,'before.LS','after.LS')['category_counts']


@pytest.mark.parametrize('before,after,expected',[
    ('1: L P[1] 100mm/sec FINE ;','1: L P[1] 200mm/sec FINE ;',{'speed'}),
    ('1: L P[1] 100mm/sec FINE ;','1: L P[1] 100mm/sec CNT50 ;',{'termination'}),
    ('1: L P[1] 100mm/sec FINE Offset,PR[1] ;','1: L P[1] 100mm/sec FINE Offset,PR[2] ;',{'offsets'}),
    ('1: UFRAME_NUM=1 ;','1: UFRAME_NUM=2 ;',{'frames_tools'}),
    ('1: R[1]=1 ;','1: R[1]=2 ;',{'instruction'}),
    ('1: MESSAGE[\'Before\'] ;','1: MESSAGE[\'After\'] ;',{'instruction'}),
    ('1: R[1:Old]=1 ; ! Before','1: R[1:New]=1 ; ! After',{'comments'}),
    ('1: ! Before ;\n2: R[1]=1 ;','1: ! After ;\n2: R[1]=1 ;',{'comments'}),
    ('1: R[1]=1 ;','9: R[1] = 1.000 ;',set()),
])
def test_semantic_categories_and_numbering_formatting_normalization(tmp_path,before,after,expected):
    pair(tmp_path,before,after)
    assert set(categories(tmp_path))==expected


def test_positions_frames_tools_and_attribute_comments_are_separate(tmp_path):
    pair(tmp_path,'1: END ;','1: END ;','P[1]{\n GP1:\n UF : 1, UT : 2, X = 1 mm\n};',
         'P[1]{\n GP1:\n UF : 3, UT : 2, X = 2 mm\n};')
    assert set(categories(tmp_path))=={'frames_tools','positions'}
    after=tmp_path/'after.LS';after.write_text(after.read_text().replace('"Synthetic"','"Changed comment"'))
    assert set(categories(tmp_path))=={'frames_tools','positions','comments'}
    result=semantic_compare(tmp_path,'before.LS','after.LS',limit=1)
    assert result['total']==3 and result['next_offset']==1 and not result['semantic_equivalence_verified']


def inventory(root,resources=(),mappings=()):
    path=root/'inventory.json'
    path.write_text(json.dumps({'format':'fanuc-dependency-inventory','schema_version':1,
        'resources':{key:{'evidence':'Synthetic declaration'} for key in resources},
        'io_mappings':{key:{'rack':0,'slot':1,'start':1} for key in mappings}}))
    return path


def test_dependency_review_requires_transitive_programs_and_explicit_data_io_frame_inventory(tmp_path):
    listing(tmp_path,body='1: CALL CHILD ;\n2: UFRAME_NUM=2 ;\n3: R[1]=DI[1] ;')
    listing(tmp_path,'CHILD','1: PR[1].J1=R[2] ;')
    paths=['MAIN.LS','CHILD.LS']
    report=dependency_review(tmp_path,paths,entry_programs=['MAIN'],view='missing')
    assert report['missing_count']==5 and len(report['program_files'])==2
    inventory(tmp_path,['UFRAME[2]','R[1]','R[2]','PR[1]'],['DI[1]'])
    report=dependency_review(tmp_path,paths,entry_programs=['MAIN'],inventory_path='inventory.json')
    assert report['explicit_dependencies_accounted_for'] and not report['physical_compatibility_verified']
    assert report['inventory_sha256']


def test_indirect_calls_missing_positions_and_unresolved_jumps_block_completeness(tmp_path):
    listing(tmp_path,body='1: CALL SR[1] ;\n2: PR[R[1]]=PR[2] ;\n3: L P[3] 10mm/sec FINE ;\n4: JMP LBL[99] ;')
    report=dependency_review(tmp_path,['MAIN.LS'],entry_programs=['MAIN'],view='missing')
    assert {row['resolution'] for row in report['items']}=={'unresolved_indirect','missing_inventory','missing_position','unresolved_runtime_frame_tool_state'}
    assert report['issue_count'] and not report['explicit_dependencies_accounted_for']
    with pytest.raises(ValueError): dependency_review(tmp_path,['MAIN.LS'],entry_programs=[])


def test_dynamic_and_conditional_frame_tool_dependencies_remain_explicit(tmp_path):
    listing(tmp_path,body='1: UFRAME_NUM=R[1] ;\n2: IF DI[1]=ON,UTOOL_NUM=2 ;\n3: R[2]=UTOOL_NUM ;')
    report=dependency_review(tmp_path,['MAIN.LS'],entry_programs=['MAIN'],view='missing')
    assert any(row['resolution']=='unresolved_frame_tool_index' for row in report['items'])
    assert {'UTOOL[2]','UTOOL_NUM'}<={row['token'] for row in report['items']}


def test_program_identity_and_unknown_sections_are_never_silently_equal(tmp_path):
    pair(tmp_path,'1: END ;','1: END ;')
    path=tmp_path/'after.LS';path.write_text(path.read_text().replace('/PROG MAIN','/PROG OTHER').replace('/MN','/APPL\nUNKNOWN=1;\n/MN'))
    assert set(categories(tmp_path))=={'program_identity','unclassified_sections'}


@pytest.mark.parametrize('mode',['semantic','dependencies'])
def test_changed_source_between_reads_is_refused(tmp_path,monkeypatch,mode):
    from fanuc_workbench.extensions.programs_v1_1_0 import review
    pair(tmp_path,'1: R[1]=1 ;','1: R[1]=2 ;')
    original=review.base._read;changed=False
    def read(root,relative,remaining):
        nonlocal changed
        result=original(root,relative,remaining)
        if not changed:
            changed=True
            path=root/relative;path.write_bytes(path.read_bytes()+b'\n! Synthetic concurrent revision')
        return result
    monkeypatch.setattr(review.base,'_read',read)
    with pytest.raises(ValueError,match='revision changed'):
        if mode=='semantic':semantic_compare(tmp_path,'before.LS','after.LS')
        else:dependency_review(tmp_path,['before.LS'],entry_programs=['MAIN'])
