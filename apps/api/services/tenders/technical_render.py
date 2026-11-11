"""Render source-owned technical points/tables and data-driven plan diagrams."""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import hashlib
import logging
import re

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.section import Section
from docx.shared import Cm, Pt
from docx.text.paragraph import Paragraph

from api.services.tenders.technical_package import key,text
from api.services.tenders.format_rules import peel_toc_lead
from api.services.tenders.outline import subset_toc_titles
from api.services.tenders.schema import OutlineItem

logger = logging.getLogger("api.tenders.technical_render")

_FACT_LABELS = {
    "projectName": "项目名称",
    "project": "项目名称",
    "scope": "招标范围",
    "contractScope": "合同范围",
    "durationDays": "工期",
    "deliveryDays": "工期",
    "quality": "质量标准",
    "constructionSite": "建设地点",
    "engineeringObject": "工程对象",
    "technicalInput": "技术输入",
    "bidValidityDays": "投标有效期",
    "bidBondYuan": "投标保证金",
    "建设地点": "建设地点",
    "计划工期": "工期",
    "项目名称": "项目名称",
}
_INTRO_STARTS = (
    "针对“{title}”的任务边界，先拆解{points}",
    "围绕“{title}”的实施链条，安排{points}",
    "以“{title}”的验收结果为牵引，落实{points}",
)
_INTRO_ENDINGS = (
    "围绕{point}留存过程记录并核对责任接口。",
    "按{point}的审查结果安排后续工作。",
    "在{point}的交接环节确认成果清单。",
    "对{point}留存检查和整改记录。",
)
_INTRO_DETAILS = (
    "，先把输入条件、责任接口和验收证据排成闭环",
    "，同步梳理前置条件、过程控制与成果确认",
    "，将关键动作、复核节点和留痕要求逐项落位",
    "，按成果清单、责任人和复核频次组织执行",
    "，把风险触发、处置动作和关闭标准连成链条",
    "，明确资料版本、审批边界及后续交接方式",
    "，围绕可交付成果建立检查顺序和反馈路径",
    "，把本项要求转成现场可执行的检查口径",
)


def _title_key(value):
    return key(peel_toc_lead(str(value or "")))


def _child_heading_style(doc, parent):
    for style in doc.styles:
        name = (style.name or "").lower()
        if name in {"heading 2", "标题 2", "heading2", "标题2"}:
            return style.name
    return parent.style.name


def _package_display_titles(package):
    from api.services.tenders.style_roles import clean_generated_text

    rows = []
    refs = []
    for chapter_index, chapter in enumerate(package.get("chapters", [])):
        chapter_title = str(chapter.get("title", "")).strip()
        rows.append(OutlineItem(id=f"chapter-{chapter_index}", title=clean_generated_text(chapter_title), level=1))
        refs.append(("chapter", chapter_title))
        for point in [p for p in package.get("sections", []) if p.get("chapter") == chapter_title]:
            point_id = str(point.get("id", ""))
            rows.append(OutlineItem(id=point_id, title=clean_generated_text(str(point.get("title", ""))), level=2))
            refs.append(("point", point_id))
    shows = subset_toc_titles(rows)
    chapter_titles = {}
    point_titles = {}
    for (kind, ref), shown in zip(refs, shows, strict=True):
        if kind == "chapter":
            chapter_titles[ref] = shown
        else:
            point_titles[ref] = shown
    return chapter_titles, point_titles


def _replace_paragraph_text(paragraph, value):
    from api.services.tenders.style_roles import clean_generated_text

    value = clean_generated_text(value)
    runs = paragraph.runs
    if not runs:
        paragraph.add_run(value)
        return
    runs[0].text = value
    for run in runs[1:]:
        run.text = ""


def anonymous_text(value, brief):
    value=str(value)
    names=[str(getattr(brief,n,'') or '').strip() for n in
           ['bidderName','bidderCreditCode','bidderAddress','bidderWebsite','bidderPhone','bidderFax','bidderEmail',
            'legalPersonName','legalPersonIdNo','agentName','agentIdNo','pmName','pmCertNo','bankAccount']]
    for name in sorted(set(names),key=len,reverse=True):
        if len(name)>=2:value=value.replace(name,'')
    value=re.sub(r'((?:统一社会)?信用代码\s*[:：]?\s*)[A-Z0-9]{18}(?![A-Z0-9])',r'\1',value)
    value=re.sub(r'[\u4e00-\u9fffA-Za-z0-9（）()·]{2,70}(?:有限责任公司|股份有限公司|有限公司)','',value)
    return value


