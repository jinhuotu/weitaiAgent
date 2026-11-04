"""Human business checks over frozen draft and actual library candidates."""
from copy import deepcopy
from datetime import datetime,timezone
from hashlib import sha256
from pathlib import Path
from .ssot import read_ssot
from .goods_adapter import source_path
from .goods_inputs import select_library_files


def business_catalog(source,session,draft,library):
    ssot=read_ssot(source);items=[]
    if not draft or not library:
        return dict(items=[],message='未加载商务草稿或资料库候选快照，不能确认商务核验完成',complete=False)
    latest={e['item_key']:e for e in session.get('business_review_history',[])}
    for b in draft['business']:
        for outcome in b['outcomes']:
            key=b['package_key']+'/'+outcome['key']
            items.append(dict(key=key,slot_key=outcome['key'],kind='material',title=outcome.get('request_title',outcome['key']),
                package_key=b['package_key'],sources=deepcopy(outcome.get('ssot_sources',[])),
                candidates=deepcopy(library.get('candidate_media',{}).get(outcome['key'],[])),review=deepcopy(latest.get(key))))
        key=b['package_key']+'/original_form'
        items.append(dict(key=key,kind='original_form',title='商务原版表及业务内容核对',package_key=b['package_key'],review=deepcopy(latest.get(key))))
    return dict(items=items,complete=bool(items) and all(i['review'] for i in items),
        message='核验保存不改草稿正文；真实资料选择须用于下一次生成，原表还须重新渲染逐页核对',
        documents=[dict(document_id=d.document_id,path=d.logical_path,sha256=d.sha256,
                        locators=[b.locator for b in d.blocks]) for d in ssot.documents if d.role not in ('integrity','tech_spec')])


def confirm_business(source,session,arguments,*,root,draft,library):
    args=deepcopy(arguments);actor=args.get('actor');basis=args.get('basis')
    if not all(isinstance(v,str) and v.strip() for v in (actor,basis)):raise ValueError('商务核验须填写确认人和依据')
    catalog=business_catalog(source,session,draft,library)
    item=next((i for i in catalog['items'] if i['key']==args.get('item_key')),None)
    if item is None:raise ValueError('商务项不在本次真实草稿清单中')
    decision=args.get('decision');result=deepcopy(session);evidence=None
    if item['kind']=='original_form':
        if decision!='original_form':raise ValueError('须明确选择商务原表核验，不能以资料缺失代替')
        ssot=read_ssot(source);doc=next((d for d in ssot.documents if d.document_id==args.get('document_id')),None)
        if doc is None or doc.role in ('tech_spec','integrity'):raise ValueError('请指定本次商务原表的真实来源文件')
        block=next((b for b in doc.blocks if b.locator==args.get('locator')),None)
        if not block or not block.text.strip():raise ValueError('商务原表须有非空且存在的段落/表行定位')
        path=source_path(doc,root)
        evidence=dict(document_id=doc.document_id,path=str(path),sha256=doc.sha256,source=block.model_dump())
    elif decision=='real_files':
        if not item['sources']:raise ValueError('商务要求缺少原文定位，请补齐来源后核验')
        previous=session.get('business_library_history',[])
        snapshot=previous[-1]['snapshot'] if previous else library
        selected=select_library_files(snapshot,{item['slot_key']:args.get('document_ids',[])},actor=actor,basis=basis)
        result.setdefault('business_library_history',[]).append(dict(snapshot=selected,actor=actor,basis=basis,confirmed_at=datetime.now(timezone.utc).isoformat()))
        evidence=dict(requirements=item['sources'],files=[c for c in item['candidates'] if c['document_id'] in args['document_ids']])
    elif decision=='missing':
        if not item['sources']:raise ValueError('商务要求缺少原文定位，不能无源确认')
        selected=session.get('business_library_history',[])
        snapshot=selected[-1]['snapshot'] if selected else library
        if not snapshot.get('loaded') or item['candidates'] or snapshot.get('media',{}).get(item['slot_key']) or item['slot_key'] in snapshot.get('unresolved_keys',[]):
            raise ValueError('资料库尚未核实或存在真实候选，不能确认确无资料并生成占位')
        evidence=dict(requirements=item['sources'],library_loaded=True,candidates=[],action='保留带文字占位，正式投标前补齐')
    else:raise ValueError('请明确选择真实资料或确无资料，不自动判断')
    event=dict(item_key=item['key'],decision=decision,actor=actor,basis=basis,evidence=evidence,
               prior=deepcopy(item['review']),confirmed_at=datetime.now(timezone.utc).isoformat(),status='reviewed_pending_regeneration')
    result.setdefault('business_review_history',[]).append(event)
    return result
