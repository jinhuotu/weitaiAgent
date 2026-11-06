import hashlib
from docx import Document
from docx.oxml.ns import qn
from docx.shared import Cm
from PIL import Image

from api.services.cablepkg.word_materials import apply, fill_tables
from api.services.tenders.native_format import _import_form_xml


def test_copies_paragraph_table_image_and_keeps_following_chapter(tmp_path):
    source = Document()
    source.add_heading("技术服务方案", 1)
    source.add_paragraph("说明：仅供编辑")
    source.add_heading("服务组织", 2)
    source.add_paragraph("专职人员负责安装指导。")
    source.add_table(rows=1, cols=2).cell(0, 0).text = "服务人员"
    image = tmp_path / "logo.png"
    Image.new("RGB", (100, 50), "blue").save(image)
    source.add_picture(str(image), width=Cm(2))
    path = tmp_path / "source.docx"
    source.save(path)
    target = Document()
    target.add_heading("7.技术服务，包括培训、安装指导等", 1)
    target.add_paragraph("对应服务方案；所需材料：服务方案")
    target.add_heading("8.售后服务保障及承诺", 1)
    target.add_paragraph("后章原文")
    binding = dict(id="binding", sourceDocumentId="doc", sourceHash=hashlib.sha256(path.read_bytes()).hexdigest(),
                   targetId="service", targetTitle="7.技术服务，包括培训、安装指导等", mode="chapter")
    result = apply(target, [binding], {"doc": path}, import_xml=_import_form_xml,
                   section_titles=[binding["targetTitle"], "8.售后服务保障及承诺"])
    assert result[0]["status"] == "copied"
    before_xml = target.element.xml
    repeated = apply(target, [binding], {"doc": path}, import_xml=_import_form_xml,
                     section_titles=[binding["targetTitle"], "8.售后服务保障及承诺"])
    assert repeated[0]["alreadyApplied"] is True
    assert target.element.xml == before_xml
    assert [p.text for p in target.paragraphs if p.text] == [
        "7.技术服务，包括培训、安装指导等", "服务组织", "专职人员负责安装指导。", "8.售后服务保障及承诺", "后章原文"]
    assert target.tables[0].cell(0, 0).text == "服务人员"
    assert len(target.inline_shapes) == 1
    target.save(tmp_path / "result.docx")
    assert len(Document(tmp_path / "result.docx").inline_shapes) == 1


def test_table_fill_uses_field_and_year_and_preserves_manual_values():
    source = Document()
    table = source.add_table(rows=3, cols=2)
    for row, values in zip(table.rows, [("项目", "填写内容"), ("投标人名称", "测试电缆有限公司"), ("注册地址", "〔待确认地址〕")]):
        for cell, value in zip(row.cells, values): cell.text = value
    target = Document()
    table = target.add_table(rows=2, cols=4)
    table.cell(0, 0).text = "投标人名称"
    table.cell(0, 1).merge(table.cell(0, 3))
    table.cell(1, 0).text = "注册地址"
    table.cell(1, 1).text = "人工确认的地址"
    result, pending = fill_tables(source, [table])
    assert table.cell(0, 1).text == "测试电缆有限公司"
    assert table.cell(1, 1).text == "人工确认的地址"
    assert [r["field"] for r in result] == ["投标人名称"]
    assert pending == ["注册地址（现值与资料候选值不一致）"]


def test_changed_version_cannot_overwrite_target(tmp_path):
    source = Document(); source.add_paragraph("资料")
    path = tmp_path / "source.docx"; source.save(path)
    target = Document(); target.add_heading("服务方案", 1); target.add_paragraph("保留原文")
    result = apply(target, [dict(id="x", sourceDocumentId="s", sourceHash="old", targetId="t", targetTitle="服务方案", mode="chapter")],
                   {"s": path}, import_xml=_import_form_xml, section_titles=["服务方案"])
    assert result[0]["status"] == "needs_review"
    assert target.paragraphs[1].text == "保留原文"


def test_stable_binding_handles_source_title_alias_and_version_changes():
    from api.services.cablepkg.word_materials import bind
    outline = [{"id": "section7", "title": "7.技术服务，包括培训、安装指导等"}]
    entry = dict(documentId="doc19", itemId="item19", title="技术服务方案（培训、安装指导）", hash="a" * 64, eligible=True)
    first, notes = bind(outline, [entry])
    assert notes == []
    assert first[0]["sourceDocumentId"] == "doc19"
    assert first[0]["targetId"] == "section7"
    assert first[0]["sourceHash"] == "a" * 64
    assert bind(outline, [entry], first)[0] == first
    changed, notes = bind(outline, [{**entry, "hash": "b" * 64}], first)
    assert changed == []
    assert notes[0]["status"] == "needs_review"