def _clean(value,brief):
    return anonymous_text(value,brief) if brief.techAnonymous else str(value)


def _schedule_date_note(brief):
    """Quote supplied calendar milestones; never turn the bid date into a start date."""
    from datetime import date
    from api.services.tenders.assets import load_invitation_text
    source=re.sub(r'\s+','',load_invitation_text(brief.invitationId))
    dates={}
    for m in re.finditer(r'(?:计划)?(开工|开始|启动|竣工|完成|结束)(?:日期|时间)[:：]?(\d{4})[-年/.](\d{1,2})[-月/.](\d{1,2})日?',source):
        try:value=date(*map(int,m.group(2,3,4)))
        except ValueError:continue
        dates.setdefault('start' if m.group(1) in {'开工','开始','启动'} else 'end',value)
    labels=[]
    if dates.get('start'):labels.append('招标文件计划开始日期：'+dates['start'].isoformat())
    if dates.get('end'):labels.append('招标文件计划完成日期：'+dates['end'].isoformat())
    if (dates.get('start') and dates.get('end') and brief.deliveryDays>0
            and (dates['end']-dates['start']).days not in {brief.deliveryDays-1,brief.deliveryDays}):
        labels.append('计划起止日期与给定日历工期存在口径差异，须核定后换算绝对节点，不擅自改写招标日期')
    return '；'.join(labels)+('。' if labels else '')


def prune_unreferenced_images(doc):
    """Do not retain removed business scans as hidden, orphaned DOCX media."""
    for part in list(doc.part.package.parts):
        element=getattr(part,'_element',None)
        if element is None:continue
        used={value for node in element.iter() for name,value in node.attrib.items()
              if name in {qn('r:embed'),qn('r:link'),qn('r:id')}}
        for rel in list(part.rels.values()):
            if rel.reltype.endswith('/image') and rel.rId not in used:
                part.drop_rel(rel.rId)


def _markdown_blocks(body):
    """Yield text lines and complete Markdown tables in source order."""
    lines = iter(str(body).splitlines())
    line = next(lines, None)
    while line is not None:
        if not line.lstrip().startswith('|'):
            yield line
            line = next(lines, None)
            continue
        group = []
        while line is not None and line.lstrip().startswith('|'):
            group.append(line)
            line = next(lines, None)
        rows = [[cell.strip() for cell in re.split(r'(?<!\\)\|', row.strip().removeprefix('|').removesuffix('|'))]
                for row in group]
        if len(rows) >= 2 and all(re.fullmatch(r':?-{2,}:?', cell) for cell in rows[1]):
            yield {'columns': rows[0], 'rows': rows[2:]}
        else:
            yield from group


def _after(doc,node,body,brief, *, size=None, schedule=None, replace_schedule=False, duration=None):
    """Insert whole paragraphs, preserving the source heading and its next block."""
    cursor=node
    from api.services.tenders.style_roles import most_common_body_style

    body_style = most_common_body_style(doc)
    from api.services.tenders.style_roles import clean_generated_text

    for block in _markdown_blocks(body):
        if isinstance(block, dict):
            cells = _schedule_cells(block) if duration is not None else None
            if cells and replace_schedule:
                if schedule:
                    cursor = _insert_progress_table(doc, cursor, schedule, size, brief)
                    schedule = None
                continue
            if cells and not _within_duration(cells, duration):
                continue
            cursor = _insert_progress_table(doc, cursor, block, size, brief)
            continue
        line = block
        line = clean_generated_text(line)
        if not line.strip():continue
        el=OxmlElement('w:p');node.addnext(el) if cursor is node else cursor.addnext(el)
        para=Paragraph(el,doc)
        # Use the source body font; never inherit a numbered heading's numPr.
        props=node.find(qn('w:pPr'))
        if props is not None:
            copy=deepcopy(props)
            for tag in ['pStyle','numPr','outlineLvl','pageBreakBefore','keepNext','sectPr']:
                for child in list(copy.findall(qn('w:'+tag))):copy.remove(child)
            if body_style:
                p_style = OxmlElement('w:pStyle')
                p_style.set(qn('w:val'), doc.styles[body_style].style_id)
                copy.insert(0, p_style)
            el.append(copy)
        run = para.add_run(_clean(line,brief))
        if size is not None:
            run.font.size = size
            run.bold = False
        cursor=el
        if schedule and not replace_schedule:
            cursor = _insert_progress_table(doc, cursor, schedule, size, brief)
            schedule = None
    if schedule:
        cursor = _insert_progress_table(doc, cursor, schedule, size, brief)
    return cursor


