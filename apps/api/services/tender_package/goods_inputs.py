"""Append-only business-data overlays; original SSOT is never amended."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from decimal import Decimal, InvalidOperation
import re
from .ssot import read_ssot,digest,norm


def create_input_session(source):
    ssot=read_ssot(source)
    targets=[]
    docs={d.document_id:d for d in ssot.documents}
    for material in ssot.materials:
        for point in material.parameter_points:
            block=next(b for b in docs[point.document_id].blocks if b.locator==point.locator)
            targets.append(dict(solidified_id=material.solidified_id,target=point.document_id+':'+point.locator,
                                source=point.model_dump(),cells=list(block.cells)))
    from .goods_adapter import price_templates,load_rules
    pricing=[dict(document_id=d.document_id,table=b.table_index,header_row=b.row,header_cells=b.cells) for d,b in price_templates(ssot,load_rules())]
    return dict(price_templates=pricing,schema_version='goods-reviewed-inputs/1',ssot_sha256=digest(ssot.model_dump()),targets=targets,
                offer_history=[],price_history=[],package_history=[],warnings=[])


def confirm_offer(session,code,target,guarantee,*,actor,basis,status=None):
    if not all(isinstance(v,str) and v.strip() for v in (guarantee,actor,basis)):raise ValueError('保证值、确认人及依据必填')
    if not any(t['solidified_id']==code and t['target']==target for t in session['targets']):raise ValueError('参数目标不存在')
    if status not in (None,'响应','偏离'):raise ValueError('响应状态无效')
    result=deepcopy(session)
    result['offer_history'].append(dict(solidified_id=code,target=target,guarantee=guarantee,actor=actor,basis=basis,status=status,source_type='manual',confirmed_at=datetime.now(timezone.utc).isoformat()))
    return result


def confirm_price(session,*,package_key,document_id,table,row,column,amount,actor,basis,currency,unit):
    if not all(isinstance(v,str) and v.strip() for v in (actor,basis,currency,unit)):raise ValueError('报价须填写确认人、依据、币种和计价单位')
    try:
        number=Decimal(str(amount))
        if not number.is_finite() or number<0:raise ValueError()
    except (ValueError,InvalidOperation):raise ValueError('实际报价须为非负有限数值')
    if any(not isinstance(i,int) or i<=0 for i in (table,row,column)):raise ValueError('报价目标位置无效')
    result=deepcopy(session)
    result['price_history'].append(dict(package_key=package_key,document_id=document_id,table=table,row=row,column=column,text=str(amount),actor=actor,basis=basis,currency=currency,unit=unit,source_type='manual_quote',confirmed_at=datetime.now(timezone.utc).isoformat()))
    return result


def confirm_packages(session,source,packages,*,actor,basis):
    ssot=read_ssot(source)
    if digest(ssot.model_dump())!=session['ssot_sha256']:raise ValueError('输入会话与SSOT不一致')
    if not actor.strip() or not basis.strip():raise ValueError('标包范围须有确认人及依据')
    known={lot.key for lot in ssot.lots};ids={m.solidified_id for m in ssot.materials};seen=set();keys=set()
    for p in packages:
        if not re.fullmatch(r'[A-Za-z0-9_-]+',str(p.get('key',''))) or p['key'] in keys:raise ValueError('标包输出键无效或重复')
        keys.add(p['key'])
        if p.get('lot_key') not in known or not str(p.get('package_code','')).strip():raise ValueError('须指定SSOT内分标及实际包号')
        for code in p['material_ids']:
            if code not in ids or code in seen:raise ValueError('物料ID未知或重复分配')
            seen.add(code)
    if seen!=ids:raise ValueError('标包分配必须覆盖所有物料')
    result=deepcopy(session)
    result['package_history'].append(dict(packages=deepcopy(packages),actor=actor,basis=basis,confirmed_at=datetime.now(timezone.utc).isoformat()))
    return result


def compile_inputs(session,source):
    ssot=read_ssot(source)
    if digest(ssot.model_dump())!=session['ssot_sha256']:raise ValueError('输入会话与SSOT摘要不符')
    products={p['document_id']:p for p in session.get('product_history',[])}
    matched=match_product_offers(ssot,list(products.values())) if products else None
    offers=deepcopy(matched['offers']) if matched else {};prices={}
    for event in session['offer_history']:
        offers.setdefault(event['solidified_id'],{})[event['target']]=deepcopy(event)
    if matched:
        resolved={(d['solidified_id'],d['target']):d['decision'] for batch in session.get('review_history',[]) for d in batch['decisions']}
        for index,item in enumerate(matched['warnings']):
            if item['type']!='product_parameter_conflict':continue
            target=item['target']['document_id']+':'+item['target']['locator'];key=(item['solidified_id'],target)
            decision=resolved.get(key)
            if decision and offers.get(key[0],{}).get(key[1])==decision:
                matched['warnings'][index]=dict(type='product_parameter_conflict_reviewed',
                    message='已人工裁决；原文件差异及此前确认完整保留，最终保证值见裁决记录',
                    solidified_id=key[0],target=deepcopy(item['target']),original_warning=deepcopy(item),resolution=deepcopy(decision))
    for event in session['price_history']:
        prices[(event['package_key'],event['document_id'],event['table'],event['row'],event['column'])]=deepcopy(event)
    confirmed_packages=session['package_history'][-1]['packages'] if session['package_history'] else None
    warnings=deepcopy(session.get('warnings',[]))
    if matched:warnings.extend(matched['warnings'])
    if prices and (not confirmed_packages or any(e['package_key'] not in {p['key'] for p in confirmed_packages} for e in prices.values())):
        warnings.append(dict(type='price_scope_pending',message='实际报价未绑定已确认标包，未回填价格'))
        prices={k:e for k,e in prices.items() if confirmed_packages and e['package_key'] in {p['key'] for p in confirmed_packages}}
    if session.get('quote_source_history'):
        from .goods_quotes import quote_catalog
        catalog=quote_catalog(session,ssot);warnings.extend(catalog['warnings'])
        valid_hashes={s['sha256'] for s in catalog['sources']}
        for key,event in list(prices.items()):
            evidence=event.get('approved_quote_evidence')
            if not evidence:continue
            package=next((p for p in confirmed_packages or [] if p['key']==event['package_key']),None)
            if evidence['sha256'] not in valid_hashes or not package or event['solidified_id'] not in package['material_ids']:
                warnings.append(dict(type='price_scope_pending',message='报价来源失效或物料未分配至所选确认标包，已扣留',source=deepcopy(event)))
                del prices[key]
        used={(e['approved_quote_evidence']['sha256'],e['approved_quote_evidence']['quote']['key']) for e in prices.values() if e.get('approved_quote_evidence')}
        held=[dict(sha256=s['sha256'],key=q['key'],solidified_id=q['solidified_id']) for s in catalog['sources'] for q in s['quotes'] if (s['sha256'],q['key']) not in used]
        if held:warnings.append(dict(type='approved_quotes_unassigned',message='报价已批准；标包及原表位置尚待人工绑定，未写入正文',quotes=held))
    provenance=deepcopy(session);provenance['warnings']=warnings
    if matched:provenance['product_matching']=matched
    return dict(offers=offers,price_fills=list(prices.values()),packages=deepcopy(session['package_history'][-1]['packages']) if session['package_history'] else None,
                input_provenance=provenance)


def read_product_source(path,document_id,*,solidified_ids=(),binding_actor=None,binding_basis=None,infer_bindings=False):
    """Read actual supplier-product fields, not tender requirement columns."""
    from .goods_product_reader import read_parameters
    path=Path(path);parameters=[]
    if not path.is_file():raise ValueError('产品资料文件不存在，请提供可读取的绝对路径')
    original_hash=sha256(path.read_bytes()).hexdigest()
    if path.suffix.lower()!='.docx':raise ValueError('产品资料目前支持DOCX结构化参数表；其他格式需提供经核对的结构化参数')
    try:parameters=read_parameters(path,infer_bindings=infer_bindings)
    except Exception as exc:raise ValueError('产品DOCX无法解析，请检查文件是否损坏或加密') from exc
    if sha256(path.read_bytes()).hexdigest()!=original_hash:raise ValueError('产品资料读取期间发生变更，请重新导入')
    return dict(document_id=document_id,path=str(path.resolve()),sha256=original_hash,solidified_ids=list(solidified_ids),parameters=parameters,source_type='supplier_product',binding_actor=binding_actor,binding_basis=binding_basis,infer_bindings=infer_bindings)


def match_product_offers(source,products):
    ssot=read_ssot(source);offers={};warnings=[]
    docs={d.document_id:d for d in ssot.documents};tender_hashes={d.sha256 for d in ssot.documents}
    checked=[]
    for product in products:
        path=Path(product['path'])
        if not path.is_file() or sha256(path.read_bytes()).hexdigest()!=product['sha256']:
            warnings.append(dict(type='product_source_invalid',message='产品资料来源文件缺失或哈希不符',source=product['document_id']));continue
        if product['source_type']!='supplier_product' or product['sha256'] in tender_hashes:
            warnings.append(dict(type='product_source_invalid',message='招标要求文件不得用作投标人保证值依据',source=product['document_id']));continue
        infer=product.get('infer_bindings') is True
        if not infer and (not all(isinstance(product.get(k),str) and product[k].strip() for k in ('binding_actor','binding_basis')) or not product.get('solidified_ids')):
            warnings.append(dict(type='product_binding_pending',message='产品资料与本次物料的对应关系尚未经具名确认',source=product['document_id']));continue
        if set(product['solidified_ids'])-{m.solidified_id for m in ssot.materials}:
            warnings.append(dict(type='product_binding_pending',message='产品资料含不在SSOT中的物料ID，请核对绑定',source=product['document_id']));continue
        # Re-read parameters from the actual bytes; caller-provided values are not trusted.
        card=read_product_source(path,product['document_id'],solidified_ids=product['solidified_ids'],binding_actor=product['binding_actor'],binding_basis=product['binding_basis'],infer_bindings=infer)
        known_ids={m.solidified_id for m in ssot.materials}
        if infer:
            unbound=[p for p in card['parameters'] if len(p['solidified_ids'])!=1 or not set(p['solidified_ids'])<=known_ids]
            if unbound:warnings.append(dict(type='product_binding_pending',message='部分参数无法从正文唯一关联本次固化ID，保持待确认',source=product['document_id'],parameters=unbound))
        if any(p['evidence_kind']=='supplier_declared' for p in card['parameters']):
            warnings.append(dict(type='product_declaration_unverified',message='来源为用户提供的保证值声明，未据此认可检测结论、无偏差声明或承诺；仅逐项引用文件值供草稿核对',source=product['document_id']))
        checked.append(card)
    for material in ssot.materials:
        for point in material.parameter_points:
            doc=docs[point.document_id];block=next(b for b in doc.blocks if b.locator==point.locator)
            headers=[b for b in doc.blocks if b.table_index==block.table_index and b.row and b.row<block.row]
            header=next((b for b in headers if any(re.fullmatch(r'参数名称|名称',norm(c)) for c in b.cells)),None)
            if header is None:continue
            li=[i for i,c in enumerate(header.cells) if re.fullmatch(r'参数名称|名称',norm(c))]
            ui=[i for i,c in enumerate(header.cells) if re.fullmatch(r'单位',norm(c))]
            if len(li)!=1 or len(ui)!=1 or max(li[0],ui[0])>=len(block.cells):continue
            label=norm(block.cells[li[0]]);unit=norm(block.cells[ui[0]])
            candidates=[]
            for product in checked:
                for p in product['parameters']:
                    bound=p.get('solidified_ids',[]) if product.get('infer_bindings') else product['solidified_ids']
                    if material.solidified_id not in bound:continue
                    if norm(p['label'])==label and p['unit'] is not None and norm(p['unit'])==unit:
                        candidates.append(dict(value=p['value'],document_id=product['document_id'],path=product['path'],sha256=product['sha256'],locator=p['locator'],raw_cells=p['raw_cells'],binding_actor=product['binding_actor'],binding_basis=product['binding_basis'],binding_mode='document_section' if product.get('infer_bindings') else 'manual',binding_evidence=p.get('binding_evidence'),evidence_kind=p['evidence_kind']))
            confirmed=[]
            for record in material.material_mapping.get('selected_sources',[]):
                for part in record.get('name_derivation',[]):
                    for e in part.get('evidence',[]):
                        if e.get('value_origin')=='manual' and e.get('source_sha256')==doc.sha256 and e.get('table_index',-2)+1==block.table_index and e.get('row')==block.row and e.get('confirmed_value') is not None:
                            confirmed.append(e)
            if candidates and any(norm(c['value'])!=norm(e['confirmed_value']) for c in candidates for e in confirmed):
                warnings.append(dict(type='product_parameter_conflict',message='提供的参数与已有人工确认矛盾，未自动覆盖',solidified_id=material.solidified_id,target=point.model_dump(),candidates=candidates,confirmed_evidence=confirmed));continue
            if len({norm(c['value']) for c in candidates})>1:
                warnings.append(dict(type='product_parameter_conflict',message='真实产品资料同参数存在不同值，未自动择一',solidified_id=material.solidified_id,target=point.model_dump(),candidates=candidates));continue
            if candidates:
                inferred=any(c['binding_mode']=='document_section' for c in candidates)
                offers.setdefault(material.solidified_id,{})[point.document_id+':'+point.locator]=dict(guarantee=candidates[0]['value'],actor='模型自动识别（文档章节固化ID）' if inferred else '资料库产品参数匹配',basis='正文单一固化ID及参数名称、单位精确匹配' if inferred else '已绑定真实产品文件；参数名称及单位精确匹配',source_type='library_product',sources=candidates)
    return dict(offers=offers,warnings=warnings)



def select_library_files(snapshot,selections,*,actor,basis):
    """Select from actual matched candidates; never accept arbitrary file paths."""
    if not actor.strip() or not basis.strip():raise ValueError('资料选择须提供确认人及依据')
    result=deepcopy(snapshot);known=result.get('candidate_media',{})
    if not known:raise ValueError('当前快照不含候选文件，请重新执行只读资料库匹配')
    for key,ids in selections.items():
        if key not in known:raise ValueError('资料项不在候选集合中')
        by_id={c['document_id']:c for c in known[key]}
        if not ids or len(set(ids))!=len(ids) or any(i not in by_id for i in ids):raise ValueError('须选择真实候选文件；不能用空选择声明资料不存在')
        paths=[]
        for code in ids:
            source=by_id[code];path=Path(source['path'])
            if not path.is_file() or sha256(path.read_bytes()).hexdigest()!=source['sha256']:raise ValueError('所选文件已变更，须重新匹配')
            paths.append(str(path))
        result['media'][key]=paths
        result['unresolved_keys']=[k for k in result.get('unresolved_keys',[]) if k!=key]
    result.setdefault('selection_history',[]).append(dict(selections=deepcopy(selections),actor=actor,basis=basis,confirmed_at=datetime.now(timezone.utc).isoformat()))
    return result
