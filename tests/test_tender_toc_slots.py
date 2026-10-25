"""目录始终带页码、格式章正文有页眉、未匹配资料画在对应位置。"""

from __future__ import annotations

from docx import Document
from docx.oxml.ns import qn

from api.services.tenders.document import build_bid_docx
from api.services.tenders.placeholders import place_unmatched_slots, _is_slot_box
from api.services.tenders.schema import BidBrief, BidRequirement, PlaceholderItem


def next_content(el, steps=1):
    """附件间的留白不算正文节点；仍核对内容顺序和重复插入。"""
    for _ in range(steps):
        el = el.getnext()
        while el is not None:
            style = el.find('w:pPr/w:pStyle', el.nsmap)
            if style is None or style.get(qn('w:val')) != 'TenderAttachmentGap':
                break
            assert not ''.join(el.itertext()).strip()
            el = el.getnext()
    return el



def test_toc_pages_even_when_flag_off(tmp_path) -> None:
    brief = BidBrief(
        projectName="分页一致性项目",
        tenderer="测试招标人",
        bidPriceYuan=200000,
        legalPersonName="李四",
        attachQualifications=False,
        includePlaceholders=False,
        includeCommitment=False,
    )
    brief.documentFormat.tocNeedPageNos = False
    path, _warnings = build_bid_docx(brief, tmp_path / "toc.docx", qualification_pdf=None)
    doc = Document(str(path))
    assert "PAGEREF" in doc.element.xml
    seen = False
    for i, section in enumerate(doc.sections):
        assert section.header.is_linked_to_previous is False
        header = section.header.paragraphs[0]
        if i == 0:
            first = section.first_page_header.paragraphs[0]
            assert "分页一致性项目" not in (first.text or "")
            continue
        assert "分页一致性项目" in (header.text or "")
        assert "w:pBdr" in header._p.xml
        seen = True
    assert seen


def test_format_copy_has_body_header(tmp_path, monkeypatch) -> None:
    from api.services.tenders.pdf_format import assemble_from_word_format

    invite = "abc123abc130"
    src = tmp_path / f"{invite}.format.docx"
    raw = Document()
    raw.add_paragraph("投标函")
    raw.add_paragraph("我方已仔细研究了招标文件的全部内容。")
    raw.save(src)
    src.with_name(src.name + ".source").write_text("docx-raw", encoding="utf-8")
    monkeypatch.setattr(
        "api.services.tenders.pdf_format._word_format_file",
        lambda invitation_id: src if invitation_id == invite else None,
    )
    brief = BidBrief(
        projectName="厂内充电桩采购项目",
        layoutMode="outline",
        invitationId=invite,
        includePlaceholders=False,
        includeCommitment=False,
        attachQualifications=False,
    )
    dest = tmp_path / "out.docx"
    assemble_from_word_format(brief, dest, volume="business")
    doc = Document(str(dest))
    header = doc.sections[0].header
    assert header.is_linked_to_previous is False
    assert "厂内充电桩采购项目" in (header.paragraphs[0].text or "")
    assert "w:pBdr" in header.paragraphs[0]._p.xml
    first = doc.sections[0].first_page_header.paragraphs[0]
    assert "厂内充电桩采购项目" not in (first.text or "")


def test_bond_sticks_to_its_chapter_not_the_letter_list() -> None:
    doc = Document()
    doc.add_paragraph("目录")
    doc.add_paragraph("四、投标保证金")
    doc.add_paragraph("一、投标函")
    doc.add_paragraph("（4）投标保证金（如有）")
    doc.add_paragraph("我方已仔细研究了招标文件的全部内容。")
    doc.add_paragraph("二、法定代表人身份证明")
    chapter = doc.add_paragraph("四、投标保证金")
    doc.add_paragraph("在此提供汇款凭证复印件。")
    doc.add_paragraph("五、资格审查资料")
    place_unmatched_slots(
        doc,
        slots=[
            PlaceholderItem(
                key="bond",
                title="投标保证金缴存回单",
                attachToSection="四、投标保证金",
            )
        ],
        media={},
        required_keys=["bond"],
        volume="business",
    )
    mention = next(p for p in doc.paragraphs if "如有" in (p.text or ""))
    assert mention._p.getnext().tag != qn("w:tbl")
    texts: list[str] = []
    el = chapter._element.getnext()
    while el is not None:
        text = "".join(t.text or "" for t in el.iter(qn("w:t")))
        if "资格审查" in text:
            break
        texts.append(text)
        el = el.getnext()
    assert any("投标保证金" in t and "粘贴" in t for t in texts)
    assert "附件：资料库扫描件" not in "\n".join(p.text or "" for p in doc.paragraphs)