def _invitation_validity_phrase(source):
    """Return a validity fact only when the invitation states its unit."""
    match = re.search(r"投标有效期[^\d\r\n]*(\d+)\s*(日历天|工作日|天|日)", source)
    if not match:
        return "投标有效期满足招标文件要求"
    return f"投标有效期为{match.group(1)}{match.group(2)}"


def _intro_fact_parts(chapter, brief, *, first: bool, facts: dict, source: str, duration: str) -> list[str]:
    values: dict[str, str] = {}
    project = str(getattr(brief, "projectName", "") or "").strip()
    if project:
        values["项目名称"] = project
    if duration:
        values["工期"] = duration
    scope = str(getattr(brief, "bidContent", "") or "").strip()
    if scope:
        values["招标范围"] = scope
    bond = getattr(brief, "bidBondYuan", None)
    if bond is not None:
        values["投标保证金"] = f"{bond:g}元"
    for key, value in facts.items():
        label = _FACT_LABELS.get(str(key))
        value = str(value or "").strip()
        if label and value and label not in values:
            values[label] = value

    if first:
        ordered = ["项目名称", "工期", "质量标准", "招标范围"]
        parts = [f"{label}为{values[label]}" for label in ordered if label in values]
        parts.append(_invitation_validity_phrase(source))
        if "投标保证金" in values:
            parts.append(f"投标保证金为{values['投标保证金']}")
        return parts

    if '设计' in chapter:
        scope = re.search(r'设计招标范围[：:]([^。\n]+)', source)
        terms = re.findall(r'[\u4e00-\u9fff]{2,}设计', scope.group(1) if scope else values.get("招标范围", ""))
        if terms:
            return ['设计交付核对' + '、'.join(term.removeprefix('本项目的') for term in terms[:2])]
    if any(token in chapter for token in ('施工', '实施', '进度', '计划', '安装')) and duration:
        return [f'以{duration}的合同工期约束工序衔接']
    if any(token in chapter for token in ('交付', '验收', '服务', '维护')):
        scope = re.search(r'竣工验收(?:及|和|与)移交(?:备案)?', values.get('招标范围', ''))
        if scope:
            return [f'将{scope.group()}纳入交接核验']
    if any(token in chapter for token in ('质量', '检验', '验收')) and values.get('质量标准'):
        return [f"按合同质量标准（{values['质量标准']}）复核成果"]
    return []


def _chapter_intro(chapter, brief, *, display_title=None, first=False, source="", duration=""):
    package = brief.technicalPackage if isinstance(brief.technicalPackage, dict) else {}
    source_title = str(chapter or '').strip() or '本章'
    chapter_title = peel_toc_lead(str(display_title or source_title))
    sections = [
        str(row.get('title') or '').strip()
        for row in package.get('sections', [])
        if isinstance(row, dict) and str(row.get('chapter') or '').strip() == source_title
    ]
    sections = list(dict.fromkeys(item for item in sections if item))
    facts = package.get('chapterFacts') if isinstance(package.get('chapterFacts'), dict) else {}
    points = [peel_toc_lead(item).replace(chapter_title, chapter_title[-2:]) for item in sections]
    fact_parts = _intro_fact_parts(chapter_title, brief, first=first, facts=facts, source=source, duration=duration)
    point_text = "、".join(points[:2]) or '评分要点、实施边界与验收条件'
    digest = hashlib.sha1(peel_toc_lead(source_title).encode("utf-8")).digest()
    start = _INTRO_STARTS[digest[0] % len(_INTRO_STARTS)].format(title=chapter_title, points=point_text)
    detail = _INTRO_DETAILS[digest[2] % len(_INTRO_DETAILS)]
    facts_text = "。".join(fact_parts)
    ending = _INTRO_ENDINGS[digest[1] % len(_INTRO_ENDINGS)].format(point=points[-1] if points else '评分要求')
    return start + detail + "。" + (facts_text + "。" if facts_text else "") + ending


