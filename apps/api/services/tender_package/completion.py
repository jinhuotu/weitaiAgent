"""Traceable material completion. No imports from tender generation or routers."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from io import StringIO, BytesIO
from pathlib import Path
import csv
import re
import zipfile

from .classify import ID_PATTERN


class MappingError(ValueError):
    pass


def exact_id(value):
    # Identifiers are opaque: preserve leading zeros and require the complete cell.
    value = str(value or '').strip()
    return value if re.fullmatch(ID_PATTERN, value) else None


def parse_mapping(data, filename, *, id_column=None, name_column=None, encoding=None, sheet=None):
    """Columns are zero-based indexes. Ambiguity requires explicit selection.

    XLSX uses cached values only; formulas never execute. Legacy XLS is rejected
    with a conversion instruction rather than interpreting binary data as CSV.
    """
    if len(data)>16*1024*1024:
        raise MappingError('映射表超过16MB；请拆分后导入')
    suffix=Path(filename).suffix.lower()
    if suffix=='.xlsx':
        try:
            with zipfile.ZipFile(BytesIO(data)) as z:
                if sum(i.file_size for i in z.infolist())>64*1024*1024:
                    raise MappingError('Excel 解压大小超限；请精简工作簿')
            from openpyxl import load_workbook
            wb=load_workbook(BytesIO(data),read_only=True,data_only=True,keep_links=False)
            try:
                if sheet is None and len(wb.sheetnames)!=1:
                    raise MappingError('工作簿有多个工作表；请指定 sheet：'+', '.join(wb.sheetnames))
                if sheet is not None and sheet not in wb.sheetnames:
                    raise MappingError('指定工作表不存在')
                ws=wb[sheet or wb.sheetnames[0]]
                rows=[]
                for i,row in enumerate(ws.iter_rows(values_only=True)):
                    if i>=10000 or len(row)>256: raise MappingError('Excel 行列超限；请精简映射表')
                    rows.append([str(x).strip() if x is not None else '' for x in row])
                sheet=ws.title
            finally: wb.close()
        except MappingError: raise
        except Exception as exc: raise MappingError('Excel 无法读取；请检查文件或转换 CSV') from exc
    elif suffix in ('.csv','.tsv'):
        text=None
        for codec in ([encoding] if encoding else ['utf-8-sig','gb18030']):
            try: text=data.decode(codec);break
            except (UnicodeError,LookupError): pass
        if text is None: raise MappingError('编码无法识别；请指定编码或转换为 UTF-8 CSV')
        try: dialect=csv.Sniffer().sniff(text[:8192],delimiters=',;\t')
        except csv.Error: dialect=csv.excel_tab if suffix=='.tsv' else csv.excel
        rows=[]
        for i,row in enumerate(csv.reader(StringIO(text),dialect)):
            if i>=10000 or len(row)>256: raise MappingError('映射表行列超限；请拆分')
            rows.append([x.strip() for x in row])
    else: raise MappingError('请使用 XLSX、CSV 或 TSV；旧版 XLS 请另存为 XLSX')
    if not rows: raise MappingError('映射表为空')
    width=max(map(len,rows),default=0)
    header_index=None
    for i,row in enumerate(rows[:30]):
        if any(re.search(r'固化.*id|solidified.*id|物料.*名称|物资.*名称|material.*name',x,re.I) for x in row):
            header_index=i;break
    first_data=header_index+1 if header_index is not None else 0
    header=rows[header_index] if header_index is not None else []
    def choose(explicit,kind):
        if explicit is not None:
            if not isinstance(explicit,int) or not 0<=explicit<width: raise MappingError('列号越界；列号从0开始')
            return explicit
        pattern=r'固化.*id|solidified.*id' if kind=='id' else r'(?:物料|物资|货物|设备|产品|标的).*名称|material.*name|item.*name'
        named=[i for i,v in enumerate(header) if re.search(pattern,v,re.I)]
        if len(named)==1:return named[0]
        if len(named)>1:raise MappingError(f'{kind}列不唯一；请指定列号')
        if kind=='id':
            candidates=[i for i in range(width) if any(i<len(r) and exact_id(r[i]) for r in rows[first_data:])]
        else:
            # Only infer names if exactly one remaining non-ID textual column exists.
            candidates=[i for i in range(width) if i!=id_col and any(i<len(r) and r[i] and not exact_id(r[i]) and not re.fullmatch(r'[\d.]+',r[i]) for r in rows[first_data:])]
        if len(candidates)!=1:raise MappingError(f'无法唯一识别{kind}列；请指定列号（从0开始）')
        return candidates[0]
    id_col=choose(id_column,'id');name_col=choose(name_column,'name')
    if id_col==name_col:raise MappingError('固化ID列与物料名称列不能相同')
    digest=sha256(data).hexdigest()
    entries,issues=[],[]
    for i,row in enumerate(rows[first_data:],first_data+1):
        if not any(row):continue
        code=exact_id(row[id_col] if id_col<len(row) else '')
        name=row[name_col].strip() if name_col<len(row) else ''
        errors=[]
        if not code:errors.append('固化ID格式无效，不做近似匹配')
        if not name:errors.append('物料名称为空')
        entry=dict(solidified_id=code,name=name,source_type='mapping',source_file=filename,
                   source_sha256=digest,sheet=sheet,row=i,id_column=id_col,name_column=name_col,
                   basis='映射表完整固化ID精确匹配',valid=not errors)
        entries.append(entry)
        if errors:issues.append(dict(row=i,solidified_id=code,messages=errors))
    groups={}
    for entry in entries:
        if entry['solidified_id']:groups.setdefault(entry['solidified_id'],[]).append(entry)
    for code,group in groups.items():
        if len(group)>1:
            conflict=len({e['name'] for e in group})>1
            issues.append(dict(solidified_id=code,rows=[e['row'] for e in group],messages=['重复ID，名称冲突' if conflict else '重复ID，名称一致；已保留所有行']))
            for entry in group:entry['duplicate']=True;entry['conflict']=conflict
    return dict(entries=entries,issues=issues,columns=dict(id=id_col,name=name_col),source_file=filename,source_sha256=digest)


def manual_confirmation(code,name,actor,basis,*,category=None):
    if not exact_id(code) or not str(name).strip() or not str(actor).strip() or not str(basis).strip():
        raise MappingError('人工确认必须填写完整固化ID、名称、确认人和依据')
    return dict(solidified_id=code,name=str(name).strip(),source_type='manual',actor=str(actor).strip(),
                basis=str(basis).strip(),confirmed_at=datetime.now(timezone.utc).isoformat(),valid=True,category=category)


def complete_materials(classified, *, imports=(), confirmations=(), categories=None):
    """Immutable application of an append-only source ledger, with deterministic priority."""
    imports=tuple(imports)
    result=deepcopy(classified)
    # Original classification is immutable across successive completion calls.
    if 'original_material_map' not in result:
        result['original_material_map']=deepcopy(result['solidified_id_map'])
    ledger=deepcopy(result.get('material_source_ledger',[]))
    for table in imports:
        ledger.extend(deepcopy(table['entries']))
    ledger.extend(deepcopy(list(confirmations)))
    known={m['solidified_id'] for m in result['solidified_id_map']}
    for record in ledger:
        if record.get('source_type') not in ('mapping','manual'):
            raise MappingError('外部补齐来源类型无效')
        if record['source_type']=='manual' and not all(record.get(k) for k in ('actor','basis','confirmed_at')):
            raise MappingError('人工记录缺少确认人、时间或依据')
    for index, record in enumerate(ledger):
        record.setdefault('record_id', f'source-{index}')
        record.setdefault('active', True)
    result['material_source_ledger']=ledger
    result['mapping_import_issues']=[*deepcopy(result.get('mapping_import_issues',[])),*[issue for table in imports for issue in table['issues']]]
    result['unmatched_source_entries']=[e for e in ledger if e.get('solidified_id') not in known]
    for mapping in result['solidified_id_map']:
        code=mapping['solidified_id']
        sources=[deepcopy(e) for e in ledger if e.get('solidified_id')==code]
        for raw in result['original_material_map']:
            if raw['solidified_id']!=code:continue
            for evidence in raw.get('material_evidence',[]):
                sources.append(dict(solidified_id=code,name=evidence['name'],source_type='extracted',
                                    source_file=raw['logical_path'],source_sha256=raw['sha256'],
                                    basis=deepcopy(evidence),valid=True))
            # Preserve historical human overrides as manual, never relabel as extraction.
            if raw.get('material_override'):
                override=raw['material_override']
                sources.append(dict(solidified_id=code,name=raw['material'],source_type='manual',
                                    actor=override.get('actor'),basis=override.get('reason'),
                                    confirmed_at=override.get('confirmed_at'),valid=True,legacy=True))
        attempts={}
        for attempt in result.get('composition_history',[]):
            if attempt['solidified_id']==code:
                attempts[attempt['storage_path']]=attempt
        sources.extend(deepcopy(list(attempts.values())))
        active=[e for e in sources if e.get('active',True)]
        valid=[e for e in active if e.get('valid') and e.get('name')]
        priorities={'mapping':0,'manual':1,'composed':2,'extracted':3}
        best=min((priorities[e['source_type']] for e in valid),default=None)
        chosen=[e for e in valid if priorities[e['source_type']]==best]
        names={e['name'] for e in chosen}
        all_names={e['name'] for e in valid}
        conflict=len(all_names)>1 or any(e.get('conflict') for e in active)
        authoritative=bool(chosen and chosen[0]['source_type'] in ('mapping','manual') and len(names)==1)
        # A named human/authority decision resolves automatic composition issues;
        # retain those issues and source disagreements as history.
        unresolved_conflict=conflict
        if authoritative:
            non_composed=[e for e in valid if e['source_type']!='composed']
            unresolved_conflict=len({e['name'] for e in non_composed})>1
        invalid=any(not e.get('valid',False) for e in active if not (authoritative and e['source_type']=='composed'))
        selected=next(iter(names)) if len(names)==1 else None
        mapping.update(material=selected,material_source_type=chosen[0]['source_type'] if selected else None,
                       material_sources=sources,conflict=conflict,needs_confirmation=selected is None or unresolved_conflict or invalid,
                       composition_resolved_by_authority=authoritative and bool(attempts),
                       category=(categories or {}).get(code,mapping.get('category')),
                       material_reason='多来源名称冲突，请核对全部依据' if conflict else '映射记录有空值或错误，需核对' if invalid else
                       '名称已按 mapping > manual > composed > extracted 优先级补齐' if selected else '无可靠名称来源，待确认；禁止推测填充')
    result['material_pending_confirmation']=[m for m in result['solidified_id_map'] if m['needs_confirmation']]
    result['material_conflicts']=[m for m in result['solidified_id_map'] if m['conflict']]
    return result


def retire_source(classified, record_id, actor, basis):
    """Correct a bad import/confirmation without deleting the original source."""
    if not str(actor).strip() or not str(basis).strip():
        raise MappingError('纠正记录必须填写确认人和依据')
    result=deepcopy(classified)
    matches=[e for e in result.get('material_source_ledger',[]) if e['record_id']==record_id]
    if len(matches)!=1: raise MappingError('来源记录不存在或不唯一')
    entry=matches[0]
    entry['active']=False
    entry.setdefault('corrections',[]).append(dict(actor=actor,basis=basis,confirmed_at=datetime.now(timezone.utc).isoformat()))
    return complete_materials(result)
