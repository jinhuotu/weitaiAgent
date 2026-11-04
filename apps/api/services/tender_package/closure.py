"""Module-two closure contract. No SSOT aggregation or generation integration."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json

from .completion import MappingError, complete_materials, manual_confirmation
from .composed import load_rules, parse_model

SCHEMA_VERSION = 'tender-package-material-map/1'


def confirm_material(classified, code, *, conductor, name, actor, basis, model=None, name_derivation=None):
    """Record the actual human decision, never infer it from a request to close."""
    rules=load_rules()
    normalized=rules['conductor_values'].get(str(conductor).strip())
    if not normalized:
        raise MappingError('人工确认的导体材质不在已配置词表中，请明确材质或维护词表')
    matches=[m for m in classified['solidified_id_map'] if m['solidified_id']==code]
    if not matches:
        raise MappingError('人工确认ID不存在于本次物料清单中')
    entry=manual_confirmation(code,name,actor,basis)
    entry['confirmed_parameters']={'conductor':normalized}
    if model is not None:
        try:
            parsed=parse_model(model,rules)
        except ValueError as exc:
            raise MappingError(str(exc)) from exc
        if parsed['conductor']!=normalized:
            raise MappingError('人工确认的型号与导体材质仍然矛盾')
        entry['confirmed_parameters']['model']=parsed['model']
    if name_derivation is not None:
        entry['name_derivation']=deepcopy(name_derivation)
    entry['source_documents']=[dict(logical_path=m['logical_path'],storage_path=m['storage_path'],sha256=m['sha256']) for m in matches]
    # A name explicitly containing a contrary conductor cannot certify consistency.
    for material in set(rules['conductor_values'].values()):
        if material != normalized and (material+'导体' in name or material+'芯' in name):
            raise MappingError('最终名称中的导体材质与人工确认材质矛盾')
    return complete_materials(classified,confirmations=[entry])


def _digest(data):
    return sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def build_final_mapping(classified, root, *, expected_ids=None):
    """Export only complete, traceable selections; never turn a pending item final."""
    current=complete_materials(classified)
    mappings=current['solidified_id_map']
    if not mappings: raise MappingError('物料映射为空，不能收口')
    ids={m['solidified_id'] for m in mappings}
    if expected_ids is not None and ids!=set(expected_ids):
        raise MappingError('最终固化ID集合与预期不一致，存在缺失或多余ID')
    if current['material_pending_confirmation']:
        raise MappingError('尚有待确认ID：'+', '.join(sorted({m['solidified_id'] for m in current['material_pending_confirmation']})))
    base=Path(root).resolve()
    entries=[]
    for code in sorted(ids):
        group=[m for m in mappings if m['solidified_id']==code]
        names={m['material'] for m in group}
        types={m['material_source_type'] for m in group}
        if len(names)!=1 or len(types)!=1 or not all(names):
            raise MappingError('同一ID最终名称或来源类型不唯一：'+code)
        name=next(iter(names));source_type=next(iter(types))
        files=[];selected=[]
        for mapping in group:
            path=(base/mapping['storage_path']).resolve()
            if not path.is_relative_to(base) or not path.is_file():raise MappingError('来源文件越界或不存在：'+code)
            if sha256(path.read_bytes()).hexdigest()!=mapping['sha256']:raise MappingError('来源文件完整性校验失败：'+code)
            files.append(dict(logical_path=mapping['logical_path'],storage_path=mapping['storage_path'],sha256=mapping['sha256']))
            sources=[s for s in mapping['material_sources'] if s.get('active',True) and s.get('valid') and s.get('name')==name and s['source_type']==source_type]
            if not sources:raise MappingError('最终名称缺少可追溯来源：'+code)
            for source in sources:
                if source_type=='manual' and not all(source.get(k) for k in ('actor','basis','confirmed_at','confirmed_parameters')):
                    raise MappingError('人工确认缺少确认人、时间、依据或参数：'+code)
                if source_type=='composed' and not all(source.get(k) for k in ('components','rules_version','source_sha256')):
                    raise MappingError('自动合成缺少参数来源或规则版本：'+code)
                if source_type=='mapping' and not all(source.get(k) for k in ('source_file','source_sha256','row')):
                    raise MappingError('映射表来源信息不完整：'+code)
                if source not in selected:selected.append(deepcopy(source))
        attempts=[deepcopy(a) for a in current.get('composition_history',[]) if a['solidified_id']==code]
        categories={m.get('category') for m in group if m.get('category')}
        if len(categories)>1:raise MappingError('同一ID大类冲突：'+code)
        entries.append(dict(solidified_id=code,material_name=name,source_type=source_type,
                            category=next(iter(categories),None),files=files,selected_sources=selected,
                            composition_history=attempts,all_sources=deepcopy(group[0]['material_sources']),
                            historical_conflict=any(m['conflict'] for m in group),status='confirmed'))
    payload=dict(schema_version=SCHEMA_VERSION,status='final',created_at=datetime.now(timezone.utc).isoformat(),
                 id_count=len(ids),entries=entries,recognition_snapshot=deepcopy(current),
                 recognition_sha256=_digest(current))
    return dict(payload=payload,payload_sha256=_digest(payload))


def write_final_mapping(artifact, destination):
    """New file only: existing snapshots and exported finals are never overwritten."""
    read_final_mapping(artifact)
    with Path(destination).open('x',encoding='utf-8') as stream:
        json.dump(artifact,stream,ensure_ascii=False,indent=2)


def read_final_mapping(source):
    """SSOT preparation entry: validate envelope integrity and return a copy."""
    artifact=json.loads(Path(source).read_text(encoding='utf-8')) if not isinstance(source,dict) else deepcopy(source)
    payload=artifact.get('payload',{})
    if payload.get('schema_version')!=SCHEMA_VERSION or payload.get('status')!='final':raise MappingError('最终映射契约版本或状态无效')
    if artifact.get('payload_sha256')!=_digest(payload):raise MappingError('最终映射摘要校验失败')
    entries=payload.get('entries',[])
    if payload.get('id_count')!=len(entries) or len({e['solidified_id'] for e in entries})!=len(entries):raise MappingError('最终映射ID数量或唯一性校验失败')
    if any(not e.get('material_name') or not e.get('selected_sources') or e.get('status')!='confirmed' for e in entries):raise MappingError('最终映射存在未完成条目')
    return deepcopy(payload)
