"""Independent, manually labelled requirement migration examples."""
from api.services.cablepkg.material_binding import normalize_demands
from api.services.tenders.schema import BidBrief, MaterialDemand, PlaceholderItem

BUNDLE = '投标人自述的企业名称变更原因说明、市场监督管理部门出具的名称变更证明相关证明材料、有权机构出具的资格资质变更证明等相关证明材料'


def demand(title, key='legacy', section='', **kwargs):
    return MaterialDemand(id='d_' + key, title=title, text=title + '（如有）', slotKey=key, section=section, **kwargs)


def outline(title, ident='target'):
    return {'id': ident, 'title': title, 'rawTitle': title, 'kind': 'attachment', 'source': 'invitation'}


def test_field_is_removed_from_old_demands_slots_and_selected_keys():
    b = BidBrief(extraPlaceholders=[PlaceholderItem(key='old',title='投标有效期')],requiredSlotKeys=['old'],includeSlotKeys=['old'])
    assert normalize_demands(b,[demand('投标有效期','old')]) == []
    assert b.extraPlaceholders == []
    assert b.requiredSlotKeys == b.includeSlotKeys == []
    assert normalize_demands(b,[demand('投标有效期承诺书')])[0].title == '投标有效期承诺书'


def test_license_binds_unique_actual_scan_section_instead_of_qualification_clause():
    target='3.企业营业执照（或事业单位法人证书或其他组织登记证书）（扫描件）'
    b=BidBrief(outlineItems=[outline(target)],extraPlaceholders=[PlaceholderItem(key='license',title='营业执照',attachToSection='3.5 资格审查资料')])
    rows=normalize_demands(b,[demand('营业执照和组织机构代码证复印件（三证合一只提供营业执照）','license','3.5 资格审查资料')])
    assert rows[0].section == target
    assert rows[0].mode == 'attachment'
    assert b.extraPlaceholders[0].attachToSection == target


def test_bundle_migrates_stale_technical_aliases_without_losing_official_proofs():
    target='8.' + BUNDLE + '（如有）'
    b=BidBrief(outlineItems=[outline(target)],extraPlaceholders=[PlaceholderItem(key='oldtech',title=BUNDLE,attachToSection='技术文件')],requiredSlotKeys=['oldtech'],includeSlotKeys=['oldtech'])
    rows=normalize_demands(b,[demand(BUNDLE,'semantic','第六章投标文件格式'),demand(BUNDLE,'oldtech','技术文件',origin='explicit')])
    assert [(d.title,d.mode,d.volume,d.condition,d.section) for d in rows] == [
        ('企业名称变更情况说明','statement','business','name_change',target),
        ('市场监督管理部门名称变更证明','attachment','business','name_change',target),
        ('资格资质变更证明','attachment','business','name_change',target)]
    assert all('如有' in d.text and d.reviewRequired for d in rows)
    assert b.extraPlaceholders == []
    assert b.requiredSlotKeys == ['cable_name_change_statement','cable_name_change_registry','cable_name_change_qualification']
    assert normalize_demands(b,rows) == rows


def test_ambiguous_license_sections_are_not_guessed():
    b=BidBrief(outlineItems=[outline('包1营业执照（扫描件）','a'),outline('包2营业执照（扫描件）','b')])
    row=normalize_demands(b,[demand('营业执照','license','原所属章节')])[0]
    assert row.section == '原所属章节'


def test_unrelated_materials_and_attachments_remain_unchanged():
    b=BidBrief(extraPlaceholders=[PlaceholderItem(key='proof',title='产品检测报告',attachToSection='技术文件')],requiredSlotKeys=['proof'])
    original=demand('产品检测报告','proof','技术文件',volume='technical')
    assert normalize_demands(b,[original]) == [original]
    assert b.requiredSlotKeys == ['proof']
    assert b.extraPlaceholders[0].attachToSection == '技术文件'


def test_normalization_reaches_existing_entrypoint_and_stays_idempotent():
    from api.services.tenders.material_intents import apply_demand_slots, _with_explicit_demands, _catalog
    target='8.'+BUNDLE+'（如有）'
    b=BidBrief(outlineItems=[outline(target)],materialDemands=[demand(BUNDLE,'new','第六章投标文件格式')],extraPlaceholders=[PlaceholderItem(key='old',title=BUNDLE,attachToSection='技术文件'),PlaceholderItem(key='field',title='投标有效期')],requiredSlotKeys=['new','old','field'],includeSlotKeys=['new','old','field'])
    apply_demand_slots(b,_with_explicit_demands(b,list(b.materialDemands),_catalog(None)))
    assert {s.key for s in b.extraPlaceholders} == {'cable_name_change_statement','cable_name_change_registry','cable_name_change_qualification'}
    assert all(d.volume=='business' and d.section==target for d in b.materialDemands)
    assert len(b.materialDemands)==3
    before=b.model_dump()
    apply_demand_slots(b,_with_explicit_demands(b,list(b.materialDemands),_catalog(None)))
    assert b.model_dump()==before


