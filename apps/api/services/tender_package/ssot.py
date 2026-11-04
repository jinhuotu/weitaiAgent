"""Independent source-preserving tender SSOT aggregation, not a generator."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import re
import unicodedata
import zipfile
from xml.etree import ElementTree as ET
from .closure import read_final_mapping
from .ssot_schema import SSOT, SourceDocument, Block, Evidence, Candidate, Fact, Requirement, Lot, TechnicalMaterial

W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def digest(value):
    return sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def norm(value):
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',str(value)))


def load_rules():
    return json.loads(Path(__file__).with_name('ssot_rules.json').read_text(encoding='utf-8-sig'))


def read_document(file, root, rules):
    base=Path(root).resolve();path=(base/file['storage_path']).resolve()
    if not path.is_relative_to(base) or not path.is_file():raise ValueError('来源文件越界或不存在：'+file['logical_path'])
    if sha256(path.read_bytes()).hexdigest()!=file['sha256']:raise ValueError('来源文件哈希不符：'+file['logical_path'])
    blocks=[];warnings=[]
    if path.suffix.lower()=='.docx':
        with zipfile.ZipFile(path) as z:
            info=z.getinfo('word/document.xml')
            if info.file_size>rules['limits']['xml_bytes']:raise ValueError('DOCX正文大小超限')
            tree=ET.fromstring(z.read(info))
        body=tree.find(W+'body');pi=ti=0;section=''
        for node in body if body is not None else []:
            if node.tag==W+'p':
                pi+=1;text=''.join(t.text or '' for t in node.iter(W+'t'))
                if re.match(r'^\s*(?:第[一二三四五六七八九十\d]+[章节部分]|\d+(?:\.\d+)*[.、\s])',text) and len(text)<120:section=text.strip()
                blocks.append(Block(locator=f'paragraph:{pi}',text=text,kind='paragraph',section=section))
            elif node.tag==W+'tbl':
                ti+=1
                # Direct OOXML cells preserve order and merged-cell structure,
                # avoiding duplicate merged-cell text from high-level readers.
                for ri,row in enumerate(node.findall(W+'tr'),1):
                    cells=['\n'.join(''.join(t.text or '' for t in p.iter(W+'t')) for p in cell.findall('.//'+W+'p')) for cell in row.findall(W+'tc')]
                    blocks.append(Block(locator=f'table:{ti}/row:{ri}',text=' | '.join(cells),kind='table_row',cells=cells,section=section,table_index=ti,row=ri))
            if len(blocks)>rules['limits']['blocks']:raise ValueError('文档块数量超限')
    elif path.suffix.lower() in ('.txt','.csv'):
        if path.stat().st_size>rules['limits']['xml_bytes']:raise ValueError('正文大小超限')
        data=path.read_bytes();text=None
        for encoding in ('utf-8-sig','gb18030'):
            try:text=data.decode(encoding);break
            except UnicodeError:pass
        if text is None:warnings.append('正文编码无法识别，需要提供文本解析结果')
        else:blocks=[Block(locator=f'line:{i}',text=line,kind='paragraph') for i,line in enumerate(text.splitlines(),1)]
    else:warnings.append('尚无此格式的离线正文适配器；原文件保留，需提供结构化解析结果')
    role=file['role'];kind=None
    if role=='contract':
        for block in blocks:
            title=norm(block.text)
            if block.kind=='paragraph' and len(title)<60:
                for label,pattern in [('special',r'^(?:第.+部分)?(?:专用合同条款|合同专用条款)$'),('general',r'^(?:第.+部分)?(?:通用合同条款|合同通用条款)$'),('agreement',r'^(?:第.+部分)?合同协议书$')]:
                    if re.fullmatch(pattern,title):kind=label;break
            if kind:break
        if kind is None and blocks and blocks[0].kind=='table_row' and all(word in blocks[0].text for word in ('适用','条款项号','约定内容')):
            kind='special'
    if not any(b.text.strip() for b in blocks):warnings.append('正文为空，需人工核对')
    if role=='contract':
        current=kind
        for block in blocks:
            title=norm(block.text)
            if block.kind=='paragraph' and len(title)<60:
                for label,pattern in [('special',r'^(?:第.+部分)?(?:专用合同条款|合同专用条款)$'),('general',r'^(?:第.+部分)?(?:通用合同条款|合同通用条款)$'),('agreement',r'^(?:第.+部分)?合同协议书$')]:
                    if re.fullmatch(pattern,title):current=label;break
            block.contract_kind=current
    return SourceDocument(document_id=digest([file['storage_path'],file['sha256']])[:24],logical_path=file['logical_path'],storage_path=file['storage_path'],sha256=file['sha256'],role=role,contract_kind=kind,blocks=blocks,warnings=warnings)


def evidence(doc, block):
    return Evidence(document_id=doc.document_id,locator=block.locator,quote=block.text)


def resolve(candidates):
    groups={}
    for candidate in candidates:
        key=digest(norm(candidate.value) if isinstance(candidate.value,str) else candidate.value)
        if key not in groups:groups[key]=deepcopy(candidate)
        else:
            group=groups[key]
            if candidate.authority is not None and (group.authority is None or candidate.authority<group.authority):group.value=deepcopy(candidate.value)
            group.sources.extend(s for s in candidate.sources if s not in group.sources)
            ranks=[r for r in (group.authority,candidate.authority) if r is not None]
            group.authority=min(ranks) if ranks else None
    values=list(groups.values())
    if not values:return Fact()
    ranked=[c for c in values if c.authority is not None]
    if not ranked:return Fact(candidates=values,status='unranked',reason='无已配置权威来源，需人工确认')
    best=min(c.authority for c in ranked);chosen=[c for c in ranked if c.authority==best]
    conflicting=len(values)>1
    return Fact(value=chosen[0].value if len(chosen)==1 else None,candidates=values,
                status='conflict' if conflicting else 'resolved',needs_confirmation=conflicting,
                reason='按权威来源给出优先值，全部冲突候选保留待核对' if conflicting else '来源一致，重复证据已合并')


def rank(doc, rules, category='identity'):
    policy=rules['authority'].get(category,{})
    return policy.get(doc.contract_kind if category in ('contract','delivery') and doc.role=='contract' else doc.role)


def _candidate(value, doc, block, rules, basis, category='identity'):
    authority=rules['authority'].get(category,{}).get(block.contract_kind) if category in ('contract','delivery') and doc.role=='contract' else rank(doc,rules,category)
    return Candidate(value=value,authority=authority,sources=[evidence(doc,block)],basis=basis)


def table_columns(cells,rules):
    return {key:i for key,pattern in rules['columns'].items() for i,cell in enumerate(cells) if re.fullmatch(pattern,norm(cell),re.I)}


def plausible_value(key,value,rules):
    value=value.strip(' ）)')
    if not value or re.search(r'[:：|&＿_]{1}',value) or re.match(r'见|详见|参见|第.+[章节].*(?:附件|附表)|按.*规定',value):return False
    labels=list(rules['fields'].values())+list(rules['columns'].values())
    if any(re.fullmatch(pattern,norm(value),re.I) for pattern in labels):return False
    if key=='tender_number':return bool(re.fullmatch(r'[\w][\w./-]*',value))
    if key=='price_ceiling':return bool(re.search(r'\d',value) or re.search(r'不设|无最高|不限',value))
    return not bool(re.fullmatch(r'\W+',value))


def collect(documents,rules):
    fields={key:[] for key in rules['fields']}
    requirements={};lot_rows=[];pricing=[]
    def requirement(category,doc,block):
        # Exact text normalization only; semantic paraphrases are not merged.
        key=category+':'+digest(norm(block.text))[:20]
        requirements.setdefault(key,(category,[]))[1].append(_candidate(block.text,doc,block,rules,'正文原文/章节上下文命中', 'contract' if category.startswith('contract_') else 'default'))
    for doc in documents:
        if doc.role=='tech_spec':continue
        headers={}
        for index,block in enumerate(doc.blocks):
            text=unicodedata.normalize('NFKC',block.text).strip()
            if not text:continue
            context=block.section+' '+text
            for category,pattern in rules['requirement_patterns'].items():
                if re.search(pattern,context):requirement(category,doc,block)
            if doc.role=='contract':
                requirement('contract_'+(block.contract_kind or 'unclassified'),doc,block)
                clause=re.match(r'^\s*(\d+(?:\.\d+)+)\s+(.+)',text,re.S)
                if clause and block.contract_kind in ('general','special'):
                    key='contract_effective:'+clause[1]
                    requirements.setdefault(key,('contract_effective',[]))[1].append(_candidate(text,doc,block,rules,'通用/专用条款显式同项号；不同原文保留为冲突','contract'))
            for key,pattern in rules['fields'].items():
                match=re.search(r'(?:^|[（(\s]|\d[.、])(?:'+pattern+r')\s*[:：]\s*([^\n;；]+)',text)
                if match:
                    value=match[1].strip(' ）)')
                    # References and blank template fields remain unresolved raw evidence.
                    if plausible_value(key,value,rules):
                        fields[key].append(_candidate(value,doc,block,rules,'显式字段标签', 'delivery' if key=='delivery_period' else 'identity'))
                if block.kind=='table_row' and len(table_columns(block.cells,rules))<2:
                    for i,cell in enumerate(block.cells[:-1]):
                        if re.fullmatch(pattern,norm(cell)):
                            value=block.cells[i+1].strip()
                            if plausible_value(key,value,rules):fields[key].append(_candidate(value,doc,block,rules,'表格标签相邻值', 'delivery' if key=='delivery_period' else 'identity'))
            if doc.role in ('invitation','notice') and index<20:
                for value in rules['method_patterns']:
                    if value in text:fields['procurement_method'].append(_candidate(value,doc,block,rules,'文档开头采购方式表述'))
                mode='协议库存' if '协议库存' in text else '框架协议' if '框架协议' in text else None
                if mode:
                    fields['procurement_mode'].append(_candidate(mode,doc,block,rules,'文档开头采购模式表述'))
                if index<5 and doc.role=='invitation' and '投标邀请书' in text:
                    title=re.sub(r'投标邀请书\s*$','',text).strip()
                    if title and not re.fullmatch(r'第.+章',title):fields['project_name'].append(_candidate(title,doc,block,rules,'邀请书开头项目专用标题'))
            if block.kind!='table_row':continue
            cols=table_columns(block.cells,rules)
            if len(cols)>=2:
                headers[block.table_index]=cols
                continue
            columns=headers.get(block.table_index,{})
            values={k:block.cells[i].strip() for k,i in columns.items() if i<len(block.cells) and block.cells[i].strip()}
            if not values:continue
            # Qualification and assessment tables are retained as requirements,
            # never treated as a purchase list merely because they mention lots.
            is_purchase=bool(('lot_code' in values or 'package_code' in values) and ('quantity' in values or 'unit' in values))
            if is_purchase:lot_rows.append((values,doc,block))
            if any(k in values for k in ('price_ceiling','unit_price','total_price')):
                pricing.append(dict(values=values,source=evidence(doc,block).model_dump(),scope='explicit_id' if 'solidified_id' in values else 'unassigned',needs_confirmation='solidified_id' not in values))
    resolved={k:resolve(c) for k,c in fields.items()}
    # Type is determined by procurement semantics, not material names or filenames.
    for doc in documents:
        if doc.role not in ('invitation','notice'):continue
        for block in doc.blocks:
            if not block.text.strip():continue
            if '协议库存' in block.text and block.locator.startswith('paragraph:') and doc.blocks.index(block)<20:
                fields['subject_type'].append(_candidate(rules['mode_subject']['协议库存'],doc,block,rules,'采购模式按配置映射标的类型'))
            for value,pattern in rules['subject_patterns'].items():
                if re.fullmatch(r'.{0,30}(?:'+pattern+r').{0,30}',block.text.strip()):
                    fields['subject_type'].append(_candidate(value,doc,block,rules,'明确采购标的类型表述'))
    resolved['subject_type']=resolve(fields['subject_type'])
    lots={}
    for values,doc,block in lot_rows:
        key=digest([values.get('lot_code'),values.get('package_code')])[:20]
        group=lots.setdefault(key,dict(fields={},sources=[],package=bool(values.get('package_code'))))
        group['sources'].append(evidence(doc,block))
        for k,value in values.items():group['fields'].setdefault(k,[]).append(_candidate(value,doc,block,rules,'采购清单明确列名'))
    for group in lots.values():
        for field in ('lot_code','lot_name','package_code','package_name','province','project_unit','unit','quantity'):
            group['fields'].setdefault(field,[])
    return resolved,[Requirement(key=k,category=category,fact=resolve(c)) for k,(category,c) in sorted(requirements.items())], [Lot(key=k,fields={f:resolve(v) for f,v in g['fields'].items()},row_sources=g['sources'],scope_status='package_explicit' if g['package'] else 'catalogue') for k,g in sorted(lots.items())],pricing


def refresh_review(ssot):
    paths=[];conflicts=[]
    facts=[('fields.'+k,f) for k,f in ssot.fields.items()]
    facts.extend(('requirements.'+r.key,r.fact) for r in ssot.requirements)
    facts.extend(('lots.'+lot.key+'.'+k,v) for lot in ssot.lots for k,v in lot.fields.items())
    for path,fact in facts:
        if fact.needs_confirmation:paths.append(path)
        if fact.status=='conflict':conflicts.append(path)
    for material in ssot.materials:
        if not material.parameter_points:paths.append('materials.'+material.solidified_id+'.parameter_points')
    if not ssot.lots:paths.append('lots')
    if any(lot.scope_status=='catalogue' for lot in ssot.lots) and ssot.fields.get('selected_lots',Fact()).needs_confirmation:paths.append('lots.selected_scope')
    if any(row.get('needs_confirmation') for row in ssot.pricing_rows):paths.append('pricing_rows.material_scope')
    for category in ('qualification_general','qualification_special','rejection','delivery','pricing','contract_agreement','contract_general','contract_special'):
        if not any(r.category==category for r in ssot.requirements):paths.append('requirements.'+category)
    paths.extend('documents.'+d.document_id for d in ssot.documents if d.warnings)
    ssot.pending=sorted(set(paths));ssot.conflicts=sorted(set(conflicts))
    kind=ssot.fields['subject_type']
    ssot.strategy=kind.value if not kind.needs_confirmation and kind.value in ('goods','works','services') else 'pending'
    return ssot


def build_ssot(manifest,root,module2_source,*,rules=None):
    rules=deepcopy(rules or load_rules())
    final=read_final_mapping(module2_source)
    inventory=deepcopy(manifest['files']);documents=[];warnings=[]
    for file in inventory:
        if file['file_type'].lower() in ('.zip','.rar','.7z','.sign'):continue
        document=read_document(file,root,rules)
        documents.append(document)
        warnings.extend(document.logical_path+'：'+w for w in document.warnings)
        if file['role']=='other':warnings.append(file['logical_path']+'：文件角色待确认，原文仍纳入来源库')
    fields,requirements,lots,pricing=collect(documents,rules)
    materials=[]
    by_path={d.storage_path:d for d in documents}
    for entry in final['entries']:
        docs=[];points=[];templates=[]
        for file in entry['files']:
            doc=by_path.get(file['storage_path'])
            if doc is None or doc.sha256!=file['sha256']:raise ValueError('模块二技术规范来源与本次文件不一致：'+entry['solidified_id'])
            docs.append(doc.document_id)
            templates.extend(evidence(doc,b) for b in doc.blocks if b.kind=='table_row' and b.row==1 and any('保证值' in c or '响应' in c for c in b.cells))
            points.extend(evidence(doc,b) for b in doc.blocks if b.kind=='table_row' and b.row!=1)
        materials.append(TechnicalMaterial(solidified_id=entry['solidified_id'],material_mapping=deepcopy(entry),document_ids=docs,parameter_points=points,response_template_sources=templates,response_table_ref='response:'+entry['solidified_id']))
    known={m.solidified_id for m in materials}
    for row in pricing:
        code=row['values'].get('solidified_id')
        row['material_ref']=code if code in known else None
        if code not in known:
            row['needs_confirmation']=True
    if any(row['needs_confirmation'] for row in pricing):warnings.append('部分报价行无法精确关联本包固化ID；保留原行，不套用到其他物料')
    if lots and any(lot.scope_status=='catalogue' for lot in lots):warnings.append('清单包含分标目录，尚未确认本包对应范围；未将目录视作已选标包')
    result=SSOT(rules_version=rules['version'],rules_snapshot=deepcopy(rules),rules_sha256=digest(rules),fields=fields,documents=documents,inventory=inventory,lots=lots,requirements=requirements,pricing_rows=pricing,materials=materials,module2_mapping=final,module2_payload_sha256=digest(final),conflicts=[],pending=[],warnings=warnings)
    return refresh_review(result)


def confirm_field(source,path,value,*,actor,basis,evidence_refs):
    """Human field completion entry; explicit source locators remain mandatory."""
    ssot=read_ssot(source)
    if not actor.strip() or not basis.strip():raise ValueError('人工补录须提供确认人和依据')
    if value is None or value=='' or value==[]:raise ValueError('人工补录值不能为空')
    if path=='fields.selected_lots' and (not isinstance(value,list) or not set(value)<={lot.key for lot in ssot.lots}):raise ValueError('本次范围须提供清单内分标 key 的列表')
    candidates=[Evidence.model_validate(s) for s in evidence_refs]
    if not candidates:raise ValueError('人工补录须指向可追溯来源')
    if path.startswith('fields.') and path[7:] in ssot.fields:
        field=ssot.fields[path[7:]]
    elif path.startswith('lots.'):
        parts=path.split('.')
        lot=next((lot for lot in ssot.lots if len(parts)==3 and lot.key==parts[1]),None)
        if lot is None:raise ValueError('分标路径不存在')
        field=lot.fields.setdefault(parts[2],Fact())
    elif path.startswith('requirements.'):
        requirement=next((r for r in ssot.requirements if r.key==path[13:]),None)
        if requirement is None:raise ValueError('要求路径不存在；新增要求请使用 add_requirement')
        field=requirement.fact
    else:raise ValueError('补录字段路径不存在')
    timestamp=datetime.now(timezone.utc).isoformat()
    candidate=Candidate(value=value,authority=-1,sources=candidates,basis=basis,actor=actor,confirmed_at=timestamp)
    field.candidates.append(candidate);field.value=deepcopy(value);field.status='manual';field.needs_confirmation=False;field.reason='已人工确认，原候选与冲突历史保留'
    ssot.manual_history.append(dict(path=path,value=value,actor=actor,basis=basis,confirmed_at=timestamp))
    return SSOT.model_validate(refresh_review(ssot).model_dump())


def read_ssot(source):
    data=json.loads(Path(source).read_text(encoding='utf-8')) if not isinstance(source,(dict,SSOT)) else source.model_dump() if isinstance(source,SSOT) else deepcopy(source)
    result=SSOT.model_validate(data)
    if result.rules_sha256!=digest(result.rules_snapshot):raise ValueError('汇聚规则摘要不符')
    if result.module2_payload_sha256!=digest(result.module2_mapping):raise ValueError('模块二映射摘要不符')
    read_final_mapping(dict(payload=result.module2_mapping,payload_sha256=result.module2_payload_sha256))
    expected=refresh_review(result.model_copy(deep=True))
    if (result.pending,result.conflicts,result.strategy)!=(expected.pending,expected.conflicts,expected.strategy):raise ValueError('SSOT待确认、冲突或策略状态与事实不一致')
    return result


def write_ssot(ssot,destination):
    result=read_ssot(ssot)
    with Path(destination).open('x',encoding='utf-8') as stream:json.dump(result.model_dump(),stream,ensure_ascii=False,indent=2)


def add_requirement(source,category,text,*,actor,basis,evidence_refs):
    ssot=read_ssot(source)
    allowed=set(load_rules()['requirement_patterns'])|{'contract_agreement','contract_general','contract_special','contract_effective'}
    if category not in allowed or not str(text).strip():raise ValueError('要求类别或正文无效')
    key=category+':'+digest(norm(text))[:20]
    if not any(r.key==key for r in ssot.requirements):
        ssot.requirements.append(Requirement(key=key,category=category,fact=Fact()))
    return confirm_field(refresh_review(ssot),'requirements.'+key,text,actor=actor,basis=basis,evidence_refs=evidence_refs)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='独立汇聚招标SSOT，不调用生成器')
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--root',required=True)
    parser.add_argument('--module2',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    try:
        manifest=json.loads(Path(args.manifest).read_text(encoding='utf-8'))
        result=build_ssot(manifest,args.root,args.module2)
        write_ssot(result,args.output)
        print(json.dumps(dict(output=args.output,materials=len(result.materials),pending=len(result.pending),conflicts=len(result.conflicts)),ensure_ascii=True))
    except (ValueError,OSError,zipfile.BadZipFile,KeyError) as exc:
        parser.exit(2,'SSOT 汇聚未完成：'+str(exc)+'；请核对输入、来源哈希或指定新的输出文件。\n')
