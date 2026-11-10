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

def _clean_item(s):
    s=re.sub(r'\s+','',s or '')
    s=re.sub(r'^[\d.、（）()]+\s*','',s)
    return s.strip(' 、：:；;。')

def _chapters_from_tables(doc):
    headings=[p.text.strip() for p in doc.paragraphs if (p.style.name or '').lower() in {'heading 1','标题 1','heading1','标题1'} and re.match(r'^[一二三四五六七八九十]+、',p.text.strip())]
    found=[]
    for tbl in doc.tables:
        for row in tbl.rows:
            cells=[c.text.strip() for c in row.cells]
            if not cells or not re.match(r'^\d+\.\d+\.\d+[（(]\d+[）)]',cells[0].replace(' ','').replace('\n','').replace('\r','')): continue
            label=(cells[1].replace(' ','').replace('\n','').replace('\r','')) if len(cells)>1 else ''
            label=re.sub(r'评分标准','',label)
            label=re.sub(r'[（(]\s*\d+\s*分[）)]','',label)
            label=re.sub(r'评分标准','',label).strip()
            mapped=next((h for h in headings if label and label in h),None)
            if mapped is None and label in {'实施方案','其他评分因素'}:
                mapped=next((h for h in headings if ('实施' in label and '实施' in h) or ('其他' in label and '服务' in h)),None)
            if mapped:
                nums=re.findall(r'(?<!\d)(\d+)\s*分', cells[1] if len(cells)>1 else '')
                found.append((mapped,int(nums[-1]) if nums else None))
    return found


def _score_rows(doc, chapters):
    names=[c[0] if isinstance(c,tuple) else c for c in chapters]
    out={c:[] for c in names}; totals={c[0]:(c[1] if isinstance(c,tuple) else None) for c in chapters}; pending={c:[] for c in names}; carry_current=None
    for tbl in doc.tables:
        current = None
        if carry_current:
            expected = totals.get(carry_current)
            used = sum(v for _, v in out.get(carry_current, ()))
            if expected is not None and used < expected:
                current = carry_current
        for row in tbl.rows:
            rawcells=[c.text.strip() for c in row.cells]
            cells=list(dict.fromkeys(x for x in rawcells if x))
            if not cells: continue
            if re.match(r'^\d+\.\d+\.\d+[（(]\d+[）)]',cells[0].replace(' ','').replace('\n','').replace('\r','')):
                label=(cells[1].replace(' ','').replace('\n','').replace('\r','')) if len(cells)>1 else ''
                label=re.sub(r'评分标准|[（(]\d+分[）)]','',label)
                current=next((p.text.strip() for p in doc.paragraphs if (p.style.name or '').lower() in {'heading 1','标题 1','heading1','标题1'} and label and label in p.text),None)
                if current is None and label in {'实施方案','其他评分因素'}:
                    current=next((p.text.strip() for p in doc.paragraphs if (p.style.name or '').lower() in {'heading 1','标题 1','heading1','标题1'} and (('实施' in label and '实施' in p.text) or ('其他' in label and '服务' in p.text))),None)
                cells=cells[2:]
                if not current: continue
            if not current: continue
            paired = len(cells)>=2 and not re.match(r'^\d+、',cells[0]) and not re.search(r'0\s*-\s*\d+\s*分',cells[0]) and re.search(r'0\s*-\s*\d+\s*分',cells[-1])
            if paired and '评分标准' in re.sub(r'\s+', '', cells[0]):
                m=re.search(r'0\s*-\s*(\d+)\s*分',cells[-1])
                item=_clean_item(cells[-1][:m.start()]) if m else ''
                if item and int(m.group(1)) > 0 and not re.search(r'评分标准\s*[（(]\s*\d+\s*分\s*[）)]|评分标准$',item): out[current].append((item,int(m.group(1))))
                cells=cells[:-1]; paired=False
            for cell in (cells[:-1] if paired else cells):
                joined=re.sub(r'\s+','',cell)
                for m in re.finditer(r'([^0-9]+?)0\s*-\s*(\d+)\s*分',joined):
                    line=m.group(1)
                    line=line.strip()
                    if not line or line.startswith('注：') or '评分标准' in line or re.match(r'^\d+、',line) and not re.search(r'0\s*-\s*\d+\s*分',line): continue
                    item=_clean_item(line)
                    if item and int(m.group(2)) > 0 and not re.search(r'评分标准\s*[（(]\s*\d+\s*分\s*[）)]|评分标准$',item): out[current].append((item,int(m.group(2))))
            if len(cells)>=2 and '评分标准' not in cells[0] and '评分因素' not in cells[0] and not re.search(r'0\s*-\s*\d+\s*分',' '.join(cells[0:-1])):
                m=re.search(r'0\s*-\s*(\d+)\s*分',cells[-1])
                if m and cells[0] and not re.match(r'^\d+、',cells[0]):
                    item=_clean_item(cells[0])
                    if item and int(m.group(1)) > 0 and not re.search(r'评分标准\s*[（(]\s*\d+\s*分\s*[）)]|评分标准$',item): out[current].append((item,int(m.group(1))))
            candidate=next((x for x in cells if re.match(r'^[（(]\s*\d+\s*[）)]',x)),None)
            if current and candidate and not re.search(r'0\s*-\s*\d+\s*分',' '.join(cells)):
                pending[current].append(_clean_item(candidate))
        carry_current = current
    for chapter,items in pending.items():
        if not items: continue
        remaining=totals.get(chapter)
        used=sum(v for _,v in out[chapter])
        if remaining is not None and (remaining-used)%len(items)==0:
            share=(remaining-used)//len(items)
            if share > 0:
                out[chapter].extend((item,share) for item in items)
        else:
            logger.warning('评分项差额无法均摊：%s',chapter)
    for chapter in out:
        out[chapter] = [(item, score) for item, score in out[chapter] if score > 0]
    return out,totals


