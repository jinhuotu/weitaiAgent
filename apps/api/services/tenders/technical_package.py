"""Technical-only, source-anchored drafting contract; never alters business outlines."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

from api.services.tenders.prompts import get_prompt
from common.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

CHAPTER_SCORING = {
    '六、承包人建议书': [('项目详细说明和总体管理方案、目标',2),('项目管理机构设置与人员配备计划（含设计）',2),('项目质量、进度、投资、合同、信息管理与控制',2),('工程竣工验收、结算、移交管理',2)],
    '七、设计组织技术措施': [('设计质量保证体系及措施',2),('风险防范措施合理可行',2),('重要设计节点安排等方面有针对性表述',2),('设计进度计划表及确保进度的组织技术措施',2),('限额设计的保证措施',2),('对本项目的设计意图和重点难点的理解',2),('新技术、新材料在本项目设计中的应用',2)],
    '八、承包人实施计划': [('施工方案与技术措施',6),('设备安装方案与技术措施',6),('质量管理体系与措施',5),('安全管理体系与措施',5),('环境保护管理体系及施工现场扬尘治理措施',4),('工程进度计划与措施',3),('风险管理措施',3)],
    '九、服务承诺': [('施工期间不拖欠农民工工资、农忙季节及业主资金不到位时连续施工',1),('质保期内、质保期外服务承诺',1),('替甲方排忧解难、协调村民及当地政府等周边关系',1),('与招标人的配合和保证措施',1),('其他实质性承诺及合理化建议',2)],
}

def _score_rows(doc, chapters):
    """Extract chapter scoring rows from source tables; constants are regression hints only."""
    from docx.table import Table
    out={c:[] for c in chapters}
    for tbl in doc.tables:
        rows=[[c.text.strip() for c in r.cells] for r in tbl.rows]
        header=' '.join(rows[0]) if rows else ''
        if not re.search(r'评分因素|评分标准|分值',header): continue
        current=None
        for row in rows[1:]:
            blob=' '.join(x for x in row if x)
            for c in chapters:
                if c in blob: current=c; break
            if not current or not blob: continue
            nums=re.findall(r'(?<!\d)(\d+)(?:\.\d+)?分?',blob)
            item=re.sub(r'^\s*[（(]?\d+[）).、]?\s*','',row[0] or blob).strip()
            if item and nums:
                out[current].append((item,int(nums[-1])))
    return out


def key(text):
    return re.sub(r'[^\w\u4e00-\u9fff]', '', str(text)).casefold()


def text(el):
    return ''.join(t.text or '' for t in el.iter(qn('w:t')))


def structure(brief, doc=None):
    """The numbered sibling group following an explicit drafting instruction owns the points."""
    if doc is None:
        from api.services.tenders.pdf_format import _word_format_file
        path = _word_format_file(brief.invitationId)
        if path is None:
            return {}
        doc = Document(path)
    blocks = list(doc.element.body)
    from api.services.tenders.word_structure import outline_level
    headings=[]; positions={id(node):i for i,node in enumerate(blocks)}
    for node in blocks:
        if node.tag != qn('w:p'):
            continue
        value=text(node).strip()
        level=outline_level(doc,node)
        from docx.text.paragraph import Paragraph
        style_name=(Paragraph(node,doc).style.name or '').lower()
        if (level == 0 or style_name in {'heading 1','标题 1','heading1','标题1'}) and re.match(r'^[一二三四五六七八九十]+、', value):
            headings.append((node,value))
    if len(headings) >= 3:
        qualified=[]
        for node,value in headings:
            idx=positions[id(node)]
            first=text(blocks[idx+1]).strip() if idx+1 < len(blocks) and blocks[idx+1].tag==qn('w:p') else ''
            if re.search(r'根据.*评分标准.*编制',first): qualified.append((node,value))
            elif qualified: break
        if len(qualified) >= 3:
            logger.info('技术标模板类型：chapter_heading')
            chapters=[v for _,v in qualified]
            logger.info('识别到章节 %d 个：%s',len(chapters),' / '.join(chapters))
            extracted=_score_rows(doc,chapters)
            sections=[]
            for _,title in qualified:
                rows=extracted.get(title) or []
                if rows is None:
                    rows=[]
                if not rows:
                    rows=[(title+'总体目标',2),(title+'组织实施',2),(title+'质量与验证',2)]
                    logger.warning('技术标章节评分项 fallback：%s',title)
                logger.info('每章评分项 %s：%d 条',title,len(rows))
                for item,score in rows:
                    identity='tp_'+hashlib.sha256((title+'\0'+item).encode()).hexdigest()[:16]
                    sections.append({'id':identity,'title':item,'chapter':title,'score':score})
            return {'template_kind':'chapter_heading','chapters':[{'title':v,'level':1} for _,v in headings], 'sections':sections,'tables':[],'figures':[]}
        logger.info('技术标模板类型：numbered_points')
    def number_id(node):
        from docx.text.paragraph import Paragraph
        props=node.find('w:pPr/w:numPr',node.nsmap)
        style=Paragraph(node,doc).style
        visited=set()
        while props is None and style is not None and style.style_id not in visited:
            visited.add(style.style_id);props=style.element.find('w:pPr/w:numPr',style.element.nsmap);style=style.base_style
        num=props.find(qn('w:numId')) if props is not None else None
        return num.get(qn('w:val')) if num is not None else None
    for index, block in enumerate(blocks):
        instruction = text(block)
        if not (re.search(r'(?:参考|按照|根据|按).{0,20}(?:要点|内容).{0,25}编制', instruction)
                and re.search(r'方案|设计|技术|实施', instruction)):
            continue
        points = []
        group = None
        for node in blocks[index+1:]:
            if node.tag != qn('w:p'):
                break
            title = text(node).strip()
            if not title:
                if points: break
                continue
            number = number_id(node)
            if len(title)>120 or (group is not None and number != group):
                break
            if number is None and not re.match(r'^(?:\d+|[（(一二三四五六七八九十])', title):
                break
            if group is None: group = number
            identity = 'tp_' + hashlib.sha256(title.encode()).hexdigest()[:16]
            points.append({'id':identity,'title':title})
        if len(points)<2:
            continue
        tables, figures = [], []
        heading = ''
        from api.services.tenders.word_structure import outline_level
        root_level=next((outline_level(doc,n) for n in reversed(blocks[:index]) if n.tag==qn('w:p') and outline_level(doc,n)),None)
        for node in blocks[index+1:]:
            if node.tag == qn('w:p'):
                value = text(node).strip()
                if root_level and outline_level(doc,node) and outline_level(doc,node)<=root_level and not re.match(r'^附[表件]',value):
                    break
                # A real appendix heading has a delimiter; multi-heading tabbed
                # inventories are not body insertion targets.
                if re.match(r'^附[表件]\S{1,8}[:：]', value) and not node.findall('.//w:tab',node.nsmap):
                    heading = value
                    if re.search(r'网络|进度', value):
                        figures.append({'title':heading,'kind':'schedule'})
                    elif re.search(r'平面|布置', value):
                        figures.append({'title':heading,'kind':'layout'})
            elif node.tag == qn('w:tbl') and heading:
                from docx.table import Table
                table = Table(node,doc)
                empty = next((i for i,r in enumerate(table.rows) if not any(c.text.strip() for c in r.cells)),None)
                if empty is not None and empty>0:
                    headers=[' / '.join(dict.fromkeys(table.rows[j].cells[c].text for j in range(empty)))
                             for c in range(len(table.rows[empty].cells))]
                    tables.append({'id':'tt_'+hashlib.sha256(heading.encode()).hexdigest()[:16],
                                   'title':heading,'columns':headers,'firstDataRow':empty,
                                   'capacity':len(table.rows)-empty})
                heading = ''
        return {'sections':points,'tables':tables,'figures':figures}
    return {}


def _payload(raw):
    try:
        value,_=json.JSONDecoder().raw_decode(str(raw).lstrip('\ufeff \t\r\n'))
    except ValueError:
        from api.services.layouts.parse import extract_json_object
        try:value=extract_json_object(raw,prefer_keys=('sections','tables'))
        except AppError:value={}
    return value if isinstance(value,dict) else {}


def _valid_body(body):
    return isinstance(body,str) and len(body.strip())>=100 and not re.search(r'未完整生成|^【待补充】',body.strip())


async def draft_package(db, brief, *, client=None):
    """Reuse matching substantial drafts; generate only missing source points and plans."""
    spec = structure(brief)
    if not spec:
        return {}, []
    from api.services.tenders.generation_parallel import complete_json, ordered_generation
    from api.services.tenders.assets import load_invitation_text
    from api.services.tenders.gen_tech_plan import _user_sources
    notes=[]
    sources=_user_sources(brief.techPlanSourceIds,brief=brief,notes=notes)
    from api.services.tenders.tender_facts import extract_tender_facts
    invitation_text=load_invitation_text(brief.invitationId)
    tender_facts=extract_tender_facts(invitation_text)
    facts={'projectName':brief.projectName,'scope':brief.bidContent,'durationDays':brief.deliveryDays,
           'quality':brief.quality,'technicalInput':brief.techPlanNote,
           'constructionSite':tender_facts.get('constructionSite') or tender_facts.get('site') or '',
           'contractScope':brief.bidContent or tender_facts.get('scope') or '',
           'engineeringObject':tender_facts.get('engineeringObject') or tender_facts.get('projectType') or '',
           'invitation':invitation_text[:24000],'userSources':sources}
    from api.services.tenders.categories import LEGACY_TECH_FIELDS
    entered={name:getattr(brief,name) for name in LEGACY_TECH_FIELDS if getattr(brief,name,'').strip()}
    if entered:facts['userTechnicalFields']=entered
    if db is not None and brief.techPlanKbIds:
        from api.services.tenders.extract import _recall_knowledge
        from api.services.tenders.gen_tech_plan import _style_guide
        knowledge,_=await _recall_knowledge(db,facts['invitation'],brief.techPlanKbIds,
            query=brief.projectName+' 技术方案',quiet=True,bidder_name=brief.bidderName)
        facts['styleReference']=_style_guide([str(r.get('content','')) for r in knowledge if isinstance(r,dict)])
    # The invitation can be long: source-local technical instructions and table
    # definitions always accompany the project facts, irrespective of truncation.
    context=json.dumps(facts,ensure_ascii=False)
    fingerprint=hashlib.sha256(json.dumps([facts,brief.invitationId,brief.bidderName,brief.techAnonymous,spec],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    cached=brief.technicalPackage or {}
    if cached.get('sourceHash')!=fingerprint:cached={}
    bodies={s['id']:s.get('body','') for s in cached.get('sections',[]) if isinstance(s,dict)}
    manual=[]
    for point in spec['sections']:
        item=next((i for i in brief.outlineItems if key(i.title)==key(point['title']) or key(i.rawTitle)==key(point['title'])),None)
        if item and item.draftStatus in {'manual','edited'} and not _valid_body(item.draftBody):
            raise AppError(ErrorCode.VALIDATION,'人工编辑的技术要点正文尚未完成，未自动覆盖：'+point['title'],status_code=422)
        if item and _valid_body(item.draftBody):
            bodies[point['id']]=item.draftBody
            if item.draftStatus in {'manual','edited'}:manual.append(point['id'])
    missing=[s for s in spec['sections'] if not _valid_body(bodies.get(s['id']))]
    if client is None and (missing or not cached.get('tables') or not cached.get('stages')):
        from api.services.models.runtime import build_llm_client
        client=await build_llm_client(db,'pro')
    async def generate(group):
        raw=await complete_json(client,[{'role':'system','content':get_prompt('technical_package_system')+'\n每个评分项正文必须覆盖做什么、怎么做、达到什么标准、谁负责、如何验证五要素；只写表态型内容，不编造人名、证书、业绩、财务或人数。'},
            {'role':'user','content':context+'\n'+json.dumps({'anonymous':brief.techAnonymous,'sections':group},ensure_ascii=False)}],mode='pro')
        return _payload(raw).get('sections',[])
    groups=[missing[i:i+2] for i in range(0,len(missing),2)]
    batches=await ordered_generation([lambda g=g:generate(g) for g in groups],stage='technical_source_points',labels=[str(i) for i in range(len(groups))])
    for batch in batches:
        for row in batch if isinstance(batch,list) else []:
            if isinstance(row,dict) and _valid_body(row.get('body')):
                bodies[str(row.get('id'))]=row['body'].strip()
    for point in spec['sections']:
        if not _valid_body(bodies.get(point['id'])):
            repaired=await generate([point])
            hit=next((r for r in repaired if isinstance(r,dict) and r.get('id')==point['id'] and _valid_body(r.get('body'))),None)
            if hit: bodies[point['id']]=hit['body']
        if not _valid_body(bodies.get(point['id'])):
            raise AppError(ErrorCode.VALIDATION,'技术要点未生成实质正文：'+point['title']+'；请重试起草',status_code=422)
    resources=cached if cached.get('tables') and cached.get('stages') and cached.get('zones') else None
    feedback=''
    previous={}
    for attempt in range(3):
        if resources is None:
            if client is None:
                from api.services.models.runtime import build_llm_client
                client=await build_llm_client(db,'pro')
            correction=('\n修正上次结构化结果，保留原表 id 和列结构；不要修改已完成的正文。校验错误：'+feedback+
                        '。dependsOn 表示完成后才能开始；依赖阶段的 startDay 必须大于前置 endDay；可并行阶段不要错误标为完成后依赖。不得延长输入工期。\n上次结果：'+json.dumps(previous,ensure_ascii=False)) if feedback else ''
            raw=await complete_json(client,[{'role':'system','content':get_prompt('technical_resources_system')},
                {'role':'user','content':context+correction+'\n'+json.dumps({'tables':spec['tables'],'figures':spec['figures']},ensure_ascii=False)}],mode='pro')
            resources=_payload(raw)
        try:
            package={**spec,'sourceHash':fingerprint,'sections':[{**s,'body':bodies[s['id']],'manual':s['id'] in manual} for s in spec['sections']],
                     'tables':[{**t,**{field:row.get(field,[] if field=='rows' else '') for field in ['rows','note']}}
                               for t in spec['tables'] for row in [next((r for r in resources.get('tables',[]) if isinstance(r,dict) and r.get('id')==t['id']),{})]],
                     'stages':resources.get('stages',[]),'zones':resources.get('zones',[]),'note':str(resources.get('note',''))}
            validate(package,brief)
            break
        except (AppError,ValueError,TypeError,KeyError) as exc:
            if attempt==2:
                raise AppError(ErrorCode.VALIDATION,'技术资源计划经修复仍未通过校验：'+str(exc),status_code=422) from exc
            feedback=str(exc);previous=resources;resources=None
            logger.warning('technical resource validation retry=%s reason=%s',attempt+1,feedback)
    notes.append(f"技术卷：原版 {len(spec['sections'])} 个要点逐项起草；资源为拟投入计划，未核实参数在实施前复核")
    return package,notes


def validate(package,brief):
    for table in package.get('tables',[]):
        rows=table.get('rows',[])
        if not rows or any(not isinstance(r,list) or len(r)!=len(table['columns']) or not any(str(v).strip() and not re.fullmatch(r'[\d\s]+|【待补充】',str(v).strip()) for v in r) for r in rows):
            raise AppError(ErrorCode.VALIDATION,'技术附表数据未完整返回：'+table['title'],status_code=422)
    stages=package.get('stages',[]);ids={str(s.get('id')) for s in stages if isinstance(s,dict)}
    if len(ids)<2 or len(ids)!=len(stages) or len(package.get('zones',[]))<3:
        raise AppError(ErrorCode.VALIDATION,'技术进度/布置绘图数据未完整返回，请重试起草',status_code=422)
    seen=set()
    ends={}
    for s in stages:
        dependencies=set(map(str,s.get('dependsOn',[])))
        if not dependencies<=seen or not 1<=int(s.get('startDay',0))<=int(s.get('endDay',0)):
            raise AppError(ErrorCode.VALIDATION,'技术进度依赖或日期无效',status_code=422)
        if brief.deliveryDays>0 and int(s['endDay'])>brief.deliveryDays:
            raise AppError(ErrorCode.VALIDATION,'技术进度超出输入工期',status_code=422)
        if any(int(s['startDay'])<=ends[d] for d in dependencies):
            raise AppError(ErrorCode.VALIDATION,'技术进度早于前置工序完成',status_code=422)
        seen.add(str(s['id']))
        ends[str(s['id'])]=int(s['endDay'])
    for zone in package['zones']:
        if not str(zone.get('name','')).strip() or any(not 0<=float(zone.get(n,-1))<=1 for n in ['x','y','w','h']) or float(zone.get('w',0))<=0 or float(zone.get('h',0))<=0 or float(zone['x'])+float(zone['w'])>1.001 or float(zone['y'])+float(zone['h'])>1.001:
            raise AppError(ErrorCode.VALIDATION,'技术布置图区域坐标无效',status_code=422)


def scoped_inputs(brief,slots,media):
    """Reject business material and prose demands at the technical render boundary."""
    from api.services.tenders.categories import slot_volume, BUSINESS_SLOT_KEYS, TECHNICAL_SLOT_KEYS
    from api.services.tenders.schema import PlaceholderItem
    from api.services.tenders.material_identity import standard_material_key
    draft=brief.model_copy(deep=True)
    points={key(p['title']) for p in structure(brief).get('sections',[])}
    allowed=[]
    candidates=list(slots or [])
    present={s.key for s in candidates}
    candidates.extend(PlaceholderItem(key=k,title=k) for k in TECHNICAL_SLOT_KEYS if k in (media or {}) and k not in present)
    for s in candidates:
        standard=standard_material_key(s)
        if standard in BUSINESS_SLOT_KEYS or slot_volume(s.key,s.title)!='technical' or key(s.title) in points:
            continue
        allowed.append(s)
    keys={s.key for s in allowed}
    draft.materialDemands=[d for d in draft.materialDemands if d.volume=='technical' and d.slotKey in keys and key(d.title) not in points]
    draft.extraPlaceholders=[s for s in draft.extraPlaceholders if s.key in keys]
    draft.requiredSlotKeys=[k for k in draft.requiredSlotKeys if k in keys]
    draft.includeSlotKeys=[k for k in draft.includeSlotKeys if k in keys]
    if draft.techAnonymous:
        # Unverified scans can contain names/seals that textual scrubbing cannot remove.
        if any((media or {}).get(s.key) for s in allowed):
            draft.materialIntentNotes=[*draft.materialIntentNotes,'技术暗标：技术扫描件未确认已去除身份标识，未嵌入；请提供经核验的匿名技术资料']
        return draft,[],{}
    return draft,allowed,{k:v for k,v in (media or {}).items() if k in keys}
