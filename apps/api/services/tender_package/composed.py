"""Deterministic composition from TABLE0. No guessed grade or procurement name."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from decimal import Decimal, InvalidOperation
import json
import re
import unicodedata
import zipfile
from xml.etree import ElementTree as ET

W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def load_rules():
    return json.loads(Path(__file__).with_name('cable_codes.json').read_text(encoding='utf-8-sig'))


def normalize(text):
    return unicodedata.normalize('NFKC',text or '').strip()


def parse_model(model, rules=None):
    rules=rules or load_rules()
    model=normalize(model).upper()
    parses=[]
    for insulation in rules['insulation']:
        for conductor in rules['conductor']:
            for sheath in rules['sheath']:
                prefix=insulation+conductor+sheath
                if not model.startswith(prefix): continue
                tail=model[len(prefix):]
                if tail and (len(tail)!=2 or tail[0] not in rules['armor'] or tail[1] not in rules['outer']):continue
                parses.append(dict(model=model,insulation=rules['insulation'][insulation],
                                   conductor=rules['conductor'][conductor],sheath=rules['sheath'][sheath],
                                   armor=rules['armor'][tail[0]] if tail else rules['armor']['0'],
                                   outer=rules['outer'][tail[1]] if tail else rules['sheath'][sheath],
                                   outer_code=tail[1] if tail else rules['sheath_outer_code'].get(sheath),
                                   tokens=dict(insulation=insulation,conductor=conductor or '(省略)',sheath=sheath,armor=tail[:1],outer=tail[1:]),
                                   rules_version=rules['version']))
    if len(parses)!=1:raise ValueError('型号无法按已配置代号唯一解析；需新增经过核对的代号规则或人工确认')
    return parses[0]


def compose_rows(rows, *, rules=None):
    rules=rules or load_rules()
    parameters={key:[] for key in rules['labels']}
    voltage_annotations=[]
    errors=[]
    # Locate requirement column by header; never read the bidder-guarantee column.
    header=rows[0] if rows else []
    value_columns=[i for i,x in enumerate(header) if re.search(r'需求值|要求值|规定值',normalize(x))]
    label_columns=[i for i,x in enumerate(header) if re.fullmatch(r'参数名称|项目名称',normalize(x))]
    if len(value_columns)!=1 or len(label_columns)!=1:
        return dict(name=None,source_type='composed',valid=False,parameters=parameters,raw_rows=rows,
                    issues=[dict(field='table0',message='TABLE0 参数名称列或项目需求值列无法唯一识别')],conflict=False,rules_version=rules['version'])
    vi,li=value_columns[0],label_columns[0]
    for ri,row in enumerate(rows[1:],2):
        if len(row)<=max(vi,li):continue
        label,value=normalize(row[li]),normalize(row[vi])
        evidence=dict(table_index=0,row=ri,column=vi+1,label=row[li],raw_value=row[vi])
        for key,pattern in rules['labels'].items():
            if re.fullmatch(pattern,re.sub(r'\s+','',label)):
                parameters[key].append(dict(value=value,**evidence))
        # kV annotations only in insulation parameter requirement values. Test
        # voltages elsewhere are intentionally excluded from voltage-grade inference.
        if '绝缘' in label and not re.search(r'试验|耐压',label):
            for match in re.finditer(r'[(（]\s*([\d.]+(?:\s*/\s*[\d.]+)?)\s*kV\s*[)）]',value,re.I):
                voltage_annotations.append(dict(value=re.sub(r'\s+','',match[1])+'kV',**evidence))
    def unique(key,items):
        values={i['value'] for i in items if i['value']}
        if len(values)!=1:
            errors.append(dict(field=key,message='必需参数缺失' if not values else '同一参数存在多个不同值',values=sorted(values)))
            return None
        return next(iter(values))
    model=unique('model',parameters['model'])
    conductor=unique('conductor',parameters['conductor'])
    specification=unique('specification',parameters['specification'])
    parameters['voltage_annotations']=voltage_annotations
    voltage=unique('voltage',parameters['voltage'] or voltage_annotations)
    parsed=None
    if model:
        try:parsed=parse_model(model,rules)
        except ValueError as exc:errors.append(dict(field='model',message=str(exc),value=model))
    if conductor:
        normalized=rules['conductor_values'].get(conductor)
        if normalized is None:errors.append(dict(field='conductor',message='导体材质无法解析',value=conductor))
        elif parsed and normalized!=parsed['conductor']:
            errors.append(dict(field='conductor',message='型号隐含导体材质与正文导体材料矛盾',model_value=parsed['conductor'],table_value=conductor,conflict=True))
    if specification:
        spec=re.fullmatch(r'([1-9]\d*)\s*[×xX*]\s*(\d+(?:\.\d+)?)\s*(?:mm2|mm²)?',specification)
        if not spec or Decimal(spec[2])<=0:errors.append(dict(field='specification',message='芯数与截面格式无法唯一解析',value=specification))
        else:specification=f'{int(spec[1])}×{Decimal(spec[2]).normalize():f}mm²'
    if voltage:
        voltage=voltage.replace('KV','kV').replace('kv','kV')
        try:
            numbers=voltage.removesuffix('kV').split('/')
            if not voltage.endswith('kV') or any(Decimal(n)<=0 for n in numbers):raise ValueError()
        except (ValueError,InvalidOperation):errors.append(dict(field='voltage',message='电压单位或数值无效',value=voltage))
        if parameters['voltage'] and voltage_annotations:
            ratings={i['value'].removesuffix('kV').split('/')[-1] for i in voltage_annotations}
            if ratings!={voltage.removesuffix('kV').split('/')[-1]}:
                errors.append(dict(field='voltage',message='额定电压与绝缘参数电压标注矛盾',values=sorted(ratings),conflict=True))
    # Validate explicit outer sheath too: a conductor-consistent model can still
    # contradict its own outer-sheath parameter. Do not silently ignore that field.
    if parameters['outer']:
        outer=unique('outer',parameters['outer'])
        if outer and parsed:
            outer_code=rules['outer_values'].get(outer.upper(),rules['outer_values'].get(outer))
            if outer_code is None:errors.append(dict(field='outer',message='外护套材料无法解析',value=outer))
            elif outer_code!=parsed['outer_code']:
                errors.append(dict(field='outer',message='型号外护套代号与正文外护套材料矛盾',model_value=parsed['outer'],table_value=outer,conflict=True))
    name=None
    components=[]
    if not errors:
        descriptions=[parsed['insulation'],parsed['sheath'],parsed['armor']]
        if parsed['outer']!=parsed['sheath']:descriptions.append(parsed['outer'])
        parts=[('voltage',voltage,parameters['voltage'] or voltage_annotations),('model',model,parameters['model']),
               ('specification',specification,parameters['specification']),('conductor',conductor+'导体',parameters['conductor']),
               ('structure','、'.join(descriptions),parameters['model'])]
        components=[dict(field=k,text=v,evidence=deepcopy(e)) for k,v,e in parts]
        name=' '.join(v for _,v,_ in parts)
    return dict(name=name,source_type='composed',valid=not errors,parameters=parameters,raw_rows=deepcopy(rows),
                issues=errors,conflict=any(e.get('conflict') for e in errors),components=components,
                model_parse=parsed,rules_version=rules['version'],rules_sha256=sha256(json.dumps(rules,sort_keys=True,ensure_ascii=False).encode()).hexdigest())


def compose_document(path, *, expected_sha256=None, rules=None):
    path=Path(path)
    if expected_sha256 and sha256(path.read_bytes()).hexdigest()!=expected_sha256:raise ValueError('技术规范哈希不符')
    with zipfile.ZipFile(path) as z:
        info=z.getinfo('word/document.xml')
        if info.file_size>16*1024*1024:raise ValueError('正文XML大小超限')
        root=ET.fromstring(z.read(info))
    table=root.find('.//'+W+'tbl')
    rows=[] if table is None else [[''.join(t.text or '' for t in cell.iter(W+'t')) for cell in row.findall(W+'tc')] for row in table.findall(W+'tr')]
    return compose_rows(rows,rules=rules)


def compose_materials(classified,root,*,rules=None):
    """Append composition attempts/history, then reuse the completion resolver."""
    from .completion import complete_materials
    result=deepcopy(classified)
    base=Path(root).resolve()
    attempts=result.setdefault('composition_history',[])
    for mapping in result['solidified_id_map']:
        path=(base/mapping['storage_path']).resolve()
        try:
            if not path.is_relative_to(base):raise ValueError('来源路径越界')
            composition=compose_document(path,expected_sha256=mapping['sha256'],rules=rules)
        except Exception as exc:
            composition=dict(name=None,source_type='composed',valid=False,conflict=False,issues=[dict(field='document',message=f'合成读取失败：{type(exc).__name__}；请核对原文件')],parameters={},components=[])
        attempt=dict(solidified_id=mapping['solidified_id'],source_file=mapping['logical_path'],
                     storage_path=mapping['storage_path'],source_sha256=mapping['sha256'],**composition)
        if attempt not in attempts:attempts.append(attempt)
    return complete_materials(result)