def test_binding_does_not_choose_between_two_versions_or_treat_report_list_as_report():
    from api.services.cablepkg.word_materials import bind
    entries = [dict(documentId=str(i), itemId="p", title="售后服务保障及承诺", hash="a" * 64, eligible=True) for i in range(2)]
    bindings, review = bind([{"id": "after", "title": "8.售后服务保障及承诺"}], entries)
    assert bindings == []
    assert review[0]["status"] == "needs_review"
    bindings, _ = bind([{"id": "report", "title": "检测检验报告"}], [dict(documentId="list", itemId="p", title="检测检验报告清单", hash="a" * 64, eligible=True)])
    assert bindings == []


def test_fields_and_body_subheadings_are_not_attachment_demands():
    from api.services.tenders.material_intents import apply_demand_slots
    from api.services.tenders.schema import BidBrief, MaterialDemand, PlaceholderItem
    from api.services.tenders.section_materials import material_name
    from api.services.tenders.outline import classify_kind
    titles = ["项目名称", "一、项目理解", "二、技术路线", "四、功能参数响应解决办法"]
    brief = BidBrief(extraPlaceholders=[PlaceholderItem(key=str(i), title=t) for i,t in enumerate(titles)],
                     requiredSlotKeys=[str(i) for i in range(4)], includeSlotKeys=[str(i) for i in range(4)])
    demands = [MaterialDemand(id=str(i), title=t, text=t, slotKey=str(i), origin="explicit") for i,t in enumerate(titles)]
    apply_demand_slots(brief, demands)
    assert brief.materialDemands == []
    assert brief.extraPlaceholders == []
    assert brief.requiredSlotKeys == brief.includeSlotKeys == []
    assert all(material_name(t) == "" for t in titles)
    assert material_name("产品制造质量控制支撑证明材料")
    assert classify_kind("2.投标人基本情况表") == "company"
    assert classify_kind("7.技术服务，包括培训、安装指导等") == "tech_chapter"


def test_template_project_scope_is_required_without_treating_text_as_evidence():
    from types import SimpleNamespace
    from api.services.tenders.company_materials import document_matches_company
    from api.services.tenders.schema import BidBrief
    template = SimpleNamespace(tags=["material-role:template", "template-bidder:测试电缆有限公司", "template-tender:SL2639"], summary="", public_id="template")
    assert document_matches_company(template, BidBrief(bidderName="测试电缆有限公司", tenderNo="SL2639"))
    assert not document_matches_company(template, BidBrief(bidderName="另一家公司", tenderNo="SL2639"))
    assert not document_matches_company(template, BidBrief(bidderName="测试电缆有限公司", tenderNo="OTHER"))


def test_financial_empty_percentage_is_not_a_numeric_value():
    source = Document(); table = source.add_table(rows=2, cols=3)
    for row, values in zip(table.rows, [("类别", "事项", "〔2023〕年"), ("风险", "资产负债率", "〔 〕%")]):
        for cell, value in zip(row.cells, values): cell.text = value
    target = Document(); table = target.add_table(rows=2, cols=3)
    for row, values in zip(table.rows, [("类别", "事项", "2023年"), ("风险", "资产负债率", "")]):
        for cell, value in zip(row.cells, values): cell.text = value
    fields, pending = fill_tables(source, [table])
    assert fields == []
    assert table.cell(1, 2).text == ""


def test_generic_commitment_needs_authoritative_context():
    from api.services.cablepkg.word_materials import bind
    entry = dict(documentId="d", itemId="p", title="型式试验报告承诺函", hash="a" * 64, eligible=True)
    assert bind([dict(id="c", title="11.承诺函")], [entry])[0] == []
    bindings, _ = bind([dict(id="c", title="11.承诺函", context="供货前提供与所供产品型号一致的有效型式试验报告承诺函")], [entry])
    assert bindings[0]["sourceDocumentId"] == "d"