def test_unmatched_slot_lands_on_letter_not_other_file(tmp_path) -> None:
    doc = Document()
    doc.add_paragraph("营业执照")
    doc.add_paragraph("后文保持不动")
    other = tmp_path / "other.pdf"
    other.write_bytes(b"%PDF-1.1")
    notes = place_unmatched_slots(
        doc,
        slots=[
            PlaceholderItem(key="license", title="营业执照"),
            PlaceholderItem(key="iso", title="资质证书"),
        ],
        media={"iso": [other]},
        required_keys=["license"],
        volume="business",
    )
    blob = "\n".join(p.text or "" for p in doc.paragraphs)
    blob += "\n" + "\n".join(
        "".join(t.text or "" for t in tbl.iter(qn("w:t"))) for tbl in doc.element.body.iter(qn("w:tbl"))
    )
    assert "附件：资料库扫描件" not in blob
    assert "此处粘贴营业执照" in blob
    assert "other" not in blob
    assert any("营业执照" in n for n in notes)


def test_unmatched_slot_without_anchor_reports_gap() -> None:
    doc = Document()
    doc.add_paragraph("投标函正文，没有对应标题。")
    notes = place_unmatched_slots(
        doc,
        slots=[PlaceholderItem(key="credit", title="信用中国查询页")],
        media={},
        required_keys=["credit"],
        volume="business",
    )
    blob = "\n".join(p.text or "" for p in doc.paragraphs)
    blob += "\n" + "\n".join(
        "".join(t.text or "" for t in tbl.iter(qn("w:t"))) for tbl in doc.element.body.iter(qn("w:tbl"))
    )
    assert "附件：资料库扫描件" not in blob
    assert "此处粘贴" not in blob
    assert any("待指定资料落位" in n and "信用中国查询页" in n for n in notes)


def test_bond_and_bill_stay_on_their_chapters() -> None:
    doc = Document()
    doc.add_paragraph("目录")
    doc.add_paragraph("四、投标保证金")
    doc.add_paragraph("五、已标价工程量清单")
    doc.add_paragraph("九、其他资料")
    doc.add_paragraph("一、投标函")
    doc.add_paragraph("（4）投标保证金（如有）")
    doc.add_paragraph("我方已仔细研究了招标文件的全部内容。")
    bond = doc.add_paragraph("四、投标保证金")
    doc.add_paragraph("在此提供汇款凭证复印件。")
    bill = doc.add_paragraph("五、已标价工程量清单")
    doc.add_paragraph("按清单报价。")
    other = doc.add_paragraph("九、其他资料")
    doc.add_paragraph("除以上资料外补充。")
    place_unmatched_slots(
        doc,
        slots=[
            PlaceholderItem(key="bond", title="投标保证金缴存回单"),
            PlaceholderItem(key="boq", title="已标价工程量清单"),
        ],
        media={},
        required_keys=["bond", "boq"],
        volume="business",
    )

    def between(start, stop_mark: str) -> str:
        bits = []
        el = start._element.getnext()
        while el is not None:
            text = "".join(t.text or "" for t in el.iter(qn("w:t")))
            if stop_mark and stop_mark in text and el.tag == qn("w:p"):
                break
            bits.append(text)
            el = el.getnext()
        return "\n".join(bits)

    bond_blob = between(bond, "已标价")
    bill_blob = between(bill, "其他资料")
    other_blob = between(other, "")
    assert "粘贴" in bond_blob and "投标保证金" in bond_blob
    assert "粘贴" in bill_blob and "工程量清单" in bill_blob
    assert "投标保证金" not in other_blob
    assert "工程量清单" not in other_blob
    mention = next(p for p in doc.paragraphs if "如有" in (p.text or ""))
    assert mention._p.getnext().tag != qn("w:tbl")


