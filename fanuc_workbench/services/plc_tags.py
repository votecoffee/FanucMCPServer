"""Bounded offline PLC tag catalogs and explicit I/O mapping suggestions."""
import csv
import hashlib
import io
from pathlib import Path
import re
import xml.etree.ElementTree as ET


MAX_BYTES, MAX_TAGS = 32 * 1024 * 1024, 20000
_IO = re.compile(r'\b(DIN|DOUT|GIN|GOUT|AIN|AOUT|DI|DO|GI|GO|AI|AO|RI|RO|UI|UO|FLG|F)\s*\[\s*([1-9][0-9]{0,5})\s*\]',re.I|re.ASCII)
_ALIASES = {'DIN':'DI','DOUT':'DO','GIN':'GI','GOUT':'GO','AIN':'AI','AOUT':'AO','FLG':'F'}


def normalize_io(value):
    match=_IO.fullmatch(value.strip())
    return f'{_ALIASES.get(match[1].upper(),match[1].upper())}[{match[2]}]' if match else None


def _hints(description):
    return sorted({normalize_io(match[0]) for match in _IO.finditer(description)})


def _entry(tag,scope='',data_type='',description='',alias_for='',dimensions='',explicit_io=''):
    values=[str(value).strip() for value in (tag,scope,data_type,description,alias_for,dimensions)]
    tag,scope,data_type,description,alias_for,dimensions=values
    if not tag or len(tag)>512 or len(scope)>128 or len(data_type)>128 or len(dimensions)>128 or len(description)>4096 or len(alias_for)>512:
        raise ValueError('PLC tag metadata exceeds its text bound')
    if any(ord(char)<32 for value in (tag,scope,data_type,alias_for) for char in value):
        raise ValueError('PLC tag identifiers cannot contain control characters')
    if scope and scope.casefold()!='controller' and not scope.startswith('Program:'):
        scope='Program:'+scope
    if scope.casefold()=='controller':scope=''
    hints=_hints(description)
    signal=None
    if explicit_io:
        signal=normalize_io(explicit_io)
        if signal is None:raise ValueError('Explicit Robot I/O column contains an unsupported signal')
        hints=sorted(set(hints+[signal]))
    return {'tag':tag,'scope':scope,'qualified_tag':scope+'.'+tag if scope else tag,
        'data_type':data_type,'description':description,'alias_for':alias_for,
        'dimensions':dimensions,'io_hints':hints,'explicit_io_hints':[signal] if signal else []}


def _put(catalog,row,*,comment=False,mapping=False):
    key=(row['scope'].casefold(),row['tag'].casefold())
    previous=catalog.get(key)
    if previous is not None:
        if comment:
            previous['description']=row['description'];previous['io_hints']=sorted(set(row['io_hints']+previous['explicit_io_hints']))
            return
        if any(previous[field] and row[field] and previous[field]!=row[field] for field in (('data_type','alias_for') if mapping else ('data_type','alias_for','description'))):
            raise ValueError('PLC catalog contains conflicting duplicate tag definitions')
        for field in ('data_type','alias_for','description','dimensions'):
            previous[field]=previous[field] or row[field]
        previous['io_hints']=sorted(set(previous['io_hints']+row['io_hints']))
        previous['explicit_io_hints']=sorted(set(previous['explicit_io_hints']+row['explicit_io_hints']))
        return
    if len(catalog)>=MAX_TAGS:raise ValueError('PLC catalog exceeds 20000 tags/members')
    catalog[key]=row


def _csv(text):
    try:
        dialect=csv.Sniffer().sniff(text[:8192],delimiters=',;\t')
    except csv.Error:
        dialect=csv.excel
    reader=csv.reader(io.StringIO(text),dialect,strict=True)
    header=None;catalog={};comments=[]
    for number,row in enumerate(reader,1):
        if number>100000 or len(row)>64 or any(len(value)>4096 for value in row):
            raise ValueError('PLC CSV exceeds row, column or field limits')
        if header is None:
            normalized=[re.sub(r'[^a-z0-9]','',value.casefold()) for value in row]
            name=next((name for name in ('plctag','tagname','tag','name') if name in normalized),None)
            if name:
                if len([value for value in normalized if value])!=len(set(value for value in normalized if value)):
                    raise ValueError('PLC CSV has duplicate header names')
                header={name:index for index,name in enumerate(normalized)};name_key=name
            elif number>=50:raise ValueError('PLC CSV needs a Tag/Name/PLC Tag header')
            continue
        def get(*names):
            index=next((header[name] for name in names if name in header),None)
            return row[index].strip() if index is not None and index<len(row) else ''
        raw_kind=get('type').upper();kind=raw_kind if raw_kind in ('TAG','ALIAS','COMMENT') else ''
        if raw_kind and not kind and ('specifier' in header or 'attributes' in header):continue
        tag=get(name_key)
        if not tag:continue
        entry=_entry(tag,get('scope'),get('datatype') or (raw_kind if not kind else ''),get('description','comment'),
            get('aliasfor','aliastarget','specifier') if kind=='ALIAS' or 'aliasfor' in header or 'aliastarget' in header else '',
            get('dimensions'),get('robotio','robotic','io'))
        if kind=='COMMENT':comments.append(entry)
        else:_put(catalog,entry,mapping=bool(entry['explicit_io_hints']))
    if header is None:raise ValueError('PLC CSV needs a Tag/Name/PLC Tag header')
    for entry in comments:_put(catalog,entry,comment=True)
    return catalog,[]


