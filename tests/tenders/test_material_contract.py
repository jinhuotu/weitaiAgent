"""Quantitative contract for three unrelated source layouts and shared entrypoints."""
import hashlib
import json
from pathlib import Path

import pytest
from docx import Document
from docx.oxml.ns import qn
from PIL import Image

from api.services.tenders.schema import PlaceholderItem
from api.services.tenders.placeholders import place_unmatched_slots
from api.services.tenders.scan_placement import place_library_scans
from api.services.tenders.classifier import material_output_coverage
from api.services.tenders.section_materials import sections,_heading_text


def create_source(path, style, sector):
    doc=Document()
    doc.add_paragraph('投标函',style=style)
    doc.add_paragraph(f'{sector}采购：我方投标文件包括登记证明、身份资料。')
    doc.add_paragraph('法定代表人身份证明',style=style)
    doc.add_paragraph('本单位法定代表人签字：________')
    doc.add_paragraph('附：法定代表人身份证复印件')
    doc.add_paragraph('资格材料',style=style)
    for letter,name in zip('ABC',['陌生佐证','登记证明','信用证明']):
        doc.add_paragraph(f'{letter}.{name}：按原版提交完整证明材料。')
    doc.add_paragraph('');doc.add_paragraph('')
    doc.add_paragraph('后续原版说明不得移动。')
    doc.add_paragraph('人员材料表',style=style)
    table=doc.add_table(rows=2,cols=2)
    table.cell(0,0).text='人员';table.cell(0,1).text='职责'
    table.cell(1,0).text='姓名待填';table.cell(1,1).text='职责待填'
    doc.add_paragraph('附：任职证明扫描件')
    doc.add_paragraph('声明函',style=style)
    doc.add_paragraph('原版声明正文及落款不变。')
    headings=[p for p in doc.paragraphs if p.style.name.startswith('Heading')]
    if style=='Heading 1':
        for label,p in zip(['一','二','三','四','五'],headings):
            p.text=label+'、'+p.text
    elif style=='Heading 3':
        for index,p in enumerate(headings,1):p.text=str(index)+'.'+p.text
    else:
        from docx.oxml import OxmlElement
        root=doc.part.numbering_part.element
        abstract=OxmlElement('w:abstractNum');abstract.set(qn('w:abstractNumId'),'71')
        level=OxmlElement('w:lvl');level.set(qn('w:ilvl'),'0')
        for name,value in [('start','1'),('numFmt','chineseCounting'),('lvlText','%1、')]:
            node=OxmlElement('w:'+name);node.set(qn('w:val'),value);level.append(node)
        abstract.append(level);root.append(abstract)
        number=OxmlElement('w:num');number.set(qn('w:numId'),'71')
        ref=OxmlElement('w:abstractNumId');ref.set(qn('w:val'),'71');number.append(ref);root.append(number)
        for p in headings:
            props=OxmlElement('w:numPr')
            for name,value in [('ilvl','0'),('numId','71')]:
                node=OxmlElement('w:'+name);node.set(qn('w:val'),value);props.append(node)
            p._p.get_or_add_pPr().append(props)
    doc.save(path)


@pytest.mark.parametrize('sector,style',[('设备采购','Heading 1'),('工程建设','Heading 6'),('专业服务','Heading 3')])
def test_all_material_demands_have_one_block_result(tmp_path,sector,style):
    fixture=Path(__file__).parent/'fixtures'/({'Heading 1':'equipment-chinese.docx','Heading 6':'engineering-auto.docx','Heading 3':'services-decimal.docx'}[style])
    source=fixture if fixture.is_file() else tmp_path/'source.docx'
    if not fixture.is_file():create_source(source,style,sector)
    doc=Document(source)
    originals=[(p,p.text) for p in doc.paragraphs]
    before_breaks=sum(br.get(qn('w:type'))=='page' for br in doc.element.body.iter(qn('w:br')))
    identity=tmp_path/'id.png';Image.new('RGB',(856,540),'white').save(identity)
    slots=[PlaceholderItem(key='identity',title='法定代表人身份证',attachToSection='法定代表人身份证明'),
           *[PlaceholderItem(key=key,title=title,attachToSection='资格材料') for key,title in [('unknown','陌生佐证'),('registry','登记证明'),('credit','信用证明')]],
           PlaceholderItem(key='appointment',title='任职证明',attachToSection='人员材料表')]
    owners={slot.key:slot.attachToSection for slot in slots}
    media={'identity':[identity],'registry':[],'unknown':[],'credit':[],'appointment':[]}
    place_unmatched_slots(doc,slots=slots,media=media,required_keys=list(owners))
    snapshot=doc.element.xml
    place_library_scans(doc,slots=slots,media=media)
    place_unmatched_slots(doc,slots=slots,media=media,required_keys=list(owners))
    assert doc.element.xml==snapshot
    report=material_output_coverage(doc,owners)
    assert report['expected']==report['outputs']==5
    assert report['unmet']==report['duplicates']==0
    assert all(paragraph.text==text for paragraph,text in originals)
    assert not list(doc.element.body.iter(qn('w:pageBreakBefore')))
    after_breaks=sum(br.get(qn('w:type'))=='page' for br in doc.element.body.iter(qn('w:br')))
    empty_boxes=0;inside_run=0
    for p in doc.element.body.iter(qn('w:p')):
        if p.find('w:pPr/w:pBdr',p.nsmap) is not None:
            empty_boxes+=not ''.join(t.text or '' for t in p.iter(qn('w:t'))).strip()
        for mark in p.iter(qn('w:bookmarkStart')):
            if mark.get(qn('w:name'),'').startswith('result_'):
                inside_run+=mark.getparent().tag!=qn('w:p')
    assert empty_boxes==inside_run==0
    stream=[]
    for head,body,_ in sections(doc,set()):
        results=[b.get(qn('w:name')) for block in body for b in block.iter(qn('w:bookmarkStart')) if b.get(qn('w:name'),'').startswith('result_')]
        stream.append({'chapter':_heading_text(head),'results':len(results)})
    assert [row['results'] for row in stream]==[0,1,3,1,0]
    doc.save(tmp_path/'result.docx')
    placeholder_count=sum(bool(p.find('w:pPr/w:pBdr',p.nsmap) is not None) for p in doc.element.body.iter(qn('w:p')))
    image_count=len(list(doc.element.body.iter(qn('w:drawing'))))
    print(json.dumps({'before':{'placeholders':0,'images':0,'empty_boxes':0,'unmet':5},'after':{'placeholders':placeholder_count,'images':image_count,'empty_boxes':empty_boxes,'unmet':report['unmet']},'sector':sector,'internal_insertions':inside_run,'empty_boxes':empty_boxes,'unmet':report['unmet'],
                      'duplicate_keys':report['duplicates'],'breaks_before':before_breaks,'breaks_after':after_breaks,'flow':stream},ensure_ascii=True))


def test_unknown_section_uses_separate_warned_attachment_area():
    doc=Document();doc.add_paragraph('声明函',style='Heading 1');doc.add_paragraph('原文')
    notes=place_unmatched_slots(doc,slots=[PlaceholderItem(key='unknown',title='无归属佐证')],media={},required_keys=['unknown'])
    assert any('警告' in note and '已降级' in note for note in notes)
    report=material_output_coverage(doc,{'unknown':'其他资料（待核对归属）'})
    assert report['outputs']==1 and report['unmet']==0
