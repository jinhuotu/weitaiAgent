from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from PIL import Image
from api.services.tenders.material_layout import constrain_blocks,printable_twips
from api.services.tenders.section_materials import insert_material
from api.services.tenders.schema import PlaceholderItem


def test_media_measures_target_page_without_following_chapters(monkeypatch, tmp_path):
    from api.services.tenders import placeholders
    doc=Document();anchor=doc.add_paragraph('本章附件位')
    tail=doc.add_paragraph('下一章函件')
    seen=[]
    image=tmp_path/'scan.png';Image.new('RGB',(20,20),'white').save(image)
    def insert(doc,files,**options):
        seen.append([p.text for p in doc.paragraphs])
        doc.add_paragraph('新附件').add_run().add_picture(str(image))
        return 1
    monkeypatch.setattr(placeholders,'_insert_slot_media',insert)
    insert_material(doc,anchor._p,PlaceholderItem(key='a',title='证明'),['fixture.png'])
    assert seen==[['本章附件位']]
    assert [p.text for p in doc.paragraphs]==['本章附件位','新附件','下一章函件']
    assert tail._p.getparent() is doc.element.body


def test_oversized_picture_fits_printable_area_and_retains_ratio(tmp_path):
    doc=Document();anchor=doc.add_paragraph('附件')
    path=tmp_path/'portrait.png';Image.new('RGB',(100,1000),'white').save(path)
    pic=doc.add_paragraph();pic.add_run().add_picture(str(path),width=doc.sections[0].page_width)
    extent=next(pic._p.iter(qn('wp:extent')))
    before=int(extent.get('cx'))/int(extent.get('cy'))
    constrain_blocks(doc,anchor._p,[pic._p])
    width,height=printable_twips(doc,anchor._p)
    assert int(extent.get('cx'))<=width*635 and int(extent.get('cy'))<=height*635
    assert abs(int(extent.get('cx'))/int(extent.get('cy'))-before)<0.00001


def test_long_tables_allow_paging_and_repeat_headers():
    doc=Document();anchor=doc.add_paragraph('附件表')
    table=doc.add_table(rows=30,cols=2)
    table.cell(0,0).text='编号';table.cell(0,1).text='名称'
    for row in table.rows:
        row._tr.get_or_add_trPr().append(OxmlElement('w:cantSplit'))
    constrain_blocks(doc,anchor._p,[table._tbl])
    assert not list(table._tbl.iter(qn('w:cantSplit')))
    assert table.rows[0]._tr.find('w:trPr/w:tblHeader',table._tbl.nsmap) is not None
