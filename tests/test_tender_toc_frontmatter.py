"""TOC relocation preserves front matter instead of swallowing tables/materials."""
from docx import Document
from docx.oxml.ns import qn
from api.services.tenders.document import _bookmark_paragraph, _write_toc_line
from api.services.tenders.native_format import _clean_toc_gap, validate_native_toc


def test_front_matter_table_and_material_stay_outside_contiguous_toc():
    doc=Document(); title=doc.add_paragraph('目录')._p
    row=doc.add_paragraph();_write_toc_line(row,0,'投标函','toc_letter','1')
    table=doc.add_table(rows=1,cols=1);table.cell(0,0).text='文件提交方式说明'
    material=doc.add_paragraph('对应证明材料；所需材料：企业资质')._p
    heading=doc.add_paragraph('投标函',style='Heading 1');_bookmark_paragraph(heading,'toc_letter')
    source=[(table._tbl,table._tbl.xml),(material,material.xml)]
    _clean_toc_gap(title,heading._p,[row._p])
    assert validate_native_toc(doc)['entries']==1
    nodes=list(doc.element.body)
    assert nodes.index(table._tbl)<nodes.index(material)<nodes.index(title)<nodes.index(row._p)<nodes.index(heading._p)
    assert all(node.xml==xml for node,xml in source)
    before=doc.element.xml
    _clean_toc_gap(title,heading._p,[row._p])
    assert doc.element.xml==before
