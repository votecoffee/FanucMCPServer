from __future__ import annotations

import html
from pathlib import Path

from fanuc_workbench.parsers.data import parse_frames, parse_io, parse_numeric_registers, parse_position_registers
from fanuc_workbench.services.compare import compare_workspaces
from fanuc_workbench.services.diagnostics import health_summary, scan_alarms
from fanuc_workbench.services.workspace import scan_comments, scan_programs, scan_variables
from fanuc_workbench.services.xref import program_dependencies, register_usage


def collect_report(root: Path, baseline: Path | None = None) -> dict:
    nums=[]; prs=[]; frames=[]; ios=[]
    for p in root.rglob('*'):
        n=p.name.upper()
        try:
            if n=='NUMREG.VA': nums.extend(parse_numeric_registers(p))
            elif n=='POSREG.VA': prs.extend(parse_position_registers(p))
            elif n=='FRAME.DG': frames.extend(parse_frames(p))
            elif n=='IOSTATE.DG': ios.extend(parse_io(p))
        except Exception: pass
    return {
        'health':health_summary(root), 'programs':scan_programs(root), 'numeric_registers':nums,
        'position_registers':prs, 'frames':frames, 'io':ios, 'alarms':scan_alarms(root),
        'dependencies':program_dependencies(root), 'usage':register_usage(root),
        'comments':scan_comments(root), 'variables':scan_variables(root),
        'changes':compare_workspaces(baseline,root) if baseline and baseline.is_dir() else [],
    }


def _rows(d: dict) -> dict[str, tuple[list[str], list[tuple]]]:
    return {
        'Robot / Backup': (['Field','Value'], list(d['health'].items())),
        'Programs': (['Program','Comment','File'], [(p.program_name,p.comment,p.path.name) for p in d['programs']]),
        'Numeric Registers': (['R','Comment','Value'], [(f'R[{r.index}]',r.comment,r.value) for r in d['numeric_registers']]),
        'Position Registers': (['PR','Comment','Rep','UF','UT','Values'], [(f'PR[{r.index}]' if r.group==1 else f'PR[{r.group},{r.index}]',r.comment,r.representation,r.user_frame,r.user_tool,'; '.join(f'{k}={v}' for k,v in r.values.items())) for r in d['position_registers']]),
        'Frames': (['Frame','Comment','XYZWPR'], [(f'{r.kind}[{r.index}]',r.comment,'; '.join(f'{k}={v}' for k,v in r.values.items())) for r in d['frames']]),
        'I/O': (['I/O','Comment','Value','Rack','Slot','Start'], [(f'{r.io_type}[{r.index}]',r.comment,r.value,r.rack,r.slot,r.start) for r in d['io']]),
        'Comments / Descriptions': (['Type','Item','Description','Value / Context','Source'], [(r.kind,r.identifier,r.comment,r.value,r.path.name) for r in d['comments']]),
        'Variables / Listings': (['Name','Value','Source','Line'], [(row.name,row.value,p.name,row.source_line) for p,row in d['variables']]),
        'Program Dependencies': (['Program','Calls'], [(k,', '.join(sorted(v))) for k,v in sorted(d['dependencies'].items())]),
        'Register / I/O Usage': (['Item','Read','Write','Other Use'], [(k,v['Read'],v['Write'],v['Use']) for k,v in sorted(d['usage'].items())]),
        'Alarms': (['Time','Alarm','Severity','Message','Source'], [(a.timestamp,a.code,a.severity,a.message,a.source.name) for a in d['alarms']]),
        'Changes vs Baseline': (['Status','File','Old bytes','New bytes','Semantic-only'], [(c.status,c.relative_path,c.old_size,c.new_size,'Yes' if c.semantic else 'No') for c in d['changes']]),
    }


def export_html(root: Path, out: Path, baseline: Path | None = None) -> Path:
    sections=_rows(collect_report(root,baseline))
    def table(headers,rows):
        return '<table><thead><tr>'+''.join(f'<th>{html.escape(str(h))}</th>' for h in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join(f'<td>{html.escape(str(c))}</td>' for c in r)+'</tr>' for r in rows)+'</tbody></table>'
    parts=['<!doctype html><html><head><meta charset="utf-8"><title>FANUC Workbench Report</title><style>body{font-family:Segoe UI,Arial;margin:32px;color:#222}h1,h2{color:#222}table{border-collapse:collapse;width:100%;margin-bottom:24px;font-size:12px}th,td{border:1px solid #ccc;padding:5px;text-align:left;vertical-align:top}th{background:#eee}code{font-family:Consolas}</style></head><body>','<h1>FANUC Workbench Documentation</h1>']
    for title,(headers,rows) in sections.items():
        if title=='Changes vs Baseline' and baseline is None: continue
        parts.append(f'<h2>{html.escape(title)}</h2>'+table(headers,rows))
    parts.append('</body></html>'); out.write_text(''.join(parts),encoding='utf-8'); return out


def export_xlsx(root: Path, out: Path, baseline: Path | None = None) -> Path:
    import xlsxwriter
    sections=_rows(collect_report(root,baseline)); wb=xlsxwriter.Workbook(str(out)); head=wb.add_format({'bold':True,'bg_color':'#D9EAF7','border':1}); cell=wb.add_format({'border':1,'valign':'top'}); wrap=wb.add_format({'border':1,'valign':'top','text_wrap':True})
    used=set()
    for title,(headers,rows) in sections.items():
        if title=='Changes vs Baseline' and baseline is None: continue
        name=title.replace('/','-')[:31]; base=name; n=2
        while name in used: name=(base[:27]+f' {n}')[:31]; n+=1
        used.add(name); ws=wb.add_worksheet(name)
        for c,h in enumerate(headers): ws.write(0,c,h,head)
        for r,row in enumerate(rows,1):
            for c,v in enumerate(row): ws.write(r,c,str(v),wrap if len(str(v))>40 else cell)
        ws.freeze_panes(1,0)
        if headers: ws.autofilter(0,0,max(1,len(rows)),len(headers)-1)
        for c,h in enumerate(headers): ws.set_column(c,c,36 if any(k in str(h).lower() for k in ('message','description','value','context','file')) else 18)
    wb.close(); return out


def export_pdf(root: Path, out: Path, baseline: Path | None = None) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import landscape, letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    sections=_rows(collect_report(root,baseline)); styles=getSampleStyleSheet(); story=[Paragraph('FANUC Workbench Documentation',styles['Title']),Spacer(1,12)]
    def add(title,headers,rows,limit=250):
        story.append(Paragraph(title,styles['Heading2'])); data=[headers]+[[str(x)[:120] for x in row] for row in rows[:limit]]
        t=Table(data,repeatRows=1); t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.lightgrey),('GRID',(0,0),(-1,-1),0.25,colors.grey),('FONT',(0,0),(-1,-1),'Helvetica',6),('VALIGN',(0,0),(-1,-1),'TOP')]))
        story.extend([t,Spacer(1,10)])
    for title,(headers,rows) in sections.items():
        if title=='Changes vs Baseline' and baseline is None: continue
        add(title,headers,rows)
    SimpleDocTemplate(str(out),pagesize=landscape(letter),rightMargin=24,leftMargin=24,topMargin=24,bottomMargin=24).build(story); return out


def export_all(root: Path, destination: Path, stem: str='fanuc_documentation', baseline: Path | None = None) -> list[Path]:
    destination.mkdir(parents=True,exist_ok=True)
    return [export_html(root,destination/f'{stem}.html',baseline),export_xlsx(root,destination/f'{stem}.xlsx',baseline),export_pdf(root,destination/f'{stem}.pdf',baseline)]