def _clean_machine_paragraphs(doc):
    from api.services.tenders.style_roles import clean_generated_text, machine_trace_hits

    removed = 0
    for paragraph in list(doc.paragraphs):
        if not machine_trace_hits(paragraph.text):
            continue
        cleaned = clean_generated_text(paragraph.text)
        if cleaned:
            _replace_paragraph_text(paragraph, cleaned)
        else:
            parent = paragraph._p.getparent()
            if parent is not None:
                parent.remove(paragraph._p)
                removed += 1
    return removed


_SCHEDULE_TOPICS = ('施工', '实施', '进度', '计划')
_SCHEDULE_HEADERS = (
    ('阶段', '节点', '工序', '环节'),
    ('工作', '任务', '内容', '措施'),
    ('时间', '工期', '区间', '周期', '时序', '安排', '时限'),
    ('核验', '验收', '判据', '记录', '检查', '成果'),
)


def _schedule_cells(spec):
    columns = spec.get('columns') or []
    indices = [next((i for i, col in enumerate(columns) if any(term in col for term in terms)), None)
               for terms in _SCHEDULE_HEADERS]
    stage, work, time, evidence = indices
    if not spec.get('rows') or None in (stage, time, evidence) or len(set(indices) - {None}) != (4 if work is not None else 3):
        return None
    result = []
    for row in spec['rows']:
        if max(i for i in indices if i is not None) >= len(row):
            return None
        # Older structured plans express the work in the stage name itself.
        values = [str(row[i if i is not None else stage]).strip() for i in indices]
        if not all(values):
            return None
        result.append(values)
    return result


def _within_duration(rows, duration):
    limit = re.search(r'\d+', duration)
    for _, _, time, _ in rows:
        days = re.findall(r'(?i)\bD\s*(\d+)|(?:第|累计)?\s*(\d+)\s*(?:日历天|工作日|天|日)(?!期)', time)
        if any(not limit or not 0 < int(a or b) <= int(limit.group()) for a, b in days):
            return False
        if '工作日' in time and '工作日' not in duration:
            return False
        if re.search(r'\d+\s*[年月]', time):
            return False
    return True


def _schedule_from_prose(points, duration):
    """Use a point's explicit timing and actions with its stated evidence."""
    evidence = re.compile(
        r'(?:^|[。；，])(?:并)?(?:以|通过|保存|留存|留有|保留)'
        r'([^；。]*(?:记录|台账|清单|纪要|凭证|签认|报表|确认)[^；。]*)'
    )
    deliverable = re.compile(r'(?:办理|建立|留存|形成|保存|保留)[^，；。]*(?:记录|台账|清单|纪要|凭证|签认|报表|确认)[^，；。]*')
    shared_evidence = re.compile(r'核验方式以([^。；]+?)(?:作为依据|[。；]|$)')
    timing = re.compile(
        r'^(?:在|于)?([\u4e00-\u9fff]{2,18}?(?:阶段|期间|过程中|前|后|时))'
        r'(?=[，：:]|按|配合|组织|开展|提交|建立|落实|汇总|编制|办理|对|由|及时|严格)'
    )
    by_point = []
    for point in points:
        rows = []
        for block in _markdown_blocks(point['body']):
            if not isinstance(block, str):
                continue
            shared = shared_evidence.search(block)
            for sentence in re.split(r'[。！？；]', block):
                period = timing.search(sentence)
                if not period:
                    continue
                work = sentence[period.end():].lstrip('，：:')
                proof = evidence.search(work)
                output = deliverable.search(work) if not proof else None
                verification = proof[1] if proof else output[0] if output else shared[1] if shared else ''
                work = work[:proof.start()] if proof else work
                if not work.strip() or not verification:
                    continue
                when = period.group(1)
                stage = re.sub(r'(?:阶段|期间|过程中|前|后|时)$', '', when)
                row = [stage, work, when, verification]
                if row not in rows:
                    rows.append(row)
        if rows and _within_duration(rows, duration):
            by_point.append((point['id'], rows))
    if by_point:
        best = max(by_point, key=lambda entry: len(entry[1]))
        if len(best[1]) >= 2:
            return best
        if len(by_point) >= 2:
            return best[0], [row for _, rows in by_point for row in rows]
    return None, []


