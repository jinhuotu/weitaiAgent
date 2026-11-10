"""Render source-owned technical points/tables and data-driven plan diagrams."""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import logging
import re

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm
from docx.text.paragraph import Paragraph

from api.services.tenders.technical_package import key,text
from api.services.tenders.format_rules import peel_toc_lead
from api.services.tenders.outline import subset_toc_titles
from api.services.tenders.schema import OutlineItem

logger = logging.getLogger("api.tenders.technical_render")


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


def _after(doc,node,body,brief, *, size=None):
    """Insert whole paragraphs, preserving the source heading and its next block."""
    cursor=node
    from api.services.tenders.style_roles import most_common_body_style

    body_style = most_common_body_style(doc)
    from api.services.tenders.style_roles import clean_generated_text

    for line in str(body).splitlines():
        line = clean_generated_text(line)
        if not line.strip():continue
        el=OxmlElement('w:p');node.addnext(el) if cursor is node else cursor.addnext(el)
        para=Paragraph(el,doc)
        # Use the source body font; never inherit a numbered heading's numPr.
        props=node.find(qn('w:pPr'))
        if props is not None:
            copy=deepcopy(props)
            for tag in ['pStyle','numPr','outlineLvl','pageBreakBefore','keepNext']:
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
    return cursor


def _chapter_intro(chapter, brief):
    package = brief.technicalPackage if isinstance(brief.technicalPackage, dict) else {}
    chapter_title = str(chapter or '').strip() or '本章'
    sections = [
        str(row.get('title') or '').strip()
        for row in package.get('sections', [])
        if isinstance(row, dict) and str(row.get('chapter') or '').strip() == chapter_title
    ]
    sections = list(dict.fromkeys(item for item in sections if item))
    facts = package.get('chapterFacts') if isinstance(package.get('chapterFacts'), dict) else {}
    fact_parts = []
    if (brief.projectName or '').strip():
        fact_parts.append(f"项目名称为{brief.projectName.strip()}")
    for key, value in facts.items():
        value = str(value or '').strip()
        if not value or key == '项目名称' or key in chapter_title:
            continue
        fact_parts.append(f"{key}为{value}")
        if len(fact_parts) >= 3:
            break
    point_text = '、'.join(sections[:4])
    intro = f"本章围绕“{chapter_title}”展开"
    if point_text:
        intro += f"，重点处理{point_text}"
    if fact_parts:
        intro += f"；结合{'，'.join(fact_parts)}"
    return intro + "，形成本章可执行、可检查、可追溯的工作安排。"


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


def _insert_progress_table(doc, cursor, table_data, size):
    """Insert the non-empty schedule immediately after the implementation intro."""
    columns = list(table_data.get('columns') or [])
    rows = list(table_data.get('rows') or [])
    if not columns or not rows:
        return cursor
    table = doc.add_table(rows=1, cols=len(columns))
    table.style = 'Table Grid'
    for cell, value in zip(table.rows[0].cells, columns, strict=True):
        cell.text = str(value)
    for row in rows:
        cells = table.add_row().cells
        for cell, value in zip(cells, row, strict=True):
            cell.text = str(value)
    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.size = size
                    run.bold = False
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
        from api.services.tenders.technical_toc import _regions
        from api.services.tenders.style_roles import most_common_body_style
        from docx.shared import Pt

        toc_nodes = {node for region in _regions(doc) for node in region}
        chapter_nodes={_title_key(p.text):p for p in paragraphs
                       if p._p not in toc_nodes and p.text.strip()}
        body_style = most_common_body_style(doc) or doc.styles.default(1)
        size = Pt(brief.documentFormat.bodySizePt)
        rendered = {}
        headings = set()
        for index, chapter in enumerate(package.get('chapters',[])):
            head=chapter_nodes.get(_title_key(chapter['title']))
            if head is None:
                raise ValueError('技术评分章未找到落位：'+chapter['title'])
            head.style = 'Heading 1'
            headings.add(head._p)
            _replace_paragraph_text(head, _clean(chapter_titles[chapter['title']], brief))
            _bookmark_paragraph(head, f'toc_technical_chapter_{index}')
            cursor=head._p
            cursor = _after(doc, cursor, _chapter_intro(chapter['title'], brief), brief, size=size)
            if index == 2:
                schedule = next(
                    (table for table in package.get('tables', [])
                     if '进度' in str(table.get('title', ''))),
                    None,
                )
                if schedule:
                    cursor = _insert_progress_table(doc, cursor, schedule, size)
            for point in [x for x in package['sections'] if x.get('chapter')==chapter['title']]:
                el=OxmlElement('w:p'); cursor.addnext(el); sub=Paragraph(el,doc)
                sub.style=_child_heading_style(doc, head)
                sub.add_run(_clean(point_titles[point['id']], brief))
                cursor=el
                cursor=_after(doc,cursor,point['body'],brief,size=size)
                body_lines = []
                node = el
                while node is not cursor:
                    node = node.getnext()
                    body_lines.append(Paragraph(node, doc).text)
                rendered[point['id']] = '\n'.join(body_lines)
                _bookmark_paragraph(sub,'toc_technical_'+point['id'])
        for p in paragraphs:
            if p._p not in headings and p.style.name in {'Heading 1', 'Heading 2'}:
                p.style = body_style
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
        _bookmark_paragraph(Paragraph(heading._p.getnext(),doc),marker)
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