def test_local_format_is_not_inserted_as_an_attachment_and_license_uses_paste_site(tmp_path):
    from docx import Document
    from docx.oxml.ns import qn
    from PIL import Image
    from api.services.tenders.section_materials import append_section_materials
    doc=Document()
    doc.add_heading('商务偏差表',1)
    table=doc.add_table(rows=2,cols=5)
    for cell,value in zip(table.rows[0].cells,['序号','招标文件条目号','招标文件条款','投标文件条款','偏差说明']):cell.text=value
    license_heading='3.企业营业执照（扫描件）'
    doc.add_heading(license_heading,1)
    doc.add_paragraph('（此处粘帖投标人营业执照副本扫描件）')
    stop=doc.add_heading('下一章节',1)
    image=tmp_path/'license.png';Image.new('RGB',(300,200),'blue').save(image)
    slots=[PlaceholderItem(key='deviation',title='商务偏差表',attachToSection='商务偏差表'),PlaceholderItem(key='license',title='营业执照',attachToSection=license_heading)]
    before=table._tbl.xml
    notes,handled=append_section_materials(doc,slots,{'license':[image]},[s.key for s in slots])
    assert 'deviation' not in handled
    assert 'license' in handled
    assert table._tbl.xml==before
    assert not any('所需材料：商务偏差表' in p.text for p in doc.paragraphs)
    images=[n for n in doc.element.body if list(n.iter(qn('w:drawing')))]
    assert len(images)==1
    nodes=list(doc.element.body)
    assert nodes.index(images[0])<nodes.index(stop._p)


def test_name_change_word_reuse_keeps_distinct_authority_proof_frames(tmp_path):
    import hashlib
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from api.services.cablepkg.word_materials import apply,prune_placeholder_boxes
    from api.services.tenders.native_format import _import_form_xml
    title='8.'+BUNDLE+'（如有）'
    doc=Document();doc.add_heading(title,1)
    for key,label in [('cable_name_change_statement','企业名称变更情况说明'),('cable_name_change_registry','市场监督管理部门名称变更证明'),('cable_name_change_qualification','资格资质变更证明')]:
        p=doc.add_paragraph('对应 '+label+'；所需材料：'+label)
        p._p.get_or_add_pPr().append(OxmlElement('w:pBdr'))
        mark=OxmlElement('w:bookmarkStart');mark.set(qn('w:name'),'result_'+hashlib.sha256(key.encode()).hexdigest()[:20]+'_box');mark.set(qn('w:id'),str(len(doc.paragraphs)))
        p._p.append(mark)
    source=Document();source.add_heading('企业名称变更情况说明',1);source.add_paragraph('企业是否发生名称变更须人工确认。')
    path=tmp_path/'statement.docx';source.save(path)
    binding=dict(id='binding',sourceDocumentId='source',sourceHash=hashlib.sha256(path.read_bytes()).hexdigest(),sourceItemId='item',sourceTitle='企业名称变更情况说明',targetId='target',targetTitle=title,mode='chapter')
    outcomes=apply(doc,[binding],{'source':path},import_xml=_import_form_xml,section_titles=[title])
    assert outcomes[0]['status']=='copied'
    prune_placeholder_boxes(doc,outcomes,[binding])
    frames=[p.text for p in doc.paragraphs if '所需材料' in p.text]
    assert frames==['对应 市场监督管理部门名称变更证明；所需材料：市场监督管理部门名称变更证明','对应 资格资质变更证明；所需材料：资格资质变更证明']


def test_company_label_preserves_signature_boundary_and_rejects_foreign_owner(monkeypatch):
    from types import SimpleNamespace
    from api.services.knowledge import ingest
    from api.services.tenders.company_materials import document_matches_company
    b=BidBrief(bidderName='人民电缆集团有限公司')
    raw='企业法人营业执照\n名称人民电缆集团有限公司类型有限责任公司\n投标人：人民电缆集团有限公司（盖单位章）\n法定代表人或委托代理人：（签字）'
    monkeypatch.setattr(ingest,'read_extracted_text',lambda *_:raw)
    doc=SimpleNamespace(public_id='license',summary='',tags=['slot:license'])
    assert document_matches_company(doc,b,key='license')
    raw='企业法人营业执照\n名称其他企业有限公司类型有限责任公司\n投标人：其他企业有限公司（盖单位章）\n法定代表人：（签字）'
    assert not document_matches_company(doc,b,key='license')


def test_split_does_not_invent_authority_proof_absent_from_source():
    title='企业名称变更原因说明及名称变更证明'
    b=BidBrief()
    rows=normalize_demands(b,[demand(title)])
    assert [d.title for d in rows]==['企业名称变更情况说明','市场监督管理部门名称变更证明']
    assert not any('资质' in d.title for d in rows)