def test_letter_body_does_not_take_paste_boxes() -> None:
    doc = Document()
    doc.add_paragraph("一、投标函及投标函附录")
    doc.add_paragraph("（一）投标函")
    doc.add_paragraph("我方已仔细研究了招标文件的全部内容。")
    doc.add_paragraph("（2）法定代表人身份证明或授权委托书；")
    item4 = doc.add_paragraph("（4）投标保证金（如有）；")
    doc.add_paragraph("（5）已标价工程量清单；")
    auth = doc.add_paragraph("二、授权委托书")
    doc.add_paragraph("委托代理人须附身份证。")
    bond = doc.add_paragraph("四、投标保证金")
    doc.add_paragraph("在此提供汇款凭证复印件。")
    doc.add_paragraph("五、资格审查资料")
    place_unmatched_slots(
        doc,
        slots=[
            PlaceholderItem(
                key="id_agent",
                title="如由委托代理人投标，须提供授权委托书及代理人身份证、近半年社保缴纳证明。",
            ),
            PlaceholderItem(key="bond", title="投标保证金缴存回单"),
        ],
        media={},
        required_keys=["id_agent", "bond"],
        volume="business",
    )
    assert item4._p.getnext().tag != qn("w:tbl")
    letter = next(p for p in doc.paragraphs if "身份证明或授权" in (p.text or ""))
    assert letter._p.getnext().tag != qn("w:tbl")

    def until(start, stop: str) -> str:
        bits = []
        el = start._element.getnext()
        while el is not None:
            text = "".join(t.text or "" for t in el.iter(qn("w:t")))
            if stop and stop in text and el.tag == qn("w:p"):
                break
            bits.append(text)
            el = el.getnext()
        return "\n".join(bits)

    assert "粘贴" not in until(auth, "投标保证金")
    assert "投标保证金" in until(bond, "资格审查") and "粘贴" in until(bond, "资格审查")
    tail = "\n".join(p.text or "" for p in doc.paragraphs)
    tail += "\n" + "\n".join(
        "".join(t.text or "" for t in tbl.iter(qn("w:t"))) for tbl in doc.element.body.iter(qn("w:tbl"))
    )
    assert "附件：资料库扫描件" not in tail and "如由委托代理人投标" not in tail


def test_unmatched_qual_veto_sits_in_other_materials() -> None:
    doc = Document()
    doc.add_paragraph("目录")
    doc.add_paragraph("九、其他资料")
    doc.add_paragraph("一、投标函")
    doc.add_paragraph("我方已仔细研究了招标文件的全部内容。")
    doc.add_paragraph("九、其他资料")
    doc.add_paragraph("除以上资料外，按招标文件要求补充。")
    chapter = doc.add_paragraph("（六）资格审查其他材料")
    doc.add_paragraph("十、施工组织设计")
    notes = place_unmatched_slots(
        doc,
        slots=[],
        media={},
        required_keys=[],
        volume="business",
        requirements=[
            BidRequirement(
                text="投标人须提供有效资质证明，否则否决投标。",
                strength="mandatory",
                matchStatus="unbound",
                volume="business",
            ),
            BidRequirement(
                text="已提供的资质证明不再重复占位。",
                strength="mandatory",
                matchStatus="matched",
                volume="business",
            ),
        ],
    )
    texts: list[str] = []
    el = chapter._element.getnext()
    while el is not None:
        text = "".join(t.text or "" for t in el.iter(qn("w:t")))
        if "施工组织" in text:
            break
        texts.append(text)
        el = el.getnext()
    assert any("资质证明" in t and "粘贴" in t for t in texts)
    assert "附件：资料库扫描件" not in "\n".join(p.text or "" for p in doc.paragraphs)
    assert any("资格审查其他材料" in n for n in notes)


