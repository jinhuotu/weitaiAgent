from docx import Document
from docx.oxml.ns import qn
from PIL import Image
from api.services.tenders.schema import PlaceholderItem
from api.services.tenders.section_materials import append_section_materials, section_type


def test_a_has_no_extra_blocks():
    doc=Document();head=doc.add_paragraph('合同响应书',style='Heading 1')
    p=doc.add_paragraph('本单位承诺遵守约定。名称：________')
    before=doc.element.body.xml
    assert section_type(head._p,[p._p]) == 'A'
    append_section_materials(doc,[PlaceholderItem(key='a',title='合同')],{},['a'])
    assert before == doc.element.body.xml


def test_b_pastes_real_file_only_at_attachment_site(tmp_path):
    for title in ['法人身份证明','授权委托书','投标保证金','已标价清单']:
        doc=Document();head=doc.add_paragraph(title,style='Heading 1')
        p=doc.add_paragraph('附：原始证明扫描件')
        stop=doc.add_paragraph('下一章',style='Heading 1')
        original=[(el,el.xml) for el in doc.element.body]
        image=tmp_path/'scan.png';Image.new('RGB',(40,40),'white').save(image)
        slot=PlaceholderItem(key='proof',title='原始证明')
        assert section_type(head._p,[p._p]) == 'B'
        append_section_materials(doc,[slot],{'proof':[image]},['proof'])
        cursor=p._p.getnext();found=False
        while cursor is not stop._p:
            found |= bool(list(cursor.iter(qn('w:drawing'))));cursor=cursor.getnext()
        assert found
        assert all(el.xml==xml for el,xml in original if el is not stop._p)
        before=doc.element.body.xml
        append_section_materials(doc,[slot],{'proof':[image]},['proof'])
        assert doc.element.body.xml==before


def test_b_missing_file_preserves_original_without_box():
    doc=Document();doc.add_paragraph('授权委托书',style='Heading 1')
    doc.add_paragraph('附：原始证明扫描件')
    before=doc.element.body.xml
    append_section_materials(doc,[PlaceholderItem(key='proof',title='原始证明')],{'proof':[]},['proof'])
    assert doc.element.body.xml==before


def test_c_items_are_filled_in_place_not_at_tail(tmp_path):
    doc=Document();doc.add_paragraph('资格材料',style='Heading 1')
    first=doc.add_paragraph('A.登记材料');second=doc.add_paragraph('B.信用材料')
    remark=doc.add_paragraph('备注原文');stop=doc.add_paragraph('下一章',style='Heading 1')
    image=tmp_path/'scan.png';Image.new('RGB',(40,40),'white').save(image)
    slots=[PlaceholderItem(key='a',title='登记材料'),PlaceholderItem(key='b',title='信用材料')]
    append_section_materials(doc,slots,{'a':[image],'b':[]},['a','b'])
    assert first._p.getnext() is second._p
    assert second._p.getnext() is remark._p
    node=remark._p.getnext();image_found=False;box=''
    while node is not stop._p:
        image_found |= bool(list(node.iter(qn('w:drawing'))))
        box+=''.join(t.text or '' for t in node.iter(qn('w:t')))
        node=node.getnext()
    assert image_found
    assert slots[1].title in box


def test_c_table_material_stays_in_its_original_cell():
    doc=Document();doc.add_paragraph('资格材料',style='Heading 1')
    table=doc.add_table(rows=1,cols=2)
    table.cell(0,0).text='登记材料';table.cell(0,1).text='信用材料'
    stop=doc.add_paragraph('下一章',style='Heading 1')
    slots=[PlaceholderItem(key='a',title='登记材料'),PlaceholderItem(key='b',title='信用材料')]
    append_section_materials(doc,slots,{'a':[],'b':[]},['a','b'])
    assert table.cell(0,0).text == '登记材料'
    assert table.cell(0,1).text == '信用材料'
    assert table._tbl.getnext() is not stop._p


def test_native_supplement_never_creates_lettered_orphan(monkeypatch):
    from api.services.tenders import native_format
    from api.services.tenders.schema import BidBrief,OutlineItem
    doc=Document();doc.add_paragraph('资格材料',style='Heading 1')
    doc.add_paragraph('C.工作证明：按要求提供材料。')
    item=OutlineItem(id='c',title='C.工作证明',rawTitle='C.工作证明',level=1,kind='scan')
    monkeypatch.setattr(native_format,'selected_items',lambda *a:[item])
    before=doc.element.body.xml
    native_format.supplement_sections(doc,BidBrief(),'business')
    assert doc.element.body.xml==before


def test_composition_list_in_pure_letter_never_creates_materials(tmp_path):
    doc=Document();doc.add_paragraph('投标函',style='Heading 1')
    doc.add_paragraph('我方投标文件包括下列内容：')
    doc.add_paragraph('(1) 营业执照扫描件；(2) 信用报告；(3) 投标保证金。')
    doc.add_paragraph('投标人：________')
    before=doc.element.body.xml
    append_section_materials(doc,[PlaceholderItem(key='credit',title='信用报告')],{'credit':[]},['credit'])
    assert doc.element.body.xml==before


def test_two_credit_sources_have_real_images_in_their_own_sections(tmp_path):
    doc=Document();doc.add_paragraph('国家企业信用信息公示系统网站查询',style='Heading 1')
    first=doc.add_paragraph('');doc.add_paragraph('')
    second_head=doc.add_paragraph('信用中国查询',style='Heading 1')
    doc.add_paragraph('');second=doc.add_paragraph('')
    end=doc.add_paragraph('下一章',style='Heading 1')
    paths=[]
    for index,color in enumerate(['red','blue','green']):
        path=tmp_path/f'opaque-{index}.png';Image.new('RGB',(40,40),color).save(path);paths.append(path)
    slots=[PlaceholderItem(key='registry',title='企业信息公示页及股东信息'),
           PlaceholderItem(key='china',title='信用报告'),PlaceholderItem(key='tax',title='重大税收违法查询')]
    media={'registry':[paths[0]],'china':[paths[1]],'tax':[paths[2]]}
    append_section_materials(doc,slots,media,list(media))
    node=first._p;count=0
    while node is not second_head._p:
        count+=len(list(node.iter(qn('w:drawing'))));node=node.getnext()
    assert count==1
    node=second._p;count=0
    while node is not end._p:
        count+=len(list(node.iter(qn('w:drawing'))));node=node.getnext()
    assert count==2
    before=doc.element.body.xml
    append_section_materials(doc,slots,media,list(media))
    assert doc.element.body.xml==before


def test_license_number_capital_and_qualification_fill_original_empty_cells():
    from api.services.tenders.pdf_format import _fill_empty_label_cells
    from api.services.tenders.schema import BidBrief
    doc=Document();table=doc.add_table(rows=3,cols=2)
    for row,label in zip(table.rows,['营业执照号','注册资金','资质等级']):
        row.cells[0].text=label
    facts=[{'role':'company','fields':[{'name':'统一社会信用代码','value':'91330000TEST1234567'},
            {'name':'注册资本','value':'100万元'},{'name':'资质等级','value':'一级'}]}]
    _fill_empty_label_cells(table._tbl,BidBrief(),facts)
    assert [row.cells[1].text for row in table.rows]==['91330000TEST1234567','100万元','一级']