def key(text):
    return re.sub(r'[^\w\u4e00-\u9fff]', '', str(text)).casefold()


def text(el):
    return ''.join(t.text or '' for t in el.iter(qn('w:t')))


def structure(brief, doc=None):
    """The numbered sibling group following an explicit drafting instruction owns the points."""
    loaded_format = doc is None
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
    table_chapters=_chapters_from_tables(doc)
    if loaded_format and not table_chapters:
        try:
            from api.services.tenders.assets import tender_invitations_dir

            invitation_id = (getattr(brief, 'invitationId', '') or '').strip()
            original_path = tender_invitations_dir() / f'{invitation_id}.docx'
            if not original_path.is_file():
                logger.warning('技术标评分表回退失败：原始邀请书不存在：%s', original_path)
            else:
                original_doc = Document(original_path)
                original_table_chapters = _chapters_from_tables(original_doc)
                doc = original_doc
                blocks = list(doc.element.body)
                positions = {id(node): i for i, node in enumerate(blocks)}
                headings = []
                for node in blocks:
                    if node.tag != qn('w:p'):
                        continue
                    value = text(node).strip()
                    level = outline_level(doc, node)
                    from docx.text.paragraph import Paragraph
                    style_name = (Paragraph(node, doc).style.name or '').lower()
                    if (level == 0 or style_name in {'heading 1', '标题 1', 'heading1', '标题1'}) and re.match(r'^[一二三四五六七八九十]+、', value):
                        headings.append((node, value))
                table_chapters = original_table_chapters
                logger.info('评分表来源=原始邀请书：%s，识别章节=%d', original_path, len(table_chapters))
        except Exception:
            logger.warning('技术标评分表回退解析失败：原始邀请书', exc_info=True)
    if table_chapters:
        criteria = {}
        try:
            from api.services.tenders.scoring_points import read_scoring_points, word_scoring_blocks

            from api.services.tenders.scoring_points import _item_name, _score

            for point in read_scoring_points('', blocks=word_scoring_blocks(doc)):
                if point.criterion:
                    criteria[key(point.item)] = point.criterion
                    detail = _item_name(_score(re.sub(r'\s+', '', point.criterion))[0])
                    criteria.setdefault(key(detail), point.criterion)
        except Exception:
            logger.warning('评分细则正文提取失败，保留章节评分项：%s', getattr(brief, 'invitationId', ''), exc_info=True)
        chapter_names=[x[0] for x in table_chapters]
        qualified=[]
        for title,total in table_chapters:
            node=next((n for n,v in headings if v==title),None)
            if node is None:
                logger.warning('技术标章节标题未找到落位：%s',title); continue
            qualified.append((node,title,total))
        if qualified:
            logger.info('技术标模板类型：chapter_heading')
            chapters=[(v,t) for _,v,t in qualified]
            logger.info('识别到章节 %d 个：%s',len(chapters),' / '.join(v for v,_ in chapters))
            extracted,totals=_score_rows(doc,chapters)
            sections=[]
            usable=[]
            for _,title,total in qualified:
                rows=extracted.get(title) or []
                if total is not None and sum(v for _,v in rows)!=total:
                    logger.warning('技术标章节分值对账失败：%s expected=%s actual=%s',title,total,sum(v for _,v in rows)); continue
                usable.append(title)
                if not rows:
                    rows=[(title+'总体目标',2),(title+'组织实施',2),(title+'质量与验证',2)]
                    logger.warning('技术标章节评分项 fallback：%s',title)
                logger.info('每章评分项 %s：%d 条',title,len(rows))
                for item,score in rows:
                    identity='tp_'+hashlib.sha256((title+'\0'+item).encode()).hexdigest()[:16]
                    sections.append({'id':identity,'title':item,'chapter':title,'score':score,
                                     'criterion': criteria.get(key(item), item)})
            return {'template_kind':'chapter_heading','chapters':[{'title':v,'level':1} for v in usable], 'sections':sections,'tables':[],'figures':[]}
    if len(headings) >= 3:
        qualified=[]
        for node,value in headings:
            idx=positions[id(node)]
            first=text(blocks[idx+1]).strip() if idx+1 < len(blocks) and blocks[idx+1].tag==qn('w:p') else ''
            if re.search(r'根据.*评分标准.*编制',first) or (re.match(r'^[一二三四五六七八九十]+、',value) and '服务' in value and qualified): qualified.append((node,value))
            elif qualified: break
        if len(qualified) >= 3:
            logger.warning('技术标章节清单 fallback：未找到评分表')
            logger.info('技术标模板类型：chapter_heading')
            chapters=[v for _,v in qualified]; extracted={c:[] for c in chapters}
            sections=[]
            for _,title in qualified:
                rows=[(title+'总体目标',2),(title+'组织实施',2),(title+'质量与验证',2)]
                logger.warning('技术标章节评分项 fallback：%s',title)
                for item,score in rows:
                    identity='tp_'+hashlib.sha256((title+'\0'+item).encode()).hexdigest()[:16]
                    sections.append({'id':identity,'title':item,'chapter':title,'score':score})
            return {'template_kind':'chapter_heading','chapters':[{'title':v,'level':1} for _,v in qualified], 'sections':sections,'tables':[],'figures':[]}
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