def test_scattered_attach_request_boxes_on_its_chapter() -> None:
    doc = Document()
    doc.add_paragraph("一、投标函")
    doc.add_paragraph("（4）投标保证金（如有）")
    doc.add_paragraph("我方已仔细研究了招标文件的全部内容。")
    chapter = doc.add_paragraph("六、安全生产许可证")
    doc.add_paragraph("七、其他资料")
    notes = place_unmatched_slots(
        doc,
        slots=[PlaceholderItem(key="license", title="营业执照")],
        media={},
        required_keys=["license"],
        volume="business",
        requirements=[
            BidRequirement(
                text="投标文件中应附安全生产许可证。",
                strength="general",
                volume="business",
                category="material",
            ),
            BidRequirement(
                text="投标文件中应附安全生产许可证复印件。",
                strength="general",
                volume="business",
                category="material",
            ),
        ],
    )
    letter = next(p for p in doc.paragraphs if (p.text or "").startswith("（4）"))
    assert letter._p.getnext().tag != qn("w:tbl")

    def until(start, stop: str) -> str:
        bits = []
        el = start._element.getnext()
        while el is not None:
            text = "".join(t.text or "" for t in el.iter(qn("w:t")))
            if stop and stop in text and el.tag == qn("w:p"):
                break
            bits.append(text)
            el = el.getnext()
        return "\n".join(bits)

    blob = until(chapter, "其他资料")
    assert "安全生产许可证" in blob and "粘贴" in blob
    assert blob.count("此处粘贴") == 1
    assert any(n.startswith("正文散落要求已纳入占位") and "安全生产许可证" in n for n in notes)
    other = until(next(p for p in doc.paragraphs if (p.text or "").startswith("七、")), "")
    assert "营业执照" not in other and "粘贴" not in other
    assert any("待指定资料落位" in n and "营业执照" in n for n in notes)


def test_uploaded_slot_is_not_replaced(tmp_path) -> None:
    doc = Document()
    doc.add_paragraph("营业执照")
    own = tmp_path / "lic.pdf"
    own.write_bytes(b"%PDF-1.1")
    place_unmatched_slots(
        doc,
        slots=[PlaceholderItem(key="license", title="营业执照")],
        media={"license": [own]},
        required_keys=["license"],
        volume="business",
    )
    blob = "\n".join(
        "".join(t.text or "" for t in tbl.iter(qn("w:t"))) for tbl in doc.element.body
    )
    assert "此处粘贴营业执照" in blob
    assert "附件：资料库扫描件" not in "\n".join(p.text or "" for p in doc.paragraphs)


def test_gap_lands_on_other_necessary_materials() -> None:
    doc = Document()
    doc.add_paragraph("一、投标函")
    doc.add_paragraph("我方已仔细研究了招标文件的全部内容。")
    doc.add_paragraph("二、资格审查资料")
    other = doc.add_paragraph("（三）其他有必要提供的材料")
    doc.add_paragraph("（四）其他说明")
    place_unmatched_slots(
        doc,
        slots=[PlaceholderItem(key="credit", title="信用中国查询截图")],
        media={},
        required_keys=["credit"],
        volume="business",
    )
    bits: list[str] = []
    el = other._element.getnext()
    while el is not None:
        text = "".join(t.text or "" for t in el.iter(qn("w:t")))
        if "其他说明" in text:
            break
        bits.append(text)
        el = el.getnext()
    assert "粘贴" not in "\n".join(bits)
    tail = "\n".join(p.text or "" for p in doc.paragraphs)
    tail += "\n" + "\n".join(
        "".join(t.text or "" for t in tbl.iter(qn("w:t"))) for tbl in doc.element.body.iter(qn("w:tbl"))
    )
    assert "附件：资料库扫描件" not in tail and "信用中国" not in tail


