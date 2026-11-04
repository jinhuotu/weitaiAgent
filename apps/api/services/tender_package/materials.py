"""Conservative material-name evidence from DOCX body, never from filenames."""
import re
import unicodedata
import zipfile
from xml.etree import ElementTree as ET

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
LABEL = r'(?:物料名称|物资名称|货物名称|设备名称|产品名称|标的名称)'


def clean(value):
    return unicodedata.normalize('NFKC', value).strip()


def select_material(evidence):
    valid = [x for x in evidence if x['name'] and x['name'] not in ('无', '待定', '待填写', '/', '-', '名称')]
    if not valid:
        return dict(material=None, evidence=evidence, conflict=False, needs_confirmation=True,
                    reason='正文无明确物料名称字段或专用标题；组件清单、型号及参数不作为物料名称')
    best = min(x['authority'] for x in valid)
    names = sorted({x['name'] for x in valid if x['authority'] == best})
    conflict = len({x['name'] for x in valid}) > 1
    return dict(material=names[0] if len(names) == 1 else None, evidence=evidence,
                conflict=conflict, needs_confirmation=len(names) != 1 or conflict,
                reason='同级权威来源冲突，需人工确认' if len(names) != 1 else
                '按名称字段优先于规范专用标题取值；存在其他候选' if conflict else '正文权威位置名称一致')


def extract_material(path=None, *, text=''):
    evidence = []
    def add(name, location, basis, authority):
        name = clean(name)
        if name and len(name) <= 160:
            evidence.append(dict(name=name, location=location, basis=basis, authority=authority))
    if path is not None:
        with zipfile.ZipFile(path) as z:
            info = z.getinfo('word/document.xml')
            if info.file_size > 16*1024*1024:
                raise ValueError('正文 XML 超出物料读取上限')
            root = ET.fromstring(z.read(info))
        body = root.find(W+'body')
        paragraphs = body.findall(W+'p') if body is not None else []
        lines = [('paragraph:'+str(i+1), ''.join(t.text or '' for t in p.iter(W+'t'))) for i,p in enumerate(paragraphs)]
        # Explicit labels in cells are authoritative; arbitrary name columns in
        # component/BOM tables deliberately provide no product-identity evidence.
        for ti, table in enumerate(root.iter(W+'tbl'),1):
            for ri, row in enumerate(table.findall(W+'tr'),1):
                cells = [''.join(t.text or '' for t in c.iter(W+'t')) for c in row.findall(W+'tc')]
                for ci, cell in enumerate(cells):
                    if re.fullmatch(LABEL+r'\s*[:：]?', clean(cell)) and ci+1<len(cells):
                        add(cells[ci+1],f'table:{ti}/row:{ri}/cell:{ci+2}','明确名称标签的相邻单元格',0)
                    match = re.fullmatch(LABEL+r'\s*[:：]\s*(.+)',clean(cell))
                    if match: add(match[1],f'table:{ti}/row:{ri}/cell:{ci+1}','单元格明确名称字段',0)
    else:
        lines = [('line:'+str(i+1), value) for i,value in enumerate(text.splitlines())]
    for index,(location,value) in enumerate(lines):
        value=clean(value)
        match=re.fullmatch(LABEL+r'\s*[:：]\s*(.+)',value)
        if match: add(match[1],location,'正文明确名称字段',0)
        # A labelled name on the following line is not an inferred adjacent heading.
        if re.fullmatch(LABEL+r'\s*[:：]?',value) and index+1<len(lines):
            add(lines[index+1][1],lines[index+1][0],'正文名称标签下一段',0)
        title=re.fullmatch(r'(.{1,100}?)(?:技术规范书|技术规格书)',value)
        if title and index<20 and not re.search(r'第.*章|招标文件|通用|专用|固化\s*ID',title[1],re.I):
            add(title[1].strip('《》 （）()'),location,'文档开头的物料专用规范标题',1)
    return select_material(evidence)


def enrich_materials(result, base):
    """Keep raw extraction and manual decisions side by side, including conflicts."""
    by_path = {f['storage_path']:f for f in result['files']}
    for file in result['files']:
        if file['role']!='tech_spec': continue
        path=(base/file['storage_path']).resolve()
        try:
            if not path.is_relative_to(base) or not path.is_file(): raise ValueError('路径不合法')
            from hashlib import sha256
            if sha256(path.read_bytes()).hexdigest()!=file['sha256']: raise ValueError('哈希不符')
            if path.suffix.lower()=='.docx': raw=extract_material(path)
            else:
                from .classify import read_content
                raw=extract_material(text=read_content(path))
        except Exception as exc:
            raw=dict(material=None,evidence=[],conflict=False,needs_confirmation=True,
                     reason=f'物料名称读取失败：{type(exc).__name__}；请核对原文或人工纠正')
        file['material_recognition']=raw
    for mapping in result['solidified_id_map']:
        file=by_path[mapping['storage_path']]
        raw=file['material_recognition']
        mapping.update(material=raw['material'],material_evidence=raw['evidence'],
                       material_candidates=list(dict.fromkeys(e['name'] for e in raw['evidence'])),
                       conflict=raw['conflict'],needs_confirmation=raw['needs_confirmation'],
                       material_reason=raw['reason'])
        override=file.get('manual_override',{})
        if override.get('material'):
            mapping.update(material=override['material'],needs_confirmation=False,
                           material_override=override,material_reason='人工确认，原始识别保留在文件 material_recognition')
    # Conflicting names for the same ID in different documents are never suppressed.
    groups={}
    for m in result['solidified_id_map']: groups.setdefault(m['solidified_id'],[]).append(m)
    for group in groups.values():
        names={m['material'] for m in group if m['material']}
        if len(names)>1:
            for m in group:
                m.update(conflict=True,needs_confirmation=True,material_reason='同一固化ID跨文件物料名称冲突')
    result['material_pending_confirmation']=[m for m in result['solidified_id_map'] if m['needs_confirmation']]
    result['material_conflicts']=[m for m in result['solidified_id_map'] if m['conflict']]
    return result
