"""Render source-owned technical points/tables and data-driven plan diagrams."""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import re

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm
from docx.text.paragraph import Paragraph

from api.services.tenders.technical_package import key,text


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


def _after(doc,node,body,brief):
    """Insert whole paragraphs, preserving the source heading and its next block."""
    cursor=node
    for line in str(body).splitlines():
        if not line.strip():continue
        el=OxmlElement('w:p');node.addnext(el) if cursor is node else cursor.addnext(el)
        para=Paragraph(el,doc)
        # Use the source body font; never inherit a numbered heading's numPr.
        props=node.find(qn('w:pPr'))
        if props is not None:
            copy=deepcopy(props)
            for tag in ['numPr','outlineLvl','pageBreakBefore','keepNext']:
                for child in list(copy.findall(qn('w:'+tag))):copy.remove(child)
            el.append(copy)
        para.add_run(_clean(line,brief));cursor=el
    return cursor


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
    notes=[]
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
        chapter_nodes={key(p.text):p for p in paragraphs}
        for chapter in package.get('chapters',[]):
            head=chapter_nodes.get(key(chapter['title']))
            if head is None: continue
            cursor=head._p
            for point in [x for x in package['sections'] if x.get('chapter')==chapter['title']]:
                el=OxmlElement('w:p'); cursor.addnext(el); sub=Paragraph(el,doc)
                sub.style=head.style
                sub.add_run(point['title'])
                cursor=el
                end=_after(doc,cursor,point['body'],brief)
                cursor=end
                _bookmark_paragraph(sub,'toc_technical_'+point['id'])
        notes.append(f"技术卷：已写入 {len(package['sections'])} 个评分项")
        prune_unreferenced_images(doc)
        return notes
    for point in package['sections']:
        marker='technical_'+point['id']
        head_marker='toc_technical_'+point['id']
        heading=next((p for p in paragraphs if key(p.text)==key(point['title']) and 'PAGEREF' not in p._p.xml),None)
        if heading is None:
            raise ValueError('技术要点未找到原版落位：'+point['title'])
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
    notes.append(f"技术卷：已写入 {len(package['sections'])} 个原版要点、{len(package['tables'])} 个附表及 {len(package['figures'])} 张计划图")
    prune_unreferenced_images(doc)
    return notes


def sync_toc(doc,brief):
    from api.services.tenders.technical_toc import sync
    return sync(doc, brief)