def test_legal_id_already_in_chapter_is_not_repeated() -> None:
    doc = Document()
    doc.add_paragraph("二、法定代表人身份证明")
    doc.add_paragraph("附：法定代表人身份证")
    # 这一段后面已有虚线框，表示身份证明章节里贴过了
    from api.services.tenders.placeholders import draw_placeholder_box

    anchor = doc.paragraphs[-1]
    draw_placeholder_box(doc, PlaceholderItem(key="id_legal", title="附：法定代表人身份证"), after=anchor)
    doc.add_paragraph("（三）其他有必要提供的材料")
    place_unmatched_slots(
        doc,
        slots=[PlaceholderItem(key="id_legal", title="法定代表人身份证正反面")],
        media={},
        required_keys=["id_legal"],
        volume="business",
    )
    paras = "\n".join(p.text or "" for p in doc.paragraphs)
    assert "附件：资料库扫描件" not in paras
    assert paras.count("法定代表人身份证正反面") == 0


def test_note_anchor_or_new_other_subsection() -> None:
    doc = Document()
    doc.add_paragraph("一、投标函")
    doc.add_paragraph("我方已仔细研究了招标文件的全部内容。")
    listed = doc.add_paragraph("（4）投标保证金（如有）")
    auth = doc.add_paragraph("二、授权委托书")
    doc.add_paragraph("委托代理人签字。")
    legal = doc.add_paragraph("三、法定代表人身份证明")
    doc.add_paragraph("本人系法定代表人。")
    doc.add_paragraph("（二）近年财务状况表")
    finance = doc.add_paragraph("注：本表后应附成立以来的财务状况表。")
    doc.add_paragraph("（三）正在施工和新承接的项目情况表")
    pact = doc.add_paragraph("注：本表后附中标通知书或合同协议书。")
    doc.add_paragraph("（四）国家企业信用信息公示系统")
    shot = doc.add_paragraph("注：附上述途径查询结果截图。")
    doc.add_paragraph("（五）近年完成的类似项目情况表")
    doc.add_paragraph("本表由投标人填写。")
    doc.add_paragraph("九、其他资料")
    stmt = doc.add_paragraph("（一）中小企业声明函（如有）")
    doc.add_paragraph("本公司（联合体）郑重声明，根据规定参加本次采购。")
    doc.add_paragraph("1、标的名称属于工业行业。")
    welfare = doc.add_paragraph("（二）残疾人福利性单位声明函（如有）")
    doc.add_paragraph("本单位郑重声明。")
    plate = doc.add_paragraph("（六）资格审查其他材料")
    doc.add_paragraph("十、施工组织设计")
    notes = place_unmatched_slots(
        doc,
        slots=[
            PlaceholderItem(key="fin", title="成立以来的财务状况表"),
            PlaceholderItem(key="pact", title="合同协议书"),
            PlaceholderItem(key="award", title="中标通知书"),
            PlaceholderItem(key="shot", title="上述途径查询结果截图"),
            PlaceholderItem(key="bill", title="本合同结算总价款的全额有效发票"),
        ],
        media={},
        required_keys=["fin", "pact", "award", "shot", "bill"],
        volume="business",
    )

    def cell(el) -> str:
        if el is None:
            return ""
        return "".join(t.text or "" for t in el.iter(qn("w:t")))

    def between(start, stop: str) -> str:
        bits = []
        el = start._element.getnext()
        while el is not None:
            text = cell(el)
            if stop and stop in text and el.tag == qn("w:p"):
                break
            bits.append(text)
            el = el.getnext()
        return "\n".join(bits)

    assert _is_slot_box(next_content(finance._p))
    assert "此处应粘贴对应的资料附件或图片" in cell(next_content(finance._p))
    assert not _is_slot_box(next_content(finance._p, 2))
    assert _is_slot_box(next_content(pact._p))
    assert "此处粘贴中标通知书或合同协议书" in cell(next_content(pact._p))
    assert not _is_slot_box(next_content(pact._p, 2))
    assert _is_slot_box(next_content(shot._p))
    assert "此处粘贴国家企业信用信息公示系统查询截图" in cell(next_content(shot._p))
    assert not _is_slot_box(next_content(shot._p, 2))
    assert "粘贴" not in between(stmt, "残疾人")
    assert "粘贴" not in between(welfare, "资格审查其他材料")
    assert "全额有效发票" in cell(next_content(plate._p))
    assert not _is_slot_box(next_content(listed._p))
    assert not _is_slot_box(next_content(auth._p))
    assert not _is_slot_box(next_content(legal._p))
    joined = "\n".join(notes)
    assert "资格审查其他材料" in joined and "全额有效发票" in joined
    assert "其他需补充的材料" not in joined
    assert sum("此处粘贴中标通知书或合同协议书" in cell(t) for t in doc.element.body) == 1


