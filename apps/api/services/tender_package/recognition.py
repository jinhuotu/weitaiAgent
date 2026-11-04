"""Single orchestration and explanatory projection of existing module-two results."""
from copy import deepcopy
from .classify import classify
from .composed import compose_materials

ROLE_LABELS=dict(invitation='投标邀请书',notice='正文/投标人须知',tech_spec='技术规范',contract='合同文件',quotation='货物清单/报价',commitment='诚信/廉洁等承诺',integrity='签名/完整性校验',other='其他/待确认',pending='尚未识别')


def recognize(manifest,root,*,overrides=None,previous=None):
    result=classify(manifest,root,overrides=overrides)
    for field in ('material_source_ledger','composition_history'):
        if field in (previous or {}):result[field]=deepcopy(previous[field])
    return compose_materials(result,root)


def describe(classified):
    groups={}
    for m in classified['solidified_id_map']:
        item=groups.setdefault(m['solidified_id'],dict(solidified_id=m['solidified_id'],files=[],material_name=m.get('material'),
            source_type=m.get('material_source_type'),needs_confirmation=False,historical_conflict=False,sources=[],composition_history=[]))
        item['files'].append({k:m[k] for k in ('logical_path','storage_path','sha256')})
        item['needs_confirmation']|=bool(m.get('needs_confirmation'))
        item['historical_conflict']|=bool(m.get('conflict'))
        for source in m.get('material_sources',[]):
            if source not in item['sources']:item['sources'].append(deepcopy(source))
    for item in groups.values():
        item['composition_history']=[deepcopy(a) for a in classified.get('composition_history',[]) if a['solidified_id']==item['solidified_id']]
        item['selected_sources']=[s for s in item['sources'] if s.get('active',True) and s.get('valid') and s.get('name')==item['material_name'] and s.get('source_type')==item['source_type']]
    from collections import Counter
    return dict(role_labels=ROLE_LABELS,role_counts=classified.get('role_counts') or dict(Counter(f.get('role','other') for f in classified.get('files',[]))),id_count=len(groups),entries=list(groups.values()),
        pending_ids=[m['solidified_id'] for m in groups.values() if m['needs_confirmation']],
        ssot_contract='tender-package-material-map/1',
        ssot_ready=bool(groups) and not any(m['needs_confirmation'] for m in groups.values()),
        note='复用 classify → composed → completion；未确认结果不可当作模块二最终映射，不调用生成器')
