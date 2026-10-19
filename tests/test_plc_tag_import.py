import hashlib

import pytest

from fanuc_workbench.services import plc_tags
from fanuc_workbench.services.io_map import export_io_csv,load_plc_map
from fanuc_workbench.services.plc_tags import load_plc_tags,suggest_mappings


def saved(tmp_path,name,text):
    path=tmp_path/name;path.write_text(text,encoding='utf-8');return path


def test_generic_csv_and_io_map_roundtrip_preserve_explicit_references(tmp_path):
    path=saved(tmp_path,'tags.csv','Name,Type,Description,AliasFor,Robot I/O\nReady,BOOL,"Robot DI[1]",Robot:I.Data.0,DI[1]\n')
    original=path.read_bytes();catalog=load_plc_tags(path)
    row=catalog['entries'][0]
    assert row['tag']=='Ready' and row['data_type']=='BOOL' and row['alias_for']=='Robot:I.Data.0'
    assert row['io_hints']==['DI[1]'] and catalog['source_sha256']==hashlib.sha256(original).hexdigest()
    assert path.read_bytes()==original and not catalog['mapping_verified']
    mapping=tmp_path/'map.csv'
    export_io_csv(mapping,[('DIN[1]','First','ON','1','1','0','Ready'),('DIN[2]','Second','OFF','1','1','1','Ready')])
    imported=load_plc_tags(mapping)['entries']
    assert len(imported)==1 and imported[0]['io_hints']==['DI[1]','DI[2]']


def test_rockwell_csv_preamble_aliases_program_scope_and_member_comments(tmp_path):
    path=saved(tmp_path,'tags.csv','remark,CSV export\nTYPE,SCOPE,NAME,DESCRIPTION,DATATYPE,SPECIFIER,ATTRIBUTES\n'
        'TAG,,Robot,"Container",RobotUDT,,\nALIAS,Main,Ready,"DI[1] ready",,Robot.Input.0,\n'
        'COMMENT,,Robot.Input.1,"DI[2] member",,,\n')
    entries={row['qualified_tag']:row for row in load_plc_tags(path)['entries']}
    assert entries['Program:Main.Ready']['alias_for']=='Robot.Input.0'
    assert entries['Robot.Input.1']['io_hints']==['DI[2]']


L5X='''<?xml version="1.0"?><RSLogix5000Content><Controller Name="Synthetic">
<DataTypes><DataType Name="RobotIO"><Members>
<Member Name="Ready" DataType="BOOL"><Description>DI[1] generic</Description></Member>
<Member Name="Start" DataType="BOOL"><Description>DO[2] command</Description></Member>
<Member Name="Words" DataType="DINT" Dimension="16"/>
<Member Name="HiddenData" DataType="DINT" Hidden="true"/>
</Members></DataType></DataTypes><Tags>
<Tag Name="Robot1" DataType="RobotIO"><Comments><Comment Operand=".Ready">DI[3] instance</Comment></Comments></Tag>
<Tag Name="ReadyAlias" TagType="Alias" AliasFor="Robot1.Ready"><Description>DI[3]</Description></Tag>
</Tags><Programs><Program Name="Cell"><Tags><Tag Name="StartAlias" TagType="Alias" AliasFor="Robot1.Start"><Description>DO[2]</Description></Tag></Tags></Program></Programs>
</Controller></RSLogix5000Content>'''


def test_l5x_imports_udt_members_aliases_scopes_and_instance_comment_overrides(tmp_path):
    path=saved(tmp_path,'robot.L5X',L5X);original=path.read_bytes();catalog=load_plc_tags(path)
    entries={row['qualified_tag']:row for row in catalog['entries']}
    assert entries['Robot1.Ready']['data_type']=='BOOL' and entries['Robot1.Ready']['io_hints']==['DI[3]']
    assert entries['ReadyAlias']['alias_for']=='Robot1.Ready'
    assert entries['Program:Cell.StartAlias']['alias_for']=='Robot1.Start'
    assert entries['Robot1.Words']['dimensions']=='16' and 'Robot1.HiddenData' not in entries
    assert catalog['warnings']==['Arrays are listed without expanding indices']
    assert path.read_bytes()==original


def test_suggestions_are_explicit_ambiguous_and_replacement_aware(tmp_path):
    entries=load_plc_tags(saved(tmp_path,'robot.l5x',L5X))['entries']
    rows={row['signal']:row for row in suggest_mappings(entries,['DIN[3]','DOUT[2]'])}
    assert rows['DI[3]']['status']==rows['DO[2]']['status']=='ambiguous'
    filtered=[entry for entry in entries if entry['qualified_tag'].startswith('Robot1.')]
    rows={row['signal']:row for row in suggest_mappings(filtered,['DIN[3]','DOUT[2]'],{'DIN[3]':'Previous'})}
    assert rows['DI[3]']['status']=='replace' and rows['DI[3]']['previous']=='Previous'
    assert rows['DO[2]']['status']=='ready' and rows['DO[2]']['row_signal']=='DOUT[2]'
    assert suggest_mappings(filtered,['DIN[3]','DI[3]'])[1]['status']=='not_in_workspace'
    assert next(row for row in suggest_mappings(filtered,['DIN[3]','DI[3]']) if row['signal']=='DI[3]')['status']=='ambiguous'


