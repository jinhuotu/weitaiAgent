"""Approved supplier quotes, kept separate from package/target confirmation."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
import re
import zipfile

from .classify import ID_PATTERN
from .ssot import norm, read_ssot


def _price_kind(title):
    title=norm(title)
    if re.search(r'限价|最高|预算|参考',title):return None
    if re.search(r'税率',title):return 'tax_rate'
    if re.search(r'未含税|不含税|税前',title):prefix='net'
    elif re.search(r'含税|税后',title):prefix='gross'
    else:prefix='unspecified'
    if '单价' in title:return prefix+'_unit'
    if re.search(r'合价|总价|金额',title):return prefix+'_total'
    return None


def read_quote_source(path):
    """Read only: never evaluate formulas or trust formula caches as approved values."""
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter
    path=Path(path).resolve()
    if not path.is_file() or path.suffix.lower()!='.xlsx':raise ValueError('请提供可读取的实际报价 XLSX 文件')
    before=sha256(path.read_bytes()).hexdigest()
    try:
        with zipfile.ZipFile(path) as archive:
            if len(archive.infolist())>10000 or sum(i.file_size for i in archive.infolist())>100*1024*1024:
                raise ValueError('报价工作簿解码规模超限，请拆分后导入')
            if any(i.file_size/max(i.compress_size,1)>1000 for i in archive.infolist()):raise ValueError('报价工作簿压缩比超限')
        workbook=load_workbook(path,read_only=True,data_only=False,keep_links=False)
    except (OSError,zipfile.BadZipFile) as exc:raise ValueError('报价工作簿不可读取，请检查文件是否损坏或加密') from exc
    quotes=[];warnings=[];currency=None;currency_evidence=[]
    try:
        for sheet in workbook:
            if sheet.max_row*sheet.max_column>1000000:raise ValueError('报价工作表单元格规模超限，请拆分后导入')
            header=None
            for row_number,row in enumerate(sheet.iter_rows(),1):
                raw=[dict(cell=f'{get_column_letter(index)}{row_number}',value=c.value if isinstance(c.value,(str,int,float,bool,type(None))) else str(c.value)) for index,c in enumerate(row,1)]
                cells=[norm(str(c.value or '')) for c in row]
                for c in row:
                    if isinstance(c.value,str) and re.search(r'币种|金额单位|货币',c.value):
                        if '人民币' in c.value:
                            currency_evidence.append(dict(sheet=sheet.title,cell=c.coordinate,value=c.value,currency='CNY'))
                ids=[i for i,c in enumerate(cells) if re.fullmatch(r'(?:固化|技术规范|技术规格)(?:书)?(?:ID|编码|编号)',c,re.I)]
                units=[i for i,c in enumerate(cells) if re.fullmatch(r'单位|计量单位',c)]
                amounts=[(i,_price_kind(c)) for i,c in enumerate(cells) if _price_kind(c)]
                if len(ids)==len(units)==1 and amounts:
                    header=dict(id=ids[0],unit=units[0],amounts=amounts,row=row_number,titles=cells)
                    continue
                if not header:continue
                found=re.findall(ID_PATTERN,cells[header['id']])
                if not found:continue
                if len(set(found))!=1:
                    warnings.append(dict(type='quote_id_ambiguous',sheet=sheet.title,row=row_number,raw_cells=raw));continue
                code=found[0].replace('－','-')
                for index,kind in header['amounts']:
                    cell=row[index];value=cell.value
                    if value is None:continue
                    item=dict(key=sheet.title+'!'+cell.coordinate,solidified_id=code,kind=kind,
                              title=header['titles'][index],sheet=sheet.title,cell=cell.coordinate,
                              row=cell.row,header_row=header['row'],raw_value=value,raw_cells=raw,
                              unit='%' if kind=='tax_rate' else cells[header['unit']],amount=None)
                    try:
                        number=Decimal(str(value))
                        if not number.is_finite() or number<0 or cell.data_type=='f':raise InvalidOperation()
                        item['amount']=str(value)
                    except (ValueError,InvalidOperation):
                        warnings.append(dict(type='quote_value_pending',message='报价为空、公式或非有限非负数，须人工提供实际值',source=deepcopy(item)))
                    quotes.append(item)
    finally:workbook.close()
    currencies={e['currency'] for e in currency_evidence}
    if len(currencies)==1:currency=next(iter(currencies))
    for item in quotes:item['currency']=currency
    if not quotes:raise ValueError('未识别到固化ID、单位和报价列，请提供含明确列名的报价表或逐项人工输入')
    if sha256(path.read_bytes()).hexdigest()!=before:raise ValueError('报价读取期间文件变化，请重新导入')
    return dict(path=str(path),sha256=before,quotes=quotes,warnings=warnings,currency_evidence=currency_evidence)


def approve_quote_source(session,source,path,*,actor,basis):
    if not all(isinstance(v,str) and v.strip() for v in (actor,basis)):raise ValueError('报价批准须有确认人和原始依据')
    ssot=read_ssot(source);record=read_quote_source(path)
    if record['sha256'] in {d.sha256 for d in ssot.documents}:raise ValueError('招标文件不得作为实际报价')
    record.update(actor=actor,basis=basis,source_type='approved_quote',confirmed_at=datetime.now(timezone.utc).isoformat())
    result=deepcopy(session);result.setdefault('quote_source_history',[]).append(record)
    return result


def quote_catalog(session,source):
    ssot=read_ssot(source);known={m.solidified_id for m in ssot.materials};sources=[];warnings=[]
    for approved in session.get('quote_source_history',[]):
        try:
            actual=read_quote_source(approved['path'])
            if actual['sha256']!=approved['sha256']:raise ValueError('已批准报价文件哈希发生变化')
        except (ValueError,OSError) as exc:
            warnings.append(dict(type='quote_source_invalid',message=str(exc),source=approved['path']));continue
        actual.update({k:deepcopy(approved[k]) for k in ('actor','basis','source_type','confirmed_at')})
        for q in actual['quotes']:q['known_material']=q['solidified_id'] in known
        sources.append(actual);warnings.extend(actual['warnings'])
    return dict(sources=sources,warnings=warnings)


def confirm_source_price(session,source,*,source_sha256,quote_key,package_key,document_id,table,row,column,actor,basis):
    from .goods_inputs import confirm_price
    catalog=quote_catalog(session,source)
    candidates=[(s,q) for s in catalog['sources'] if s['sha256']==source_sha256 for q in s['quotes'] if q['key']==quote_key]
    if not candidates:raise ValueError('报价来源缺失、变更或尚未批准')
    s,q=candidates[-1]
    if not q['known_material'] or q['amount'] is None or not q['currency'] or not q['unit']:raise ValueError('报价物料、金额、币种或单位尚不明确，请先核实')
    result=confirm_price(session,package_key=package_key,document_id=document_id,table=table,row=row,column=column,
                         amount=q['amount'],actor=actor,basis=basis,currency=q['currency'],unit=q['unit'])
    result['price_history'][-1].update(solidified_id=q['solidified_id'],approved_quote_evidence=dict(
        path=s['path'],sha256=s['sha256'],actor=s['actor'],basis=s['basis'],confirmed_at=s['confirmed_at'],quote=deepcopy(q)))
    return result