def test_financial_values_match_year_and_do_not_fill_example_column():
    source = Document(); table = source.add_table(rows=2, cols=3)
    for row, values in zip(table.rows, [("类别", "事项", "2023年"), ("风险", "资产负债率", "42.5%")]):
        for cell, value in zip(row.cells, values): cell.text = value
    target = Document(); table = target.add_table(rows=2, cols=5)
    for row, values in zip(table.rows, [("类别", "事项", "2021年", "2023年", "2023年（示例）"), ("风险", "资产负债率", "", "", "50%")]):
        for cell, value in zip(row.cells, values): cell.text = value
    fields, _ = fill_tables(source, [table])
    assert table.cell(1, 2).text == ""
    assert table.cell(1, 3).text == "42.5%"
    assert table.cell(1, 4).text == "50%"
    assert fields[0]["year"] == "2023年"


def test_explicit_mapping_validates_all_coordinates_before_writing():
    import pytest
    source = Document(); source.add_table(rows=1, cols=1).cell(0, 0).text = "值"
    target = Document(); table = target.add_table(rows=1, cols=2)
    mappings = [dict(sourceTable=0, sourceRow=0, sourceColumn=0, targetTable=0, targetRow=0, targetColumn=0),
                dict(sourceTable=0, sourceRow=100, sourceColumn=0, targetTable=0, targetRow=0, targetColumn=1)]
    with pytest.raises(IndexError):
        fill_tables(source, [table], mappings)
    assert table.cell(0, 0).text == ""


def test_legacy_word_reuses_shared_conversion_without_modifying_original(tmp_path, monkeypatch):
    from api.services import word_convert
    source = tmp_path / "old.doc"; source.write_bytes(b"legacy-original")
    def convert(original, destination):
        document = Document(); document.add_heading("服务方案", 1); document.add_paragraph("旧版内容")
        document.save(destination)
    monkeypatch.setattr(word_convert, "convert_doc_to_docx", convert)
    target = Document(); target.add_heading("服务方案", 1)
    binding = dict(id="legacy", sourceDocumentId="old", sourceHash=hashlib.sha256(source.read_bytes()).hexdigest(),
                   targetId="service", targetTitle="服务方案", mode="chapter")
    result = apply(target, [binding], {"old": source}, import_xml=_import_form_xml, section_titles=["服务方案"])
    assert result[0]["status"] == "copied"
    assert target.paragraphs[1].text == "旧版内容"
    assert source.read_bytes() == b"legacy-original"


import pytest