def _description(node):
    child=node.find('Description')
    return ''.join(child.itertext()).strip() if child is not None else ''


def _l5x(text):
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b',text,re.I):
        raise ValueError('L5X DTD/entity declarations are unsupported')
    root=ET.fromstring(text)
    pending=[(root,0)];count=0
    while pending:
        node,depth=pending.pop();count+=1
        if count>250000 or depth>64:raise ValueError('L5X exceeds XML element/depth bounds')
        node.tag=node.tag.rsplit('}',1)[-1]
        pending.extend((child,depth+1) for child in node)
    if root.tag!='RSLogix5000Content':raise ValueError('Expected an RSLogix5000Content L5X export')
    controller=root.find('Controller')
    if controller is None:raise ValueError('L5X is missing its Controller element')
    catalog={};warnings=set();types={}
    for datatype in controller.findall('DataTypes/DataType'):
        name=datatype.get('Name','')
        if name in types:raise ValueError('L5X has duplicate data type definitions')
        types[name]=datatype.findall('Members/Member')

    def expand(tag,datatype,scope,stack):
        if datatype not in types:return
        if datatype in stack or len(stack)>=8:
            warnings.add('Recursive/deep UDT members were not expanded');return
        for member in types[datatype]:
            if member.get('Hidden','').casefold()=='true':continue
            name=member.get('Name','')
            if not name:continue
            path=tag+'.'+name;kind=member.get('DataType','');dimensions=member.get('Dimensions',member.get('Dimension',''))
            row=_entry(path,scope,kind,_description(member),member.get('AliasFor',''),dimensions)
            _put(catalog,row)
            if dimensions and dimensions.strip('0, ')!='':
                warnings.add('Arrays are listed without expanding indices')
            else:expand(path,kind,scope,stack+(datatype,))

    groups=[('',controller.findall('Tags/Tag'))]
    groups.extend(('Program:'+program.get('Name',''),program.findall('Tags/Tag')) for program in controller.findall('Programs/Program'))
    for scope,tags in groups:
        for tag in tags:
            name=tag.get('Name','');kind=tag.get('DataType','');dimensions=tag.get('Dimensions','')
            row=_entry(name,scope,kind,_description(tag),tag.get('AliasFor',''),dimensions)
            _put(catalog,row)
            if dimensions and dimensions.strip('0, ')!='':warnings.add('Arrays are listed without expanding indices')
            else:expand(name,kind,scope,())
            for comment in tag.findall('Comments/Comment'):
                operand=comment.get('Operand','')
                if re.fullmatch(r'(?:\.[A-Za-z0-9_]+|\[[0-9]+(?:,[0-9]+)*\])*',operand) is None:
                    warnings.add('Unsupported member-comment operands were not expanded');continue
                _put(catalog,_entry(name+operand,scope,description=''.join(comment.itertext())),comment=True)
    return catalog,sorted(warnings)


def load_plc_tags(path: Path):
    path=Path(path)
    with path.open('rb') as stream:data=stream.read(MAX_BYTES+1)
    if len(data)>MAX_BYTES:raise ValueError('PLC export exceeds 32 MiB')
    if b'\x00' in data:raise ValueError('PLC export must be UTF-8 or CP1252 text')
    try:text=data.decode('utf-8-sig')
    except UnicodeDecodeError:text=data.decode('cp1252')
    try:
        if path.suffix.casefold()=='.csv':catalog,warnings=_csv(text);kind='csv'
        elif path.suffix.casefold()=='.l5x':catalog,warnings=_l5x(text);kind='l5x'
        else:raise ValueError('Choose a CSV or L5X export')
    except (ET.ParseError,csv.Error) as error:
        raise ValueError('PLC export syntax is unsupported or malformed') from error
    entries=sorted(catalog.values(),key=lambda row:row['qualified_tag'].casefold())
    return {'format':kind,'source_name':path.name,'source_sha256':hashlib.sha256(data).hexdigest(),
        'source_bytes':len(data),'entries':entries,'warnings':warnings,'mapping_verified':False}


def suggest_mappings(entries,signals,current=None):
    current=current or {};available={}
    for signal in signals:
        canonical=normalize_io(signal)
        if canonical:available.setdefault(canonical,[]).append(signal)
    candidates={}
    for entry in entries:
        for signal in entry['io_hints']:
            candidates.setdefault(signal,set()).add(entry['qualified_tag'])
    rows=[]
    for signal,tags in sorted(candidates.items()):
        found=available.get(signal,[]);actual=found[0] if len(found)==1 else None;old=current.get(actual,'') if actual else '';tag=next(iter(tags)) if len(tags)==1 else None
        status=('not_in_workspace' if not found else 'ambiguous' if len(tags)>1 or len(found)>1 else
                'already_mapped' if old==tag else 'replace' if old else 'ready')
        rows.append({'signal':signal,'row_signal':actual,'tag':tag,'candidates':sorted(tags),'previous':old,'status':status})
    return rows
