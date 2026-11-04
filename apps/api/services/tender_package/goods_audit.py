"""Explain pending offers and apply explicit row decisions through public APIs."""
from copy import deepcopy
from collections import Counter
from datetime import datetime, timezone
import re
from .ssot import read_ssot,digest,norm
from .goods_inputs import compile_inputs,confirm_offer
from .goods_adapter import response_status


def _category(row,conflict=False):
    demand=norm(row['requirement']);value=norm(row.get('guarantee') or '')
    if conflict:return '来源冲突','原人工确认与产品文件不一致'
    if not value:return '缺失保证值','缺少可采用的有依据保证值'
    if not demand:return '组件/结构描述','原表没有单一要求值列，须结合原行核对适用性'
    if '不适用' in value or '不涉及' in demand or '不涉及' in value:return '适用性','需要确认适用范围，不能据文字直接判定'
    if '投标人提供' in demand or '供应商提供' in demand:return '投标人自报参数','有文件声明值，仍需确认实际供货保证及符合性'
    if '标称' in demand or '%' in demand:return '相对/派生指标','比例或基准值未在当前比较规则中核实'
    if re.search(r'\d\s*[-~～至]\s*\d',demand):return '范围/区间','范围及附注需人工核对'
    if re.fullmatch(r'-\d+(?:\.\d+)?',demand):return '有符号数值','上下限语义不由数值大小自行推断'
    if '(' in demand or '（' in demand:return '带条件数值','数值附带电压、材料、规格等条件，需确认条件一致'
    return '文本/等级','文本、等级或复合含义未由现有规则判定'


def build_review_catalog(source,session):
    ssot=read_ssot(source);options=compile_inputs(session,ssot);docs={d.document_id:d for d in ssot.documents}
    matched=options['input_provenance'].get('product_matching',{})
    conflicts={(w['solidified_id'],w['target']['document_id']+':'+w['target']['locator']):deepcopy(w)
               for w in matched.get('warnings',[]) if w['type']=='product_parameter_conflict'}
    rows=[];automatic=[]
    for material in ssot.materials:
        for point in material.parameter_points:
            doc=docs[point.document_id];block=next(b for b in doc.blocks if b.locator==point.locator)
            headers=[b for b in doc.blocks if b.table_index==block.table_index and b.row and b.row<block.row]
            header=next((b for b in headers if any(re.fullmatch(r'参数名称|名称',norm(c)) for c in b.cells)),None)
            def column(pattern):
                indices=[i for i,c in enumerate(header.cells) if re.search(pattern,norm(c))] if header else []
                return block.cells[indices[0]] if len(indices)==1 and indices[0]<len(block.cells) else ''
            target=point.document_id+':'+point.locator
            offer=options['offers'].get(material.solidified_id,{}).get(target)
            requirement=column(r'需求值|要求值|规定值|要求表述')
            status,value,reason=response_status(requirement,offer)
            row=dict(solidified_id=material.solidified_id,target=target,label=column(r'^(?:参数名称|名称)$'),
                     unit=column(r'^单位$'),requirement=requirement,guarantee=value,status=status,reason=reason,
                     source=point.model_dump(),raw_cells=list(block.cells),offer=deepcopy(offer))
            row['source_document']=dict(document_id=doc.document_id,logical_path=doc.logical_path,
                                        storage_path=doc.storage_path,sha256=doc.sha256)
            key=(material.solidified_id,target)
            if key in conflicts:row['conflict']=conflicts[key]
            if status=='待确认':
                row['parameter_type'],row['review_reason']=_category(row,key in conflicts);rows.append(row)
            else:automatic.append(row)
    groups=Counter((r['parameter_type'],r['review_reason']) for r in rows)
    return dict(schema_version='goods-pending-review/1',ssot_sha256=digest(ssot.model_dump()),
                pending_count=len(rows),automatic_count=len(automatic),pending=rows,automatic=automatic,
                groups=[dict(parameter_type=k[0],review_reason=k[1],count=v) for k,v in groups.items()],
                lots=[dict(key=lot.key,fields={k:v.model_dump() for k,v in lot.fields.items()}) for lot in ssot.lots],
                materials=[dict(solidified_id=m.solidified_id,name=m.material_mapping['material_name']) for m in ssot.materials],
                pricing_templates=deepcopy(session['price_templates']))


def confirm_pending_rows(source,session,decisions,*,actor,basis):
    """No inferred decisions, no broad 'all conform' flag, no changes to passed rows."""
    if not isinstance(actor,str) or not actor.strip() or not isinstance(basis,str) or not basis.strip():raise ValueError('核对须有确认人和依据')
    if not decisions:raise ValueError('须逐项选择已核对的参数及响应/偏离结论')
    catalog=build_review_catalog(source,session);pending={(r['solidified_id'],r['target']):r for r in catalog['pending']}
    result=deepcopy(session);seen=set();events=[]
    for decision in decisions:
        key=(decision['solidified_id'],decision['target'])
        if key in seen or key not in pending:raise ValueError('目标不在待核对集合中或重复；已自动通过项保持不变')
        seen.add(key);row=pending[key]
        if decision.get('status') not in ('响应','偏离'):raise ValueError('每项须明确选择响应或偏离，不自动预选')
        value=decision.get('guarantee',row['guarantee'])
        if not isinstance(value,str) or not value.strip():raise ValueError('缺少保证值的冲突项须先明确填写最终值')
        row_basis=decision.get('basis') or basis
        prior=deepcopy(row)
        event=confirm_offer(dict(targets=[dict(solidified_id=key[0],target=key[1])],offer_history=[]),
                            key[0],key[1],value,actor=actor,basis=row_basis,status=decision['status'])['offer_history'][0]
        result['offer_history'].append(event)
        state,_,reason=response_status(row['requirement'],event)
        if state=='待确认':raise ValueError('人工状态与可计算结果矛盾：'+reason)
        events.append(dict(solidified_id=key[0],target=key[1],prior=prior,
                           decision=deepcopy(result['offer_history'][-1])))
    result.setdefault('review_history',[]).append(dict(actor=actor,basis=basis,decisions=events,
                                                       confirmed_at=datetime.now(timezone.utc).isoformat()))
    # Every previously determined row keeps precisely the same value and status.
    after=build_review_catalog(source,result)
    after_rows={(r['solidified_id'],r['target']):r for r in after['automatic']}
    for row in catalog['automatic']:
        other=after_rows.get((row['solidified_id'],row['target']))
        if other is None or (other['guarantee'],other['status'])!=(row['guarantee'],row['status']):raise ValueError('已通过参数发生变化，已中止')
    return result
