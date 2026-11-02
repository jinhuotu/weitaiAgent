from docx import Document
from docx.oxml.ns import qn
from PIL import Image
from api.services.tenders import section_materials as placement
from api.services.tenders.schema import PlaceholderItem


def test_numbering_defines_physical_scopes_despite_heading6_and_blank_styles(monkeypatch,tmp_path):
    doc=Document()
    heads=[]
    for title in ['投标函','法定代表人身份证明','投标人基本情况表','残疾人福利性单位声明函']:
        head=doc.add_paragraph(title,style='Heading 6');heads.append(head._p)
        doc.add_paragraph('',style='Heading 6')
        if '身份证明' in title:
            cue=doc.add_paragraph('附：法定代表人身份证复印件或扫描件')
            doc.add_paragraph('投标人签字：________')
        elif '基本情况' in title:
            table=doc.add_table(rows=1,cols=1);table.cell(0,0).text='投标人基本情况'
            doc.add_paragraph('注：应附营业执照扫描件')
        else:
            doc.add_paragraph('我方投标文件包括营业执照、身份证。')
    numbering={head:('1',0,'chineseCounting','%1、',1,1) for head in heads}
    monkeypatch.setattr(placement,'numbering_info',lambda doc,el:numbering.get(el))
    scopes=placement.sections(doc,set())
    assert [h for h,_,_ in scopes]==heads
    images=[]
    for n,color in enumerate(['red','blue']):
        p=tmp_path/f'{n}.png';Image.new('RGB',(40,40),color).save(p);images.append(p)
    slots=[PlaceholderItem(key='id',title='法定代表人身份证',attachToSection='法定代表人身份证明'),
           PlaceholderItem(key='license',title='营业执照',attachToSection='投标人基本情况表')]
    placement.append_section_materials(doc,slots,{'id':[images[0]],'license':[images[1]]},['id','license'])
    flow=[(placement._heading_text(h),sum(len(list(el.iter(qn('w:drawing')))) for el in body)) for h,body,_ in placement.sections(doc,set())]
    assert [count for _,count in flow]==[0,1,1,0]
    node=cue._p.getnext()
    while node is not None and not list(node.iter(qn('w:drawing'))):
        node=node.getnext()
    assert node is not None


def test_unknown_owner_never_falls_back_to_last_chapter():
    doc=Document();doc.add_paragraph('残疾人福利性单位声明函',style='Heading 1')
    doc.add_paragraph('本单位声明属实。')
    before=doc.element.body.xml
    placement.append_section_materials(doc,[PlaceholderItem(key='unknown',title='陌生证明',attachToSection='不存在章节')],{},['unknown'])
    assert doc.element.body.xml==before