def test_sweep_fills_finance_note_once() -> None:
    doc = Document()
    doc.add_paragraph("（二）近年财务状况表")
    note = doc.add_paragraph("注：在此附经审计的财务会计报表。应提供成立以来的财务状况表和纳税社保证明材料。")
    doc.add_paragraph("（三）正在施工和新承接项目情况表")
    doc.add_paragraph("投标人应按实填写正在施工项目。")
    org = doc.add_paragraph("（四）施工组织设计编制要求")
    doc.add_paragraph("投标人应根据招标文件编制施工组织设计。")
    place_unmatched_slots(
        doc,
        slots=[
            PlaceholderItem(key="fin", title="成立以来的财务状况表"),
            PlaceholderItem(key="tax", title="纳税社保证明材料"),
        ],
        media={},
        required_keys=["fin", "tax"],
        volume="business",
    )
    nxt = next_content(note._p)
    assert _is_slot_box(nxt)
    blob = "".join(t.text or "" for t in nxt.iter(qn("w:t")))
    assert "此处粘贴财务会计报表" in blob
    assert "此处粘贴财务状况表" in next_content(nxt).xml
    assert "此处粘贴纳税社保证明材料" in next_content(nxt, 2).xml
    assert not _is_slot_box(next_content(nxt, 3))
    assert not _is_slot_box(next_content(org._p))


def test_empty_review_plate_gets_none_letter() -> None:
    doc = Document()
    plate = doc.add_paragraph("（六）资格审查其他材料")
    doc.add_paragraph("七、投标保证金")
    notes = place_unmatched_slots(
        doc,
        slots=[],
        media={},
        required_keys=[],
        volume="business",
    )
    bits = []
    el = plate._element.getnext()
    while el is not None:
        text = "".join(t.text or "" for t in el.iter(qn("w:t")))
        if "投标保证金" in text:
            break
        bits.append(text)
        el = el.getnext()
    blob = "\n".join(bits)
    assert "资格审查其他材料无" in blob
    assert "我方无需要补充的其他资格审查材料。" in blob
    assert blob.count("资格审查其他材料无") == 1
    assert any("已补无声明" in n for n in notes)


def test_technical_leftover_does_not_create_tail_or_use_business_plate() -> None:
    doc = Document()
    plate = doc.add_paragraph("（六）资格审查其他材料")
    doc.add_paragraph("七、下一章")
    place_unmatched_slots(
        doc,
        slots=[PlaceholderItem(key="extra", title="专项施工方案")],
        media={},
        required_keys=["extra"],
        volume="technical",
    )
    bits = []
    el = plate._element.getnext()
    while el is not None:
        text = "".join(t.text or "" for t in el.iter(qn("w:t")))
        if "下一章" in text:
            break
        bits.append(text)
        el = el.getnext()
    assert "专项施工方案" not in "\n".join(bits)
    assert "资格审查其他材料无" not in "\n".join(p.text or "" for p in doc.paragraphs)
    tail = "\n".join(p.text or "" for p in doc.paragraphs)
    tail += "\n" + "\n".join(
        "".join(t.text or "" for t in tbl.iter(qn("w:t"))) for tbl in doc.element.body.iter(qn("w:tbl"))
    )
    assert "附件：资料库扫描件" not in tail and "专项施工方案" not in tail