def _chapter_schedules(chapters, points_by_chapter, specs, duration):
    """Assign each structured schedule to its best matching chapter once."""
    plans = {}
    for spec in specs:
        rows = _schedule_cells(spec)
        if not rows or not _within_duration(rows, duration):
            continue
        title = str(spec.get('title') or '')
        terms = [word for word in ('设计', '施工', '安装', '采购', '调试', '验收') if word in title]
        ranked = []
        for chapter in chapters:
            name = chapter['title']
            points = points_by_chapter[name]
            if name in plans or (spec.get('chapter') and _title_key(spec['chapter']) != _title_key(name)):
                continue
            if not any(term in name or any(term in p['title'] for p in points) for term in _SCHEDULE_TOPICS):
                continue
            for point in points:
                score = sum(word in point['title'] for word in terms)
                ranked.append(((score, sum(word in name for word in terms), '实施' in name), name, point['id']))
        if ranked:
            _, name, point = max(ranked, key=lambda candidate: candidate[0])
            plans[name] = point, rows
    return plans


def _insert_progress_table(doc, cursor, table_data, size, brief=None):
    """Write a fixed grid at the source location for either table representation."""
    data = [table_data['columns'], *table_data['rows']]
    count = max(map(len, data))
    if not count:
        return cursor
    siblings = (cursor, *cursor.itersiblings()) if cursor is not None else ()
    section = next((node.find('./' + qn('w:pPr') + '/' + qn('w:sectPr'))
                    for node in siblings
                    if node.find('./' + qn('w:pPr') + '/' + qn('w:sectPr')) is not None), None)
    layout = Section(section, doc.part) if section is not None else doc.sections[-1]
    width = (layout.page_width - layout.left_margin - layout.right_margin) // count
    table = doc.add_table(rows=len(data), cols=count)
    table.style = 'Table Grid'
    table.autofit = False
    for column in table.columns:
        column.width = width
    for index, (row, values) in enumerate(zip(table.rows, data, strict=True)):
        for column, cell in enumerate(row.cells):
            cell.width = width
            value = str(values[column]).replace(r'\|', '').replace('|', '') if column < len(values) else ''
            run = cell.paragraphs[0].add_run(_clean(value, brief) if brief is not None else value)
            run.font.size = size or Pt(brief.documentFormat.bodySizePt)
            run.bold = index == 0
    if cursor is not None:
        cursor.addnext(table._tbl)
    return table._tbl


def _font(size):
    from PIL import ImageFont
    from pathlib import Path
    for path in [Path('C:/Windows/Fonts/simsun.ttc'),Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]:
        if path.is_file():return ImageFont.truetype(str(path),size)
    return ImageFont.load_default()