def _scoring_schedule_table(brief, chapter_facts):
    """为评分型技术卷提供一张由招标工期驱动的非空施工进度表。"""
    duration = int(getattr(brief, 'deliveryDays', 0) or 0)
    if not duration:
        match = re.search(r'(\d+)\s*日历?天', str((chapter_facts or {}).get('计划工期', '')))
        duration = int(match.group(1)) if match else 150
    checkpoints = [
        ('施工准备与图纸会审', 1, max(1, round(duration * 0.10)), '完成交底、会审纪要和开工条件确认'),
        ('设备材料采购与进场检验', max(2, round(duration * 0.08)), max(2, round(duration * 0.30)), '完成采购计划、到货核验和合格记录'),
        ('土建及管沟施工', max(3, round(duration * 0.22)), max(3, round(duration * 0.60)), '完成基础、管沟和隐蔽验收'),
        ('设备安装与接线', max(4, round(duration * 0.52)), max(4, round(duration * 0.80)), '完成设备就位、接线和单机检查'),
        ('系统调试与送电验收', max(5, round(duration * 0.78)), max(5, round(duration * 0.93)), '完成联调、试运行和送电验收'),
        ('竣工资料整理与移交', max(6, round(duration * 0.91)), duration, '完成竣工图、资料签收和遗留项关闭'),
    ]
    rows = []
    for name, start, end, output in checkpoints:
        start = min(duration, max(1, int(start)))
        end = min(duration, max(start, int(end)))
        rows.append([name, f'D{start}-D{end}', output])
    return {
        'id': 'technical_scoring_schedule',
        'title': f'施工进度计划表（{duration}日历天）',
        'columns': ['阶段', '计划区间', '完成判据'],
        'rows': rows,
        'note': '计划区间以开工令为D1，节点完成以对应记录和签认为准。',
    }


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
    source_facts = tender_facts.get('facts', {})
    chapter_facts = {}
    for label in ('项目名称', '建设地点', '计划工期'):
        value = source_facts.get(label, '').strip()
        if value and not value.startswith(('见', '详见', '按')) and len(value) <= 100:
            chapter_facts[label] = value
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
    scored = spec.get('template_kind') == 'chapter_heading'
    needs_resources = bool(spec['tables'] or spec['figures'])
    bodies={s['id']:s.get('body','') for s in cached.get('sections',[]) if isinstance(s,dict) and not s.get('degraded')}
    degraded = set()
    manual=[]
    for point in spec['sections']:
        item=next((i for i in brief.outlineItems if key(i.title)==key(point['title']) or key(i.rawTitle)==key(point['title'])),None)
        if item and item.draftStatus in {'manual','edited'} and not _valid_body(item.draftBody):
            raise AppError(ErrorCode.VALIDATION,'人工编辑的技术要点正文尚未完成，未自动覆盖：'+point['title'],status_code=422)
        if item and _valid_body(item.draftBody):
            bodies[point['id']]=item.draftBody
            if item.draftStatus in {'manual','edited'}:manual.append(point['id'])
    missing=[s for s in spec['sections'] if not _valid_body(bodies.get(s['id']))]
    if client is None and (missing or (needs_resources and not cached.get('stages'))):
        from api.services.models.runtime import build_llm_client
        try:
            client=await build_llm_client(db,'pro')
        except Exception as exc:
            if not scored or needs_resources:
                raise
            logger.warning('评分正文模型不可用 error=%s', type(exc).__name__)
    async def generate(group):
        raw=await complete_json(client,[{'role':'system','content':get_prompt('technical_package_system')+'\n每个评分项正文必须覆盖做什么、怎么做、达到什么标准、谁负责、如何验证五要素；只写表态型内容，不编造人名、证书、业绩、财务或人数。'},
            {'role':'user','content':context+'\n'+json.dumps({'anonymous':brief.techAnonymous,'sections':group},ensure_ascii=False)}],mode='pro')
        return _payload(raw).get('sections',[])
    if scored:
        from api.services.tenders.scoring_points import generate_scoring_narrative
        from api.services.tenders.style_roles import clean_generated_text

        async def generate_point(point):
            return await generate_scoring_narrative(
                client, item=point['title'], criterion=point.get('criterion', point['title']),
                chapter=point['chapter'], facts=context, score=point.get('score'),
            )

        results = await ordered_generation(
            [lambda point=point: generate_point(point) for point in missing],
            stage='technical_scoring_narrative', labels=[point['id'] for point in missing],
        ) if client is not None else [''] * len(missing)
        for point, body in zip(missing, results, strict=True):
            body = clean_generated_text(body)
            if body:
                bodies[point['id']] = body
        logger.info('技术评分正文：模型调用=%d 非空=%d',
                    len(results) if client is not None else 0,
                    sum(bool(bodies.get(point['id'])) for point in missing))
    groups=[] if scored else [missing[i:i+2] for i in range(0,len(missing),2)]
    batches=await ordered_generation([lambda g=g:generate(g) for g in groups],stage='technical_source_points',labels=[str(i) for i in range(len(groups))])
    for batch in batches:
        for row in batch if isinstance(batch,list) else []:
            if isinstance(row,dict) and _valid_body(row.get('body')):
                bodies[str(row.get('id'))]=row['body'].strip()
    for point in spec['sections']:
        if not scored and not _valid_body(bodies.get(point['id'])):
            repaired=await generate([point])
            hit=next((r for r in repaired if isinstance(r,dict) and r.get('id')==point['id'] and _valid_body(r.get('body'))),None)
            if hit: bodies[point['id']]=hit['body']
        if not (bodies.get(point['id']) if scored else _valid_body(bodies.get(point['id']))):
            if not scored:
                raise AppError(ErrorCode.VALIDATION,'技术要点未生成实质正文：'+point['title']+'；请重试起草',status_code=422)
            from api.services.tenders.scoring_points import criterion_body

            bodies[point['id']] = criterion_body(point['title'], point.get('criterion', point['title']))
            degraded.add(point['id'])
            logger.warning('技术要点模型正文为空，使用评分细则派生正文：%s', point['title'])
    resources=cached if cached.get('tables') and cached.get('stages') and cached.get('zones') else (None if needs_resources else {})
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
            package={**spec,'sourceHash':fingerprint,'chapterFacts':chapter_facts,
                     'sections':[{**s,'body':bodies[s['id']],'manual':s['id'] in manual,
                       'degraded':s['id'] in degraded} for s in spec['sections']],
                     'tables':[{**t,**{field:row.get(field,[] if field=='rows' else '') for field in ['rows','note']}}
                               for t in spec['tables'] for row in [next((r for r in resources.get('tables',[]) if isinstance(r,dict) and r.get('id')==t['id']),{})]],
                     'stages':resources.get('stages',[]),'zones':resources.get('zones',[]),'note':str(resources.get('note',''))}
            if scored and not package.get('tables'):
                package['tables'] = [_scoring_schedule_table(brief, chapter_facts)]
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
    if package.get('template_kind') == 'chapter_heading' and not package.get('figures'):
        return
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
    from api.services.tenders.schema import OutlineItem, PlaceholderItem
    from api.services.tenders.format_rules import peel_toc_lead
    from api.services.tenders.material_identity import standard_material_key
    draft=brief.model_copy(deep=True)
    package = draft.technicalPackage or structure(brief)
    points={key(p['title']) for p in package.get('sections',[])}
    if package.get('template_kind') == 'chapter_heading':
        source_items = {key(peel_toc_lead(item.rawTitle or item.title)): item
                        for item in draft.outlineItems}
        for index, chapter in enumerate(package['chapters']):
            title = chapter['title']
            item = source_items.get(key(peel_toc_lead(title)))
            if item is None:
                draft.outlineItems.append(OutlineItem(
                    id=f'tech-score-chapter-{index}', title=title, rawTitle=title,
                    kind='tech_chapter', source='copy', tocEligible=True,
                ))
            else:
                item.kind = 'tech_chapter'
                item.skipped = False
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