@pytest.mark.parametrize('text',[
    '<!DOCTYPE a [<!ENTITY x "abc">]><RSLogix5000Content/>',
    '<other/>','<RSLogix5000Content/>','<RSLogix5000Content><Controller>',
    '<RSLogix5000Content><Controller><DataTypes><DataType Name="A"/><DataType Name="A"/></DataTypes></Controller></RSLogix5000Content>',
    '<RSLogix5000Content>'+('<x>'*65)+('</x>'*65)+'</RSLogix5000Content>',
],ids=['dtd','wrong-root','missing-controller','malformed','duplicate-type','depth'])
def test_unsafe_or_unsupported_xml_is_refused(tmp_path,text):
    with pytest.raises(ValueError):load_plc_tags(saved(tmp_path,'bad.l5x',text))


@pytest.mark.parametrize('text',[
    'Nothing,Here\n1,2', 'Name,Name\nA,B', 'Name,Type\nA,BOOL\nA,DINT',
    'PLC Tag,Robot I/O\nA,DI[*]', 'Name,Description\nA,"unterminated',
],ids=['missing-header','duplicate-header','conflicting-tag','wildcard-io','malformed'])
def test_malformed_csv_is_refused_without_partial_catalog(tmp_path,text):
    with pytest.raises(ValueError):load_plc_tags(saved(tmp_path,'bad.csv',text))


def test_limits_binary_and_recursive_arrays_are_explicit(tmp_path,monkeypatch):
    path=saved(tmp_path,'tags.csv','Name\nA\nB\n')
    with monkeypatch.context() as patch:
        patch.setattr(plc_tags,'MAX_TAGS',1)
        with pytest.raises(ValueError,match='20000'):load_plc_tags(path)
    with monkeypatch.context() as patch:
        patch.setattr(plc_tags,'MAX_BYTES',4)
        with pytest.raises(ValueError,match='32 MiB'):load_plc_tags(path)
    path.write_bytes(b'\x00')
    with pytest.raises(ValueError):load_plc_tags(path)
    path=saved(tmp_path,'recursive.l5x','<RSLogix5000Content><Controller><DataTypes><DataType Name="Rec"><Members><Member Name="Child" DataType="Rec"/></Members></DataType></DataTypes><Tags><Tag Name="Root" DataType="Rec"/></Tags></Controller></RSLogix5000Content>')
    catalog=load_plc_tags(path)
    assert len(catalog['entries'])==2 and catalog['warnings']==['Recursive/deep UDT members were not expanded']


def test_gui_import_assign_and_review_change_only_grid_until_save(tmp_path,monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication,QDialog
    from fanuc_workbench.core.settings import QStandardPaths
    from fanuc_workbench.ui import main_window,plc_catalog
    app=QApplication.instance() or QApplication([])
    monkeypatch.setattr(QStandardPaths,'writableLocation',lambda _:str(tmp_path/'settings'))
    monkeypatch.setattr(main_window,'discover_plugins',lambda:[])
    workspace=tmp_path/'workspace';workspace.mkdir()
    (workspace/'IOSTATE.DG').write_text('DIN[3] ON Ready\nDOUT[2] OFF Start\n')
    source=saved(tmp_path,'robot.l5x',L5X)
    window=main_window.MainWindow();window.workspace=workspace;window.io.sync_workspace()
    panel=window.io.plc_catalog
    class Immediate:
        def start(self,worker):worker.run()
    window.thread_pool=Immediate()
    monkeypatch.setattr(plc_catalog.QFileDialog,'getOpenFileName',lambda *_:(str(source),''))
    panel.choose_import()
    assert panel.catalog['format']=='l5x' and panel.import_button.isEnabled()
    panel.filter.setText('tag:Robot1.')
    window.io.table.setCurrentCell(0,0)
    row=next(i for i in range(panel.table.rowCount()) if panel.table.item(i,0).text()=='Robot1.Ready')
    panel.table.setCurrentCell(row,0);panel.assign()
    assert window.io.table.item(0,6).text()=='Robot1.Ready' and load_plc_map(workspace)=={}
    monkeypatch.setattr(plc_catalog.QDialog,'exec',lambda _:QDialog.Accepted)
    panel.review()
    assert window.io.table.item(1,6).text()=='Robot1.Start' and load_plc_map(workspace)=={}
    window.io.save_map()
    assert load_plc_map(workspace)=={'DIN[3]':'Robot1.Ready','DOUT[2]':'Robot1.Start'}
    assert window.desktop_layout.tables['IOTab/table/plc_tag_catalog'][0] is panel.table
    def stale(_):
        window.io.table.item(0,6).setText('Other')
        return QDialog.Accepted
    monkeypatch.setattr(plc_catalog.QDialog,'exec',stale)
    panel.review()
    assert window.io.table.item(0,6).text()=='Other' and 'changed during review' in panel.status.text()
    window.close()