def _diagram(package,kind,brief):
    from PIL import Image,ImageDraw
    if kind=='schedule':
        stages=package['stages'];positions={}
        # Dependency levels produce a true network, not an illustration prompt.
        levels={}
        for s in stages:levels[str(s['id'])]=1+max([levels[str(d)] for d in s.get('dependsOn',[])]+[-1])
        width=490
        for index,s in enumerate(stages):
            positions[str(s['id'])]=(60+(index%3)*width,50+(index//3)*280)
        height=150+((len(stages)+2)//3)*280
        image=Image.new('RGB',(1600,height),'white');draw=ImageDraw.Draw(image);font=_font(36)
        for s in stages:
            x,y=positions[str(s['id'])]
            for dep in s.get('dependsOn',[]):
                a,b=positions[str(dep)]
                if b==y:
                    draw.line((a+440,b+100,x,y+100),fill='black',width=3)
                    draw.polygon([(x,y+100),(x-14,y+92),(x-14,y+108)],fill='black')
                else:
                    draw.line([(a+220,b+215),(a+220,b+240),(x+220,b+240),(x+220,y)],fill='black',width=3)
                    draw.polygon([(x+220,y),(x+212,y-14),(x+228,y-14)],fill='black')
        for s in stages:
            x,y=positions[str(s['id'])];draw.rectangle((x,y,x+440,y+215),outline='black',width=3)
            label=_clean(s['name'],brief)
            draw.text((x+10,y+12),'\n'.join(label[i:i+11] for i in range(0,len(label),11)),fill='black',font=font)
            draw.text((x+10,y+165),f"D{s['startDay']} - D{s['endDay']}",fill='black',font=font)
        draw.text((70,height-55),'D：相对开工日；箭头表示前置依赖，开工日期按确认指令执行。',fill='black',font=font)
    else:
        image=Image.new('RGB',(1600,1000),'white');draw=ImageDraw.Draw(image);font=_font(36)
        draw.rectangle((50,50,1550,900),outline='black',width=4)
        for z in package['zones']:
            x,y=50+int(float(z['x'])*1500),50+int(float(z['y'])*850)
            w,h=int(float(z['w'])*1500),int(float(z['h'])*850)
            draw.rectangle((x,y,min(1550,x+w),min(900,y+h)),outline='black',width=3)
            label=_clean(z['name'],brief);step=max(3,w//38)
            draw.text((x+10,y+15),'\n'.join(label[i:i+step] for i in range(0,len(label),step)),fill='black',font=font)
        draw.text((60,940),'示意布置；尺寸、位置及安全距离须按确认图纸与现场复核。',fill='black',font=font)
    stream=BytesIO();image.save(stream,format='PNG');stream.seek(0);return stream


def render(doc,brief):
    from api.services.tenders.document import _bookmark_paragraph
    from api.services.tenders.pdf_format import _word_format_file
    from docx import Document
    package=brief.technicalPackage
    machine_removed = _clean_machine_paragraphs(doc)
    chapter_titles, point_titles = _package_display_titles(package)
    notes=[]
    if machine_removed:
        notes.append(f"技术卷已清理评分提示语 {machine_removed} 段")
    if not brief.techAnonymous:
        from api.services.tenders.company_identity import foreign_bidder
        from common.errors import AppError,ErrorCode
        if any(foreign_bidder(p['body'],brief.bidderName) for p in package['sections']):
            raise AppError(ErrorCode.VALIDATION,'技术明标正文出现其他投标人身份，请核对后重新起草',status_code=422)
    markers={m.get(qn('w:name')) for m in doc.element.iter(qn('w:bookmarkStart'))}
    paragraphs=list(doc.paragraphs)
    source_path=_word_format_file(brief.invitationId)
    source=Document(source_path) if source_path else None
    source_props={key(p.text):p._p.pPr for p in source.paragraphs} if source is not None else {}
    if package.get('template_kind') == 'chapter_heading':
        from api.services.tenders.assets import load_invitation_text
        from api.services.tenders.technical_toc import _regions

        toc_nodes = {node for region in _regions(doc) for node in region}
        chapter_nodes={_title_key(p.text):p for p in paragraphs
                       if p._p not in toc_nodes and p.text.strip()}
        size = Pt(brief.documentFormat.bodySizePt)
        rendered = {}
        chapters = list(package.get('chapters', []) or [])
        invitation = load_invitation_text(brief.invitationId) or ''
        stated = re.search(r'(?:计划|总)工期[\s|：:]*([^\n|。；，,\d]*\d+\s*(?:日历天|工作日|天|日))', invitation)
        duration = stated.group(1).strip() if stated else ''
        points_by_chapter = {c['title']: [p for p in package['sections'] if p['chapter'] == c['title']]
                             for c in chapters}
        plans = _chapter_schedules(chapters, points_by_chapter, package.get('tables', []), duration)
        numbered_refs = {
            variant: peel_toc_lead(title)
            for title, display in chapter_titles.items()
            for variant in (title, display)
            if variant != peel_toc_lead(variant)
        }
        for chapter_index, chapter in enumerate(chapters):
            head=chapter_nodes.get(_title_key(chapter['title']))
            if head is None:
                raise ValueError('技术评分章未找到落位：'+chapter['title'])
            head.style = 'Heading 1'
            _replace_paragraph_text(head, _clean(chapter_titles[chapter['title']], brief))
            _bookmark_paragraph(head, f'toc_technical_chapter_{chapter_index}')
            cursor=head._p
            display_title = chapter_titles.get(chapter['title'], chapter['title'])
            cursor = _after(
                doc,
                cursor,
                _chapter_intro(
                    chapter['title'],
                    brief,
                    display_title=display_title,
                    first=chapter is chapters[0],
                    source=invitation,
                    duration=duration,
                ),
                brief,
                size=size,
            )
            chapter_points = points_by_chapter[chapter['title']]
            schedule_point, rows = plans.get(chapter['title'], (None, []))
            markdown_schedules = {
                point['id']: [block for block in _markdown_blocks(point['body'])
                              if isinstance(block, dict) and _schedule_cells(block) is not None]
                for point in chapter_points
            }
            if rows and markdown_schedules[schedule_point]:
                replacement_point = schedule_point
            else:
                replacement_point = None
            eligible = any(term in chapter['title'] or any(term in p['title'] for p in chapter_points)
                           for term in _SCHEDULE_TOPICS)
            valid_markdown = any(_within_duration(_schedule_cells(block), duration)
                                 for blocks in markdown_schedules.values() for block in blocks)
            if eligible and not rows and not valid_markdown:
                schedule_point, rows = _schedule_from_prose(chapter_points, duration)
                if not rows:
                    notes.append(f"技术卷进度表缺项：{head.text}缺少可对应的阶段、工作、时间安排和核验证据")
            for point in chapter_points:
                el=OxmlElement('w:p'); cursor.addnext(el); sub=Paragraph(el,doc)
                sub.style=_child_heading_style(doc, head)
                from api.services.tenders.assemble import _run
                _run(
                    sub,
                    _clean(point_titles[point['id']], brief),
                    size=None,
                    bold=False,
                    font=brief.documentFormat.fontName,
                )
                cursor=el
                body = point['body']
                for ref, bare in numbered_refs.items():
                    body = body.replace('“' + ref + '”', '“' + bare + '”').replace('"' + ref + '"', '"' + bare + '"')
                schedule = {'columns': ['阶段', '工作内容', '时间安排', '执行与核验方式'], 'rows': rows}
                cursor=_after(doc,cursor,body,brief,size=size,
                              schedule=schedule if point['id'] == schedule_point else None,
                              replace_schedule=point['id'] == replacement_point,
                              duration=duration)
                body_lines = []
                node = el
                while node is not cursor:
                    node = node.getnext()
                    if node.tag == qn('w:p'):
                        body_lines.append(Paragraph(node, doc).text)
                rendered[point['id']] = '\n'.join(body_lines)
                _bookmark_paragraph(sub,'toc_technical_'+point['id'])
        from api.services.tenders.scoring_points import scoring_uniqueness

        uniqueness = scoring_uniqueness([rendered[point['id']] for point in package['sections']])
        package['qa'] = {
            'scoringUniqueness': uniqueness,
            'scoringItems': [{'id': point['id'], 'title': point['title'],
                              'degraded': bool(point.get('degraded'))}
                             for point in package['sections']],
        }
        logger.warning('技术评分项正文重复度：max=%.3f dup=%d severe=%d', uniqueness['max_jaccard'], uniqueness['dup_count'], uniqueness['severe_count'])
        notes.append(f"技术卷：已写入 {len(package['sections'])} 个评分项；重复度 max={uniqueness['max_jaccard']:.3f} dup={uniqueness['dup_count']} severe={uniqueness['severe_count']}")
        if uniqueness['severe_count'] > 0:
            notes.append('内容重复度超标')
        prune_unreferenced_images(doc)
        return notes
    for point in package['sections']:
        marker='technical_'+point['id']
        head_marker='toc_technical_'+point['id']
        heading=next((p for p in paragraphs if _title_key(p.text)==_title_key(point['title']) and 'PAGEREF' not in p._p.xml),None)
        if heading is None:
            raise ValueError('技术要点未找到原版落位：'+point['title'])
        _replace_paragraph_text(heading, point_titles.get(point.get('id'), point['title']))
        # The legacy technical slicer promotes short numbered list entries to
        # top-level headings. Keep their original list/indent/font properties;
        # the dedicated TOC rows carry the requested child hierarchy instead.
        if key(point['title']) in source_props:
            current=heading._p.pPr
            original=source_props[key(point['title'])]
            signature=lambda p:None if p is None else [(n.tag,sorted(n.attrib.items())) for n in p.iter()]
            if signature(current)!=signature(original):
                if current is not None:heading._p.remove(current)
                if original is not None:heading._p.insert(0,deepcopy(original))
        if head_marker not in markers:_bookmark_paragraph(heading,head_marker)
        if marker in markers:continue
        end=_after(doc,heading._p,point['body'],brief)
        first = heading._p.getnext()
        if first.tag != qn('w:p'):
            first = heading._p
        _bookmark_paragraph(Paragraph(first,doc),marker)
    # Only original, empty data cells are writable. Neither headers nor columns move.
    for spec in package.get('tables',[]):
        heading=next((p for p in doc.paragraphs if p.text.strip()==spec['title'] and 'PAGEREF' not in p._p.xml),None)
        node=heading._p.getnext() if heading else None
        while node is not None and node.tag==qn('w:p') and not re.match(r'^附[表件]',text(node).strip()):node=node.getnext()
        if node is None or node.tag!=qn('w:tbl'):raise ValueError('技术附表未找到原版表格：'+spec['title'])
        from docx.table import Table
        table=Table(node,doc)
        for index,values in enumerate(spec['rows'][:spec['capacity']],spec['firstDataRow']):
            for cell,value in zip(table.rows[index].cells,values):
                if cell.text.strip():continue
                paragraph=cell.paragraphs[0]
                run=paragraph.runs[0] if paragraph.runs else paragraph.add_run()
                run.text=_clean(value,brief)
        if spec.get('note') and 'technical_'+spec['id'] not in markers:
            end=_after(doc,node,'拟投入计划说明：'+spec['note'],brief)
            _bookmark_paragraph(Paragraph(end,doc),'technical_'+spec['id'])
    if brief.techAnonymous:
        # Unknown source/library scans may expose identity in pixels. Generated
        # diagrams are added only after this pass and carry no identity fields.
        for tag in ['drawing','pict']:
            for el in list(doc.element.body.iter(qn('w:'+tag))):
                p=next(el.iterancestors(qn('w:p')),None)
                if p is not None and any(m.get(qn('w:name'),'').startswith('technical_figure_') for m in p.iter(qn('w:bookmarkStart'))):continue
                el.getparent().remove(el)
    for figure in package.get('figures',[]):
        marker='technical_figure_'+figure['kind']
        if marker in markers:continue
        heading=next((p for p in doc.paragraphs if p.text.strip()==figure['title'] and 'PAGEREF' not in p._p.xml),None)
        if heading is None:continue
        node=heading._p
        cursor=node.getnext()
        while cursor is not None and cursor.tag==qn('w:p') and not re.match(r'^附[表件]',text(cursor).strip()):
            node=cursor;cursor=cursor.getnext()
        el=OxmlElement('w:p');node.addnext(el);p=Paragraph(el,doc)
        p.add_run().add_picture(_diagram(package,figure['kind'],brief),width=Cm(15))
        _bookmark_paragraph(p,marker)
        if figure['kind']=='schedule':
            explanation=_schedule_date_note(brief)+'；'.join(f"{s['name']}：第{s['startDay']}—{s['endDay']}日" for s in package['stages'])
            explanation+='。以上为相对开工日的拟定节点，实际日期按经确认的开工指令与实施条件换算。'
        else:
            explanation=package.get('note','')
        _after(doc,el,explanation,brief)
    from api.services.tenders.scoring_points import scoring_uniqueness

    uniqueness = scoring_uniqueness([str(point.get('body', '')) for point in package.get('sections', [])])
    package.setdefault('qa', {})['scoringUniqueness'] = uniqueness
    logger.warning('技术评分项正文重复度：max=%.3f dup=%d severe=%d', uniqueness['max_jaccard'], uniqueness['dup_count'], uniqueness['severe_count'])
    notes.append(f"技术卷：已写入 {len(package['sections'])} 个原版要点、{len(package['tables'])} 个附表及 {len(package['figures'])} 张计划图；重复度 max={uniqueness['max_jaccard']:.3f} dup={uniqueness['dup_count']} severe={uniqueness['severe_count']}")
    if uniqueness['severe_count'] > 0:
        notes.append('内容重复度超标')
    prune_unreferenced_images(doc)
    return notes


def sync_toc(doc,brief):
    from api.services.tenders.technical_toc import sync
    return sync(doc, brief)
