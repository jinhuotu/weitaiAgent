"""Thin OOXML adapter over the existing run writer. No table reconstruction."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import re
import zipfile
from lxml import etree

W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def cell_text(cell):
    return ''.join(t.text or '' for t in cell.iter(W+'t'))


def patch_docx(source,destination,changes,*,expected_sha256=None,table_fragment=None):
    """Changes: [{table:1-based,row:1-based,column:1-based,text:...}].

    Refuses to overwrite existing source text. Unchanged members are byte-identical.
    A table fragment copies an existing form, not a newly synthesized table.
    """
    source=Path(source);destination=Path(destination)
    if destination.exists():raise ValueError('输出文件已存在，禁止覆盖')
    if expected_sha256 and sha256(source.read_bytes()).hexdigest()!=expected_sha256:raise ValueError('模板哈希不符')
    from api.services.tenders.pdf_format import _set_run_text
    from docx.oxml import OxmlElement
    with zipfile.ZipFile(source) as package:
        xml=package.read('word/document.xml')
        tree=etree.fromstring(xml);original=deepcopy(tree)
        body=tree.find(W+'body');tables=body.findall(W+'tbl')
        applied=[];rejected=[];seen=set()
        for change in changes:
            key=(change['table'],change['row'],change['column'])
            if key in seen:raise ValueError('指定填充单元格重复')
            seen.add(key)
            try:
                table=tables[key[0]-1]
                cell=table.findall(W+'tr')[key[1]-1].findall(W+'tc')[key[2]-1]
            except IndexError as exc:raise ValueError('指定填充位置不存在') from exc
            if any(i<=0 for i in key):raise ValueError('填充位置须为正整数')
            if cell_text(cell).strip():
                rejected.append(dict(**change,reason='原单元格非空，保留原文，需人工核对'))
                continue
            if cell.find(W+'tcPr/'+W+'vMerge') is not None:
                rejected.append(dict(**change,reason='合并单元格需人工确认填充位置'))
                continue
            paragraph=cell.find(W+'p')
            if paragraph is None:raise ValueError('单元格无原始段落，禁止重建表结构')
            runs=paragraph.findall(W+'r')
            run=runs[0] if runs else OxmlElement('w:r')
            if not runs:paragraph.append(run)
            _set_run_text(run,str(change['text']))
            applied.append(deepcopy(change))
        # Undo permitted cell edits on a verification copy. Every other OOXML
        # node must be identical, including table headers, widths and row order.
        verification=deepcopy(tree)
        original_tables=original.find(W+'body').findall(W+'tbl')
        verification_tables=verification.find(W+'body').findall(W+'tbl')
        for change in applied:
            ti,ri,ci=change['table']-1,change['row']-1,change['column']-1
            current=verification_tables[ti].findall(W+'tr')[ri].findall(W+'tc')[ci]
            old=original_tables[ti].findall(W+'tr')[ri].findall(W+'tc')[ci]
            current.getparent().replace(current,deepcopy(old))
        if etree.tostring(verification,method='c14n')!=etree.tostring(original,method='c14n'):
            raise ValueError('指定单元格以外的原始结构发生变化')
        if table_fragment is not None:
            table=tables[table_fragment-1]
            keep={table}
            previous=table.getprevious()
            # Keep immediate original caption paragraphs, without fabricating one.
            while previous is not None and previous.tag==W+'p' and not cell_text(previous).strip():
                keep.add(previous);previous=previous.getprevious()
            if previous is not None and previous.tag==W+'p' and len(cell_text(previous))<160:keep.add(previous)
            for child in list(body):
                if child not in keep and child.tag!=W+'sectPr':body.remove(child)
        edited=etree.tostring(tree,xml_declaration=True,encoding='UTF-8',standalone=True)
        destination.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(destination,'x') as out:
            for info in package.infolist():
                out.writestr(info,edited if info.filename=='word/document.xml' else package.read(info.filename))
    with zipfile.ZipFile(source) as src,zipfile.ZipFile(destination) as dst:
        if src.namelist()!=dst.namelist():raise ValueError('DOCX包成员被修改')
        if any(src.read(n)!=dst.read(n) for n in src.namelist() if n!='word/document.xml'):raise ValueError('DOCX非正文资源被修改')
    return dict(applied=applied,rejected=rejected,unchanged_other_members=True,structure_verified=True,table_fragment=table_fragment)
