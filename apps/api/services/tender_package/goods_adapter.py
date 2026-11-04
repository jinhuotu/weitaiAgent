"""Goods-package orchestration over existing writers. No legacy entry changes."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
import json
import re
import shutil
import zipfile
from .ssot import read_ssot, digest, norm
from .goods_ooxml import patch_docx


def load_rules():
    return json.loads(Path(__file__).with_name('goods_rules.json').read_text(encoding='utf-8-sig'))


def warning(kind,message,*,source=None,critical=False,**extra):
    return dict(type=kind,message=message,level='critical' if critical else 'warning',source=source,**extra)


def response_status(requirement,offer):
    """Never copy a required value into an unprovided guarantee."""
    if not offer or not all(str(offer.get(k,'')).strip() for k in ('guarantee','actor','basis')):
        return '待确认',None,'未提供有依据的实际保证值'
    value=str(offer['guarantee']).strip()
    demand=norm(requirement);plain=norm(value)
    computed=None
    if demand==plain:computed='响应'
    else:
        match=re.fullmatch(r'(>=|<=|>|<|≥|≤|大于等于|小于等于|不小于|不大于|大于|小于)?(\d+(?:\.\d+)?)',demand)
        if match and re.fullmatch(r'\d+(?:\.\d+)?',plain):
            a,b=Decimal(plain),Decimal(match[2]);op=match[1] or '='
            ok={'=':a==b,'>=':a>=b,'≥':a>=b,'大于等于':a>=b,'不小于':a>=b,'<=':a<=b,'≤':a<=b,'小于等于':a<=b,'不大于':a<=b,'>':a>b,'大于':a>b,'<':a<b,'小于':a<b}[op]
            computed='响应' if ok else '偏离'
    reviewed=offer.get('status')
    if reviewed not in (None,'响应','偏离'):return '待确认',value,'人工状态无效'
    if reviewed and computed and reviewed!=computed:return '待确认',value,'人工状态与可计算的参数结果矛盾'
    status=computed or reviewed
    return (status,value,'参数比较或具名人工依据') if status else ('待确认',value,'复杂参数需要人工确认响应/偏离')


def source_path(doc,root):
    base=Path(root).resolve();path=(base/doc.storage_path).resolve()
    if not path.is_relative_to(base) or not path.is_file():raise ValueError('模板不存在或越界')
    if sha256(path.read_bytes()).hexdigest()!=doc.sha256:raise ValueError('模板哈希与SSOT不一致')
    return path


def technical_response(ssot,material,root,destination,offers,rules):
    documents={d.document_id:d for d in ssot.documents}
    if len(material.document_ids)!=1:raise ValueError('一个固化ID关联多份技术规范，需确认采用的原始模板')
    doc=documents[material.document_ids[0]]
    changes=[];rows=[]
    for point in material.parameter_points:
        block=next(b for b in doc.blocks if b.locator==point.locator)
        headers=[b for b in doc.blocks if b.table_index==block.table_index and b.row and b.row<block.row]
        header=next((b for b in headers if any(re.fullmatch(rules['guarantee_header'],norm(c)) for c in b.cells)),None)
        offer=offers.get(material.solidified_id,{}).get(point.document_id+':'+point.locator)
        requirement=''
        if header:
            indices=[i for i,c in enumerate(header.cells) if re.search(rules['requirement_header'],norm(c))]
            if len(indices)==1 and indices[0]<len(block.cells):requirement=block.cells[indices[0]]
        status,value,basis=response_status(requirement,offer)
        record=dict(solidified_id=material.solidified_id,source=point.model_dump(),requirement=requirement,
                    guarantee=value,status=status,basis=basis,offer=deepcopy(offer),written=False)
        if header:
            cols=[i for i,c in enumerate(header.cells) if re.fullmatch(rules['guarantee_header'],norm(c))]
            statuscols=[i for i,c in enumerate(header.cells) if re.fullmatch(rules['status_header'],norm(c))]
            if len(cols)==1 and cols[0]<len(block.cells):
                ci=cols[0]
                stamp=rules['pending_text'] if value is None else value
                free=[i for i in statuscols if i<len(block.cells) and not block.cells[i].strip()]
                if not free and value is not None:stamp+='（'+status+'）'
                changes.append(dict(table=block.table_index,row=block.row,column=ci+1,text=stamp))
                if free:changes.append(dict(table=block.table_index,row=block.row,column=free[0]+1,text=status))
                record['target']=dict(table=block.table_index,row=block.row,column=ci+1)
        rows.append(record)
    proof=patch_docx(source_path(doc,root),destination,changes,expected_sha256=doc.sha256)
    applied={(c['table'],c['row'],c['column']) for c in proof['applied']}
    for record in rows:
        target=record.get('target')
        record['written']=bool(target and (target['table'],target['row'],target['column']) in applied)
    return dict(solidified_id=material.solidified_id,file=str(destination),rows=rows,proof=proof)


def price_templates(ssot,rules):
    result={}
    for doc in ssot.documents:
        if doc.role not in ('quotation','notice'):continue
        for b in doc.blocks:
            if b.kind!='table_row' or not b.row or b.row>3 or len(b.cells)<4:continue
            # Header evidence only. Prose instructions mentioning prices do not qualify.
            short=[norm(c) for c in b.cells if len(norm(c))<35]
            if any(re.fullmatch(rules['price_quantity_header'],c) for c in short) and any(re.search(rules['price_header'],c) for c in short):
                result.setdefault((doc.document_id,b.table_index),(doc,b))
    return list(result.values())


def business_requests(ssot):
    from api.services.tenders.match import attachment_requests
    requests={}
    for requirement in ssot.requirements:
        if requirement.category not in ('qualification_general','qualification_special','rejection'):continue
        for slot in attachment_requests(str(requirement.fact.value or ''),source_kind='qualification'):
            requests.setdefault((slot.key,slot.title),slot)
    rules=load_rules()
    for doc in ssot.documents:
        if doc.role not in ('notice','invitation'):continue
        for block in doc.blocks:
            if not re.search(rules['business_context'],block.section):continue
            for slot in attachment_requests(block.text,source_kind='format'):
                requests.setdefault((slot.key,slot.title),slot)
    return [slot for slot in requests.values() if not slot.key.startswith('request_') or re.search(rules['business_unknown_title'],slot.title)]


async def resolve_business_library(db,ssot,*,selected_ids=None):
    """Read-only library adapter. No ensure/seed/commit operations."""
    from api.services.tenders.library_kb import get_base_by_public_id,TENDER_LIB_PUBLIC_ID,_list_docs,_is_material_parent,_group_children,item_to_slot,resolve_attachments,_doc_file_path
    from api.services.tenders.match import attachment_catalog_item
    from api.services.tenders.schema import PlaceholderItem
    base=await get_base_by_public_id(db,TENDER_LIB_PUBLIC_ID);docs=await _list_docs(db,base.id)
    children=_group_children(docs)
    catalogue=[PlaceholderItem(key=str(row['key']),title=str(row['title'])) for doc in docs if _is_material_parent(doc) for row in [item_to_slot(doc,children.get(doc.public_id,[]))]]
    slots=business_requests(ssot);mapped=[]
    for slot in slots:
        match=attachment_catalog_item(slot,catalogue)
        mapped.append(match or slot)
    media=await resolve_attachments(db,mapped)
    documents=[(d,p) for d in docs if (p:=_doc_file_path(d))]
    warnings=[];actual={};unresolved=[];candidates={}
    for request,slot in zip(slots,mapped):
        paths=media.get(slot.key,[])
        candidates[request.key]=[dict(document_id=d.public_id,path=str(p),sha256=sha256(p.read_bytes()).hexdigest()) for d,p in documents if p in paths]
        choices=(selected_ids or {}).get(request.key,(selected_ids or {}).get(slot.key))
        if choices is not None:
            allowed={p.resolve() for d,p in documents if d.public_id in choices}
            paths=[p for p in paths if p.resolve() in allowed]
        elif len({d.public_id for d,p in documents if p in paths})>1:
            warnings.append(warning('business_selection_pending','同类资料命中多份，需指定本次实际选用文件',source=slot.title));unresolved.append(request.key);paths=[]
        actual[request.key]=paths
    return dict(slots=slots,media=actual,documents=documents,warnings=warnings,unresolved_keys=unresolved,candidate_media=candidates)


def write_business(destination,library,rules,*,reviewed_texts=()):
    from docx import Document
    from api.services.tenders.placeholders import _draw_box,_insert_slot_media
    from api.services.tenders.schema import PlaceholderItem
    from api.services.tenders.expiry_check import collect_expiry_warnings
    doc=Document();title=doc.add_paragraph(rules['business_title'],style='Title')
    from docx.shared import RGBColor
    for run in title.runs:run.font.color.rgb=RGBColor(0,0,0)
    warnings=list(library.get('warnings',[]));matched=[];media={};outcomes=[]
    slots=library.get('slots') or [PlaceholderItem(key='pending_business',title=rules['missing_business_title'])]
    for slot in slots:
        doc.add_heading(slot.title,level=1)
        requested=[Path(p) for p in library.get('media',{}).get(slot.key,[])]
        unavailable=[p for p in requested if not p.is_file()]
        if slot.key in library.get('unresolved_keys',[]) or library.get('loaded') is False or unavailable:
            doc.add_paragraph('本项真实材料尚待核对选定；未按缺失材料绘制占位框')
            warnings.append(warning('business_unresolved','资料尚未选定或无法读取，未以缺失占位代替',source=slot.title,critical=True))
            outcomes.append(dict(key=slot.key,files=[str(p) for p in requested],kind='selection_pending'))
            continue
        files=requested
        media[slot.key]=files
        if files:
            inserted=0;rendered_files=[]
            for selected in files:
                count=_insert_slot_media(doc,[selected],max_pages=rules['business_image_max_pages'])
                inserted+=count
                if count:rendered_files.append(selected)
            media[slot.key]=rendered_files
            expected=0
            for file in files:
                if file.suffix.lower()=='.pdf':
                    try:
                        from pypdf import PdfReader
                        expected+=len(PdfReader(str(file)).pages)
                    except Exception:expected+=1
                else:expected+=1
            if inserted and inserted<expected:warnings.append(warning('business_render_incomplete','真实资料未全部贴入；请检查页数、格式和容量配置，禁止视作完整',source=slot.title,critical=True))
            if not inserted:warnings.append(warning('business_render_failed','真实文件无法贴图；未以缺失占位代替，请人工处理',source=slot.title,critical=True))
            else:matched.append(dict(key=slot.key,title=slot.title,fileCount=len(rendered_files)))
            outcomes.append(dict(key=slot.key,files=[str(p) for p in files],kind='images' if inserted else 'failed'))
        else:
            _draw_box(doc,slot,wording='此处粘贴'+slot.title+'；请补充经核对的真实资料')
            warnings.append(warning('business_missing','未选用真实资料，已保留带名称的资料位置',source=slot.title,critical=True))
            outcomes.append(dict(key=slot.key,files=[],kind='labelled_placeholder'))
    for item in reviewed_texts:
        if not all(str(item.get(k,'')).strip() for k in ('text','actor','basis')):
            warnings.append(warning('business_text_pending','承诺/声明缺少具名核对依据，未写入正文',critical=True));continue
        # Reuse the existing run writer; never draft facts/commitments by inference.
        from api.services.tenders.pdf_format import _set_run_text
        p=doc.add_paragraph();r=p.add_run();_set_run_text(r._r,item['text'])
    destination.parent.mkdir(parents=True,exist_ok=True);doc.save(destination)
    warnings.extend(collect_expiry_warnings({'matched':matched},media,library.get('documents',[])))
    return dict(file=str(destination),outcomes=outcomes,warnings=warnings)


def generate_goods(ssot_source,root,destination,*,offers=None,packages=None,library=None,price_fills=None,reviewed_business_texts=(),business_forms=(),rules=None,render_adapter=None,input_provenance=None):
    ssot=read_ssot(ssot_source);rules=deepcopy(rules or load_rules())
    if ssot.strategy not in ('goods','pending'):raise ValueError('本适配器仅用于货物类，工程/服务路径保持不变')
    destination=Path(destination)
    if destination.exists():raise ValueError('输出目录必须为新目录')
    destination.mkdir(parents=True)
    warnings=[];technical=[];price=[];business=[];assigned=set()
    if input_provenance:
        warnings.extend(deepcopy(input_provenance.get('warnings',[])))
        for item in input_provenance.get('product_matching',{}).get('warnings',[]):
            if item not in warnings:warnings.append(deepcopy(item))
    materials={m.solidified_id:m for m in ssot.materials}
    if not packages:
        packages=[dict(key='scope_pending',lot_key=None,package_code=None,material_ids=list(materials))]
        warnings.append(warning('package_scope_pending','实际分标/包号未确认，产物放入待分配目录；未把目录中所有分标当作已选包',critical=True))
    if ssot.strategy=='pending':warnings.append(warning('subject_type_pending','标的类型未确认，当前仅生成货物应答草稿',critical=True))
    for path in ssot.pending:
        warnings.append(warning('ssot_pending','SSOT待确认：'+path,source=path,critical=bool(re.search(rules['critical_pattern'],path))))
    for path in ssot.conflicts:
        warnings.append(warning('ssot_conflict','SSOT来源差异需核对：'+path,source=path,critical=bool(re.search(rules['critical_pattern'],path))))
    for message in ssot.warnings:warnings.append(warning('ssot_warning',message))
    for category in ('qualification_general','qualification_special','rejection'):
        if not any(r.category==category for r in ssot.requirements):warnings.append(warning('critical_field_missing','缺少需优先核对的关键条款：'+category,source=category,critical=True))
    if rules['platform_max_package_bytes'] is None or rules['platform_max_file_bytes'] is None:
        warnings.append(warning('platform_limits_pending','未提供本次平台容量限制；不能宣称已符合平台要求，请核对后配置上限',critical=True))
    templates=price_templates(ssot,rules)
    try:
        for package in packages:
            key=str(package['key'])
            if not re.fullmatch(r'[A-Za-z0-9_-]+',key):raise ValueError('标包输出键包含不安全字符')
            if package.get('lot_key') and package['lot_key'] not in {l.key for l in ssot.lots}:raise ValueError('标包引用的分标不在SSOT中')
            if not package.get('lot_key') or not package.get('package_code'):
                warnings.append(warning('package_assignment_pending','当前输出目录尚未形成已确认的分标/包号绑定',source=key,critical=True))
            package_dir=destination/key
            for channel in ('technical','business','price'):(package_dir/channel).mkdir(parents=True)
            for code in package['material_ids']:
                if code not in materials or code in assigned:raise ValueError('固化ID不存在或在包间重复分配')
                if not re.fullmatch(r'[0-9-]+',code):raise ValueError('固化ID不可用作安全输出名')
                assigned.add(code)
                result=technical_response(ssot,materials[code],root,package_dir/'technical'/(code+'.docx'),offers or {},rules)
                result['package_key']=key;technical.append(result)
                for row in result['rows']:
                    if row['status']=='待确认' or not row['written']:warnings.append(warning('technical_pending',row['basis'] if row['written'] else '原表指定位置无法安全填入，需人工核对',source=row['source'],solidified_id=code))
                    elif row['status']=='偏离':warnings.append(warning('technical_deviation','技术参数存在偏离',source=row['source'],solidified_id=code))
            if library is None:
                from api.services.tenders.schema import PlaceholderItem
                library_for_package=dict(loaded=False,slots=business_requests(ssot),media={},documents=[],warnings=[warning('library_not_loaded','本次未提供资料库实际选用结果，不能视作资料库中不存在；请加载资料库后核对',critical=True)])
            else:library_for_package=library
            b=write_business(package_dir/'business'/'business.docx',library_for_package,rules,reviewed_texts=reviewed_business_texts)
            b['package_key']=key;b['forms']=[];b['reviewed_texts']=deepcopy(list(reviewed_business_texts));b['library_selection_history']=deepcopy(library_for_package.get('selection_history',[]))
            for outcome in b['outcomes']:
                slot=next((s for s in library_for_package.get('slots',[]) if s.key==outcome['key']),None)
                outcome['request_title']=slot.title if slot else rules['missing_business_title']
                outcome['request_hint']=slot.hint if slot else ''
                outcome['ssot_sources']=[dict(document_id=d.document_id,locator=block.locator,quote=block.text) for d in ssot.documents for block in d.blocks if slot and slot.hint and norm(slot.hint) in norm(block.text)]
            for index,form in enumerate(business_forms):
                if form.get('package_key')!=key:continue
                if not form.get('actor') or not form.get('basis'):raise ValueError('商务原表填充缺少核对人或依据')
                doc=next((d for d in ssot.documents if d.document_id==form['document_id']),None)
                if doc is None:raise ValueError('商务范本不属于SSOT来源')
                output=package_dir/'business'/('form-'+str(index)+'.docx')
                proof=patch_docx(source_path(doc,root),output,form.get('changes',[]),expected_sha256=doc.sha256,table_fragment=form.get('table_fragment'))
                b['forms'].append(dict(file=str(output),actor=form['actor'],basis=form['basis'],source=doc.document_id,proof=proof))
                if proof['rejected']:warnings.append(warning('business_form_pending','原商务表部分位置无法安全填入，已保留原文',source=doc.document_id,critical=True))
            if not b['forms'] and not reviewed_business_texts:
                warnings.append(warning('business_forms_pending','未提供已核对的商务原表填充或承诺正文；当前仅为资料附件，不视作完整商务全卷',source=key,critical=True))
            business.append(b);warnings.extend(b['warnings'])
            chosen_templates=templates
            if package.get('price_templates') is not None:
                requested_templates=set(package['price_templates'])
                chosen_templates=[(d,h) for d,h in templates if d.document_id+':'+str(h.table_index) in requested_templates]
                if len(chosen_templates)!=len(requested_templates):raise ValueError('指定报价范本不在SSOT原始表中')
            elif len(templates)>1:
                warnings.append(warning('price_template_selection_pending','存在多份报价范本，均按原表导出，需确认本次适用范本；未擅自择一',source=key,critical=True))
            for doc,header in chosen_templates:
                changes=[]
                for fill in (price_fills or []):
                    if fill.get('package_key')!=key or fill.get('document_id')!=doc.document_id or fill.get('table')!=header.table_index:continue
                    if not fill.get('actor') or not fill.get('basis'):raise ValueError('价格填充缺少人工核对依据')
                    if fill.get('source_type')=='manual_quote':
                        ci=fill['column']-1
                        headers=[b for b in doc.blocks if b.table_index==header.table_index and b.row and b.row<=3]
                        if fill['row']<=header.row or not any(ci<len(b.cells) and len(norm(b.cells[ci]))<35 and re.search(rules['price_columns'],norm(b.cells[ci])) for b in headers):
                            raise ValueError('实际报价目标不是原表中已识别的价格/税率栏')
                        if not package.get('lot_key') or not package.get('package_code'):
                            warnings.append(warning('price_scope_pending','实际报价未绑定已确认标包，保留待确认',source=key,critical=True));continue
                        amount=Decimal(str(fill['text']))
                        if not amount.is_finite() or amount<0 or not all(fill.get(k) for k in ('currency','unit','confirmed_at')):
                            raise ValueError('实际报价缺少有效数值、币种、单位或确认时间')
                    elif fill.get('ssot_pricing_row') is not None:
                        source=ssot.pricing_rows[fill['ssot_pricing_row']]
                        if source.get('needs_confirmation'):raise ValueError('价格来源尚未确认适用范围，禁止自动回填')
                        if str(fill['text']) not in [str(v) for v in source['values'].values()]:raise ValueError('价格填充值不属于指定SSOT来源')
                    else:raise ValueError('价格回填须引用SSOT报价行')
                    changes.append({k:fill[k] for k in ('table','row','column','text')})
                output=package_dir/'price'/('form-'+doc.document_id+'-'+str(header.table_index)+'.docx')
                proof=patch_docx(source_path(doc,root),output,changes,expected_sha256=doc.sha256,table_fragment=header.table_index)
                if proof['rejected']:warnings.append(warning('price_cells_pending','部分价格位置原文非空或不适合安全填入，未覆盖原文，请核对',source=key,critical=True))
                if not changes:warnings.append(warning('price_values_pending','本报价范本没有可回填的已确认实际报价，保留原表待确认',source=key,critical=True))
                price.append(dict(package_key=key,file=str(output),source_document=doc.document_id,table=header.table_index,proof=proof,reviewed_fills=[deepcopy(f) for f in (price_fills or []) if f.get('package_key')==key and f.get('document_id')==doc.document_id and f.get('table')==header.table_index]))
            if not chosen_templates:
                warnings.append(warning('price_template_missing','未识别到可复用的原报价表，未编造报价表；请提供原格式',critical=True))
            if chosen_templates and not price_fills:warnings.append(warning('price_values_pending','原报价表已按原结构导出；没有已核对报价，不填写价格或套用其他物料限价',critical=True))
        if assigned!=set(materials):raise ValueError('存在未分配的固化ID，禁止静默遗漏')
        artifacts=[]
        for file in sorted(destination.rglob('*.docx')):
            size=file.stat().st_size
            artifacts.append(dict(path=str(file.relative_to(destination)),size=size,sha256=sha256(file.read_bytes()).hexdigest()))
            if file.suffix not in rules['allowed_formats']:warnings.append(warning('platform_format','文件格式不在配置白名单中',source=str(file),critical=True))
            if rules['platform_max_file_bytes'] is not None and size>rules['platform_max_file_bytes']:warnings.append(warning('capacity_exceeded','单文件超限，请核对平台规则后压缩扫描件或按允许范围拆分；未自动降质',source=str(file),critical=True))
        for package in packages:
            for channel,limit in rules.get('platform_channel_limits',{}).items():
                channel_bytes=sum(a['size'] for a in artifacts if len(Path(a['path']).parts)>1 and Path(a['path']).parts[:2]==(package['key'],channel))
                if limit is not None and channel_bytes>limit:warnings.append(warning('capacity_exceeded','端口容量超限，请核对平台规则后压缩或拆分',source=package['key']+'/'+channel,critical=True))
            size=sum(a['size'] for a in artifacts if Path(a['path']).parts[0]==package['key'])
            if rules['platform_max_package_bytes'] is not None and size>rules['platform_max_package_bytes']:warnings.append(warning('capacity_exceeded','标包总容量超限；建议按平台允许规则拆分或压缩，原文件未改动',source=package['key'],critical=True))
        from .goods_render import render_package
        visual=render_package(destination,render_adapter)
        if visual['visual_status']!='accepted':warnings.append(warning('visual_not_accepted',visual['message'],critical=True))
        warnings.sort(key=lambda w:0 if w.get('level')=='critical' else 1)
        report=dict(visual_validation=visual,input_provenance=deepcopy(input_provenance),schema_version='goods-package-result/1',ssot_sha256=digest(ssot.model_dump()),rules=rules,packages=deepcopy(packages),technical=technical,business=business,price=price,artifacts=artifacts,warnings=warnings,status='draft_review_required' if warnings else 'generated',ready_for_submission=False)
        (destination/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        return report
    except Exception:
        # Only a newly created directory belonging to this invocation is removed.
        shutil.rmtree(destination)
        raise



def load_library_snapshot(path):
    from datetime import date
    from types import SimpleNamespace
    from api.services.tenders.schema import PlaceholderItem
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    return dict(slots=[PlaceholderItem.model_validate(s) for s in data['slots']],
                media={k:[Path(p) for p in v] for k,v in data['media'].items()},
                documents=[(SimpleNamespace(public_id=d['public_id'],name=d['name'],expire_at=date.fromisoformat(d['expire_at']) if d['expire_at'] else None,is_perpetual=d['is_perpetual']),Path(d['path'])) for d in data['documents']],
                warnings=data.get('warnings',[]),unresolved_keys=data.get('unresolved_keys',[]),loaded=data.get('loaded',True),selection_history=data.get('selection_history',[]))


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='独立货物应答适配器，缺少核对依据时输出草稿与清单')
    parser.add_argument('--ssot',required=True);parser.add_argument('--root',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--offers');parser.add_argument('--packages');parser.add_argument('--library');parser.add_argument('--rules');parser.add_argument('--reviewed-inputs');parser.add_argument('--product-sources');parser.add_argument('--render-config')
    args=parser.parse_args()
    def read_json(path):return json.loads(Path(path).read_text(encoding='utf-8-sig')) if path else None
    try:
        options={}
        if args.reviewed_inputs:
            from .goods_inputs import compile_inputs
            options=compile_inputs(read_json(args.reviewed_inputs),args.ssot)
        if args.product_sources:
            from .goods_inputs import match_product_offers
            matched=match_product_offers(args.ssot,read_json(args.product_sources))
            merged=matched['offers']
            for code,rows in options.get('offers',{}).items():merged.setdefault(code,{}).update(rows)
            options['offers']=merged
            options.setdefault('input_provenance',{})['product_matching']=matched
        if args.render_config:
            from .goods_render import renderer_from_config
            options['render_adapter']=renderer_from_config(read_json(args.render_config))
        result=generate_goods(args.ssot,args.root,args.output,offers=options.pop('offers',read_json(args.offers)),packages=options.pop('packages',read_json(args.packages)),library=load_library_snapshot(args.library) if args.library else None,rules=read_json(args.rules),**options)
        print(json.dumps(dict(status=result['status'],technical=len(result['technical']),business=len(result['business']),price=len(result['price']),warnings=len(result['warnings']),output=args.output),ensure_ascii=True))
    except (ValueError,OSError,KeyError,zipfile.BadZipFile) as exc:
        parser.exit(2,'货物应答生成未完成：'+str(exc)+'；请核对模板、指定位置与输入依据。\n')
