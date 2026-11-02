import pytest
from docx import Document
from docx.oxml.ns import qn
from PIL import Image
from api.services.tenders.schema import PlaceholderItem
from api.services.tenders.section_materials import append_section_materials


@pytest.mark.parametrize('specified',[True,False])
def test_identity_faces_share_paragraph_and_fit_standard_height(tmp_path,specified):
    doc=Document();doc.add_paragraph('法人身份证明',style='Heading 1')
    doc.add_paragraph('附：法定代表人身份证复印件' if specified else '本单位法定代表人签字：________')
    stop=doc.add_paragraph('下一章',style='Heading 1')
    paths=[]
    for n,color in enumerate(['red','blue']):
        p=tmp_path/f'{n}.png';Image.new('RGB',(856,540),color).save(p);paths.append(p)
    slot=PlaceholderItem(key='id_legal',title='法定代表人身份证',attachToSection='法人身份证明')
    append_section_materials(doc,[slot],{'id_legal':paths},['id_legal'])
    pictures=[p for p in doc.paragraphs if list(p._p.iter(qn('w:drawing')))]
    assert len(pictures)==1
    assert len(list(pictures[0]._p.iter(qn('w:drawing'))))==2
    heights=[int(x.get('cy')) for x in pictures[0]._p.iter(qn('wp:extent'))]
    assert len(set(heights))==1 and max(heights)<=5.4*360000
    assert not list(pictures[0]._p.iter(qn('w:br')))
    assert stop.paragraph_format.page_break_before is None


def test_bond_is_required_even_if_catalog_omitted_it():
    for empty in (True,False):
        doc=Document();head=doc.add_paragraph('投标保证金',style='Heading 1')
        if not empty:doc.add_paragraph('按招标要求提交。')
        stop=doc.add_paragraph('声明函',style='Heading 1')
        doc.add_paragraph('本单位声明属实。')
        append_section_materials(doc,[],{'bond':[]},[])
        nodes=[];node=head._p.getnext()
        while node is not stop._p:
            nodes.append(node);node=node.getnext()
        text=''.join(t.text or '' for node in nodes for t in node.iter(qn('w:t')))
        assert '投标保证金' in text and '此处粘贴' in text
        assert not any(br.get(qn('w:type'))=='page' for node in nodes for br in node.iter(qn('w:br')))
        assert not any(list(node.iter(qn('w:pageBreakBefore'))) for node in nodes)
        before=doc.element.body.xml
        append_section_materials(doc,[],{'bond':[]},[])
        assert doc.element.body.xml==before


def test_bond_with_real_bank_guarantee_has_no_missing_box(tmp_path):
    doc=Document();doc.add_paragraph('投标保证金',style='Heading 1')
    doc.add_paragraph('附：银行保函扫描件')
    p=tmp_path/'proof.png';Image.new('RGB',(600,800),'white').save(p)
    slot=PlaceholderItem(key='bank_guarantee',title='银行保函')
    append_section_materials(doc,[slot],{'bank_guarantee':[p],'bond':[]},['bank_guarantee'])
    assert list(doc.element.body.iter(qn('w:drawing')))
    assert not any('此处粘贴' in para.text for para in doc.paragraphs)


@pytest.mark.parametrize('lines,expected',[(5,0),(28,1)])
def test_placeholder_page_break_only_when_current_page_is_full(lines,expected):
    from api.services.tenders.section_materials import insert_material
    doc=Document()
    for _ in range(lines):
        anchor=doc.add_paragraph('本章原始内容')
    stop=doc.add_paragraph('下一章',style='Heading 1')
    insert_material(doc,anchor._p,PlaceholderItem(key='missing',title='证明材料'),[])
    node=anchor._p.getnext();breaks=0
    while node is not stop._p:
        breaks+=sum(br.get(qn('w:type'))=='page' for br in node.iter(qn('w:br')))
        node=node.getnext()
    assert breaks==expected
    assert stop.paragraph_format.page_break_before is None