@pytest.mark.asyncio
async def test_retries_retain_stale_binding_until_explicit_version_confirmation(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from api.services.tenders import library_kb, records, pdf_format
    from api.services.tenders.schema import BidBrief, WordMaterialBinding, OutlineItem
    source = tmp_path / "source.docx"
    document = Document(); document.add_paragraph("新版内容"); document.save(source)
    parent = SimpleNamespace(public_id="p", parent_id=None, name="技术服务方案", tags=["slot:custom"])
    child = SimpleNamespace(public_id="d", parent_id="p", summary="", content_hash=hashlib.sha256(source.read_bytes()).hexdigest(),
        tags=["material-role:template", "template-bidder:测试电缆有限公司", "template-tender:SL2639"])
    monkeypatch.setattr(library_kb, "get_base_by_public_id", AsyncMock(return_value=SimpleNamespace(id=1)))
    monkeypatch.setattr(library_kb, "_list_docs", AsyncMock(return_value=[parent,child]))
    monkeypatch.setattr(library_kb, "_doc_file_path", lambda doc: source if doc is child else None)
    monkeypatch.setattr(pdf_format, "_word_format_file", lambda _: None)
    saved = WordMaterialBinding(id="saved", targetId="t", targetTitle="技术服务方案", sourceItemId="p", sourceDocumentId="d", sourceHash="a" * 64)
    brief = BidBrief(bidderName="测试电缆有限公司", tenderNo="SL2639", libraryBindings=[saved],
                     outlineItems=[OutlineItem(id="t", title="技术服务方案", kind="tech_chapter")])
    first, _ = await records._word_library_inputs(None, brief)
    assert first == []
    assert brief.libraryBindings[0].sourceHash == "a" * 64
    second, _ = await records._word_library_inputs(None, brief)
    assert second == []
    assert brief.libraryBindingReview[0]["status"] == "needs_review"


def test_chapter_replacement_retains_already_matched_original_scan(tmp_path):
    from docx.oxml import OxmlElement
    source = Document(); source.add_heading("授权委托书", 1); source.add_paragraph("授权正文")
    path = tmp_path / "authorization.docx"; source.save(path)
    image = tmp_path / "id.png"; Image.new("RGB", (200, 100), "red").save(image)
    target = Document(); target.add_heading("授权委托书", 1)
    paragraph = target.add_paragraph(); paragraph.add_run().add_picture(str(image), width=Cm(4))
    marker = OxmlElement('w:bookmarkStart'); marker.set(qn('w:name'), 'result_identity_img'); marker.set(qn('w:id'), '1')
    paragraph._p.append(marker)
    end = OxmlElement('w:bookmarkEnd'); end.set(qn('w:id'), '1'); paragraph._p.append(end)
    binding = dict(id="auth", sourceDocumentId="d", sourceHash=hashlib.sha256(path.read_bytes()).hexdigest(), targetId="a", targetTitle="授权委托书", mode="chapter")
    result = apply(target, [binding], {"d":path}, import_xml=_import_form_xml, section_titles=["授权委托书"])
    assert len(target.inline_shapes) == 1
    assert result[0]['retainedScanBlocks'] == 1


def test_template_guarantee_column_cannot_copy_empty_response(tmp_path):
    source = Document(); source.add_heading("参数表", 1)
    table = source.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "参数"; table.cell(0, 1).text = "投标人保证值"
    table.cell(1, 0).text = "电压"; table.cell(1, 1).text = "完全响应"
    path = tmp_path / "parameters.docx"; source.save(path)
    target = Document(); target.add_heading("参数表", 1); target.add_paragraph("原表待填")
    binding = dict(id="parameters", sourceDocumentId="d", sourceHash=hashlib.sha256(path.read_bytes()).hexdigest(), targetId="a", targetTitle="参数表", mode="chapter")
    result = apply(target, [binding], {"d":path}, import_xml=_import_form_xml, section_titles=["参数表"])
    assert result[0]['status'] == 'needs_review'
    assert target.paragraphs[1].text == "原表待填"


def test_match_report_separates_written_word_from_missing_real_report():
    from api.services.cablepkg.word_materials import reconcile_match
    report = dict(matched=[], missingFiles=[dict(title="技术服务方案"), dict(title="产品检测报告")], createdItems=[])
    outcomes = [dict(bindingId="b", targetTitle="技术服务方案", mode="chapter", status="copied")]
    result = reconcile_match(report, outcomes, [dict(id="b", sourceTitle="技术服务方案")])
    assert result["missingFiles"] == [dict(title="产品检测报告")]
    assert result["matched"] == []
    assert result["wordMaterials"] == outcomes


def test_numbering_in_imported_paragraph_style_gets_its_own_definition(tmp_path):
    source = Document(); source.add_heading("服务方案", 1)
    source.add_paragraph("第一项", style="List Number")
    source.add_paragraph("第二项", style="List Number")
    path = tmp_path / "numbered.docx"; source.save(path)
    target = Document(); target.add_heading("服务方案", 1)
    original_num_id = target.styles["List Number"].element.find('.//w:numId', target.element.nsmap).get(qn('w:val'))
    binding = dict(id="numbered", sourceDocumentId="d", sourceHash=hashlib.sha256(path.read_bytes()).hexdigest(), targetId="a", targetTitle="服务方案", mode="chapter")
    result = apply(target, [binding], {"d":path}, import_xml=_import_form_xml, section_titles=["服务方案"])
    assert result[0]['status'] == 'copied'
    first_style = target.paragraphs[1].style
    second_style = target.paragraphs[2].style
    copied_num = first_style.element.find('.//w:numId', first_style.element.nsmap).get(qn('w:val'))
    assert copied_num != original_num_id
    assert second_style.style_id == first_style.style_id
    numbering = target.part.numbering_part.element
    num = numbering.find(f'w:num[@w:numId="{copied_num}"]', numbering.nsmap)
    abstract_id = num.find(qn('w:abstractNumId')).get(qn('w:val'))
    abstract = numbering.find(f'w:abstractNum[@w:abstractNumId="{abstract_id}"]', numbering.nsmap)
    assert abstract.find('.//w:numFmt', abstract.nsmap).get(qn('w:val')) == 'decimal'


def test_original_form_names_match_reference_and_cover_metadata_only():
    from api.services.cablepkg.word_materials import bind
    entries = [dict(documentId="cover", itemId="p", title="参数表与配置表封面填写规则", hash="a" * 64, eligible=True),
               dict(documentId="audit", itemId="a", title="财务信息审核结果凭证单说明", hash="b" * 64, eligible=True)]
    outline = [dict(id="parameters", title="2.技术特性参数表（格式）"),
               dict(id="components", title="3.货物组件材料配置表（格式）"),
               dict(id="audit", title="9.XX年财务信息审核结果凭证单（如有）")]
    result, _ = bind(outline, entries)
    assert [(x['targetId'], x['sourceDocumentId'], x['mode']) for x in result] == [
        ('parameters', 'cover', 'table'), ('components', 'cover', 'table'), ('audit', 'audit', 'reference')]


def test_cover_mapping_does_not_write_tool_instructions_as_values():
    source = Document(); table = source.add_table(rows=3, cols=2)
    for row, values in zip(table.rows, [('字段', '填写内容'), ('项目编号', 'SL2639'), ('分标名称', '〔按投标工具显示的名称填写〕')]):
        for cell, value in zip(row.cells, values): cell.text = value
    target = Document(); table = target.add_table(rows=2, cols=2)
    table.cell(0,0).text = '项目编号'; table.cell(1,0).text = '分标名称'
    fields, _ = fill_tables(source, [table])
    assert table.cell(0,1).text == 'SL2639'
    assert table.cell(1,1).text == ''
    assert [x['field'] for x in fields] == ['项目编号']


def test_duplicate_boxes_are_removed_only_for_completed_body_not_cover_or_proof():
    from docx.oxml import OxmlElement
    from api.services.cablepkg.word_materials import prune_placeholder_boxes
    document = Document()
    for title in ['技术偏差表', '技术特性参数表', '企业名称变更证明']:
        paragraph = document.add_paragraph('对应 ' + title + '；所需材料：' + title)
        paragraph._p.get_or_add_pPr().append(OxmlElement('w:pBdr'))
    bindings = [dict(id='dev', sourceTitle='技术偏差表'),
                dict(id='cover', sourceTitle='参数表与配置表封面填写规则', contentScope='cover'),
                dict(id='statement', sourceTitle='企业名称变更情况说明')]
    outcomes = [dict(bindingId='dev', targetTitle='技术偏差表', mode='chapter', status='copied'),
                dict(bindingId='cover', targetTitle='技术特性参数表', mode='table', status='filled'),
                dict(bindingId='statement', targetTitle='企业名称变更情况说明', mode='chapter', status='copied')]
    assert prune_placeholder_boxes(document, outcomes, bindings) == ['技术偏差表']
    assert '技术特性参数表' in document.paragraphs[0].text
    assert '企业名称变更证明' in document.paragraphs[1].text


def test_numeric_field_qualifier_is_not_treated_as_heading_number():
    source = Document(); table = source.add_table(rows=2, cols=2)
    table.cell(0,0).text='字段'; table.cell(0,1).text='值'
    table.cell(1,0).text='10kV电压'; table.cell(1,1).text='10'
    target = Document(); table=target.add_table(rows=2, cols=2)
    table.cell(0,0).text='35kV电压'; table.cell(1,0).text='10kV电压'
    fields,_=fill_tables(source,[table])
    assert table.cell(0,1).text == ''
    assert table.cell(1,1).text == '10'
    assert [field['field'] for field in fields] == ['10kV电压']


def test_changed_target_identifier_cannot_silently_move_saved_binding():
    from api.services.cablepkg.word_materials import bind
    entry = dict(documentId='d', itemId='p', title='技术服务方案', hash='a'*64, eligible=True)
    saved,_=bind([dict(id='old-section',title='技术服务方案')],[entry])
    current,review=bind([dict(id='new-section',title='技术服务方案')],[entry],saved)
    assert current == []
    assert any('ID 已变化' in row.get('reason','') for row in review)


def test_cover_fields_inside_one_cell_preserve_split_run_labels():
    source = Document(); table = source.add_table(rows=3, cols=2)
    for row, values in zip(table.rows, [('字段', '填写内容'), ('项目编号', 'SL2639'), ('分标名称', '〔按工具显示的名称填写〕')]):
        for cell,value in zip(row.cells,values): cell.text=value
    target=Document(); table=target.add_table(rows=1,cols=1)
    paragraph=table.cell(0,0).paragraphs[0]
    paragraph.add_run('项目').bold=True
    paragraph.add_run('编号：　　分标名称：　　')
    filled,pending=fill_tables(source,[table],cover=True)
    assert paragraph.text == '项目编号：SL2639　分标名称：　　'
    assert paragraph.runs[0].text == '项目'
    assert paragraph.runs[0].bold is True
    assert [field['field'] for field in filled] == ['项目编号']
    assert '分标名称' in pending
