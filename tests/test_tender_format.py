"""招标书排版条款：页边距 / 封面 / 目录页码 / 页脚 PAGE 域。"""

from __future__ import annotations

import re

import pytest
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

from api.services.tenders.assemble import assemble_bid_docx
from api.services.tenders.format_rules import (
    extract_document_format,
    extract_tender_no,
    toc_item_label,
)
from api.services.tenders.schema import DocumentFormat, OutlineItem, default_brief


def test_extract_skips_unspecified_invitation() -> None:
    fmt = extract_document_format("第一章 投标邀请\n项目名称：某某充电站\n投标人须知")
    assert fmt.specified is False
    assert fmt.pageNumberPos == "bottom-center"
    assert fmt.pageNumberStart == "body"
    assert fmt.tocNeedPageNos is True
    assert fmt.tocNumbering == "cn"
    assert extract_tender_no("项目名称：某某") == ""


def test_extract_full_format_chapter() -> None:
    text = """
投标文件格式要求：
1. 封面（必填）项目全称、招标编号、投标人全称、“正本 / 副本” 字样、投标日期；封面加盖公章。
招标编号：WT-2026-088。
2. 目录层级一、1、(1)，目录页码和正文页码一一对应，不能错页。
3. 页码连续编码，不能跳号。页底居中。封面、扉页不编页码，从目录起编。
页边距均为 2.5cm。正文用宋体小四，一级标题用三号。
"""
    fmt = extract_document_format(text)
    assert fmt.specified is True
    assert fmt.marginLeftCm == 2.5
    assert fmt.marginTopCm == 2.5
    assert fmt.fontName == "宋体"
    assert fmt.bodySizePt == 12
    assert fmt.headingSizePt == 16
    assert fmt.coverShowTenderNo is True
    assert fmt.coverShowCopyMark is True
    assert fmt.coverNeedSeal is True
    assert fmt.tocNeedPageNos is True
    assert fmt.tocNumbering == "cn"
    assert fmt.pageNumberPos == "bottom-center"
    assert fmt.pageNumberStart == "toc"
    assert extract_tender_no(text) == "WT-2026-088"
    assert extract_tender_no("项目编号：豫财招标采购-2026-906") == "豫财招标采购-2026-906"
    assert extract_tender_no("项目编号：2026年10月28日") == ""
    assert "封面含" in "；".join(fmt.notes)


def test_extract_bottom_right_and_named_margins() -> None:
    text = (
        "装订要求：上边距 3cm，下边距 2.5cm，左边距 2.8cm，右边距 2.6cm。"
        "页码置于右下角，从正文起编。"
    )
    fmt = extract_document_format(text)
    assert fmt.marginTopCm == 3.0
    assert fmt.marginBottomCm == 2.5
    assert fmt.pageNumberPos == "bottom-right"
    assert fmt.pageNumberStart == "body"


def test_toc_item_label_schemes() -> None:
    assert toc_item_label(0, "cn") == "一、"
    assert toc_item_label(1, "arabic") == "2."
    assert toc_item_label(0, "paren") == "(1)"
    assert toc_item_label(0, "attach") == "附件一"


def test_assemble_applies_format_cover_and_page_fields(tmp_path) -> None:
    brief = default_brief()
    brief.layoutMode = "outline"
    brief.projectName = "厂内充电桩采购项目"
    brief.tenderer = "某市热电有限公司"
    brief.tenderNo = "WT-2026-088"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.documentFormat = DocumentFormat(
        specified=True,
        marginLeftCm=2.5,
        marginRightCm=2.5,
        marginTopCm=2.5,
        marginBottomCm=2.5,
        coverShowTenderNo=True,
        coverShowCopyMark=True,
        coverCopyMark="正本",
        coverNeedSeal=True,
        tocNeedPageNos=True,
        pageNumberPos="bottom-right",
        pageNumberStart="toc",
        fontName="宋体",
    )
    brief.outlineItems = [
        OutlineItem(id="o01", title="授权委托书", kind="auth", source="generate"),
        OutlineItem(id="o02", title="法定代表人身份证明", kind="legal_id", source="generate"),
    ]
    dest = tmp_path / "fmt.docx"
    _, warnings = assemble_bid_docx(brief, dest, qualification_pdf=None)
    assert any("封面加盖公章" in w for w in warnings)
    doc = Document(str(dest))
    texts = [p.text for p in doc.paragraphs if p.text.strip()]
    blob = "\n".join(texts)
    assert "厂内充电桩采购项目" in blob
    assert "招标编号：WT-2026-088" in blob
    assert "正本" in blob
    mark = next(p for p in doc.paragraphs if (p.text or "").strip() == "正本")
    assert mark.alignment == WD_ALIGN_PARAGRAPH.RIGHT
    mark_i = next(i for i, p in enumerate(doc.paragraphs) if (p.text or "").strip() == "正本")
    title_i = next(i for i, p in enumerate(doc.paragraphs) if "厂内充电桩采购项目" in (p.text or ""))
    assert mark_i < title_i
    assert "（封面加盖公章）" in blob
    assert any(t.startswith("一、授权委托书") for t in texts)
    assert any("授权委托书" in t for t in texts)
    cover = doc.sections[0]
    rest = doc.sections[1]
    assert abs(float(cover.left_margin.cm) - 2.5) < 0.05
    assert abs(float(rest.left_margin.cm) - 2.5) < 0.05
    cover_footer = cover.footer.paragraphs[0]._p.xml
    assert "PAGE" not in cover_footer
    rest_xml = rest.footer.paragraphs[0]._p.xml
    assert "PAGE" in rest_xml
    assert rest.footer.paragraphs[0].alignment is not None
    bookmarks = {el.get(qn("w:name")) for el in doc.element.iter(qn("w:bookmarkStart"))}
    assert "toc_o01" in bookmarks
    pagerefs = [
        "".join(t.text or "" for t in el.iter(qn("w:instrText")))
        for el in doc.element.iter(qn("w:instrText"))
    ]
    assert any("PAGEREF toc_o01" in x for x in pagerefs)


def test_assemble_cover_meta_sits_in_lower_third(tmp_path) -> None:
    brief = default_brief()
    brief.layoutMode = "outline"
    brief.projectName = "厂内新能源充电桩采购项目"
    brief.tenderer = "二连浩特市联源热电有限公司"
    brief.bidderName = "河南伟泰光电科技有限公司"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.documentFormat = DocumentFormat(specified=False)
    brief.outlineItems = [
        OutlineItem(id="o01", title="投标函", kind="letter", source="generate"),
    ]
    dest = tmp_path / "cover-meta.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    doc = Document(str(dest))
    body = doc.element.body
    events: list[tuple[str, str]] = []
    for child in body:
        tag = child.tag
        if tag == qn("w:p"):
            text = "".join(t.text or "" for t in child.iter(qn("w:t"))).replace("\u200b", "").strip()
            p_pr = child.find(qn("w:pPr"))
            line_twips = 0
            if p_pr is not None:
                sp = p_pr.find(qn("w:spacing"))
                if sp is not None and (sp.get(qn("w:lineRule")) or "") == "exact":
                    line_twips = int(sp.get(qn("w:line")) or 0)
            if line_twips >= 240 and not text:
                events.append(("gap", str(line_twips)))
            elif text:
                events.append(("p", text))
        if any(e[0] == "p" and e[1] == "目录" for e in events):
            break
    texts = [t for k, t in events if k == "p"]
    assert any("厂内新能源充电桩采购项目" in t for t in texts)
    assert any(t.replace(" ", "") == "投标文件" for t in texts)
    title_i = next(i for i, (k, t) in enumerate(events) if k == "p" and "投 标 文 件" in t)
    meta_i = next(i for i, (k, t) in enumerate(events) if k == "p" and t.startswith("招标人："))
    spacer_i = next(i for i, (k, t) in enumerate(events) if k == "gap" and title_i < i < meta_i)
    spacer_twips = int(events[spacer_i][1])
    assert spacer_twips >= int(4.0 * 567)
    assert any(t.startswith("投标人：") for t in texts)


def test_assemble_default_skips_cover_page_number(tmp_path) -> None:
    brief = default_brief()
    brief.layoutMode = "outline"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(id="o01", title="投标函", kind="letter", source="generate"),
    ]
    dest = tmp_path / "default-fmt.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    doc = Document(str(dest))
    assert len(doc.sections) >= 3
    assert "PAGE" not in doc.sections[0].footer.paragraphs[0]._p.xml
    assert "PAGE" not in doc.sections[1].footer.paragraphs[0]._p.xml
    assert "PAGE" in doc.sections[2].footer.paragraphs[0]._p.xml
    assert abs(float(doc.sections[0].left_margin.cm) - 2.8) < 0.05
    assert abs(float(doc.sections[0].page_width.cm) - 21.0) < 0.05


def test_toc_item_label_uses_chinese_past_ten() -> None:
    assert toc_item_label(0, "cn") == "一、"
    assert toc_item_label(9, "cn") == "十、"
    assert toc_item_label(10, "cn") == "十一、"
    assert toc_item_label(16, "cn") == "十七、"


def test_word_template_keeps_format_and_plain_toc(tmp_path, monkeypatch) -> None:
    """上传 Word 后按本标生成：用切出来的原版，目录按后文标题写页码，只填对得上的空。"""
    from docx.enum.section import WD_SECTION

    from api.services.tenders.pdf_format import _slice_word_format

    def add_page(doc: Document, build) -> None:
        if any((p.text or "").strip() for p in doc.paragraphs):
            doc.add_section(WD_SECTION.NEW_PAGE)
        build(doc)

    raw = Document()
    add_page(raw, lambda d: d.add_paragraph("第八章、投标文件格式"))
    def cover(d: Document) -> None:
        p = d.add_paragraph()
        blank = p.add_run("　　")
        blank.underline = True
        p.add_run("（项目名称）标段施工")
        d.add_paragraph("投 标 文 件")
        no = d.add_paragraph()
        no.add_run("招标编号：")
        gap = no.add_run("　　")
        gap.underline = True
    add_page(raw, cover)
    add_page(
        raw,
        lambda d: (
            d.add_paragraph("目 录"),
            d.add_paragraph("投标函"),
            d.add_paragraph("法定代表人身份证明"),
            d.add_paragraph("授权委托书"),
        ),
    )
    add_page(
        raw,
        lambda d: (
            d.add_paragraph("投标函"),
            d.add_paragraph("我方已仔细研究了招标文件的全部内容。"),
        ),
    )
    add_page(raw, lambda d: d.add_paragraph("法定代表人身份证明"))
    src = tmp_path / "invite.docx"
    raw.save(src)
    sliced = tmp_path / "fmt.docx"
    _slice_word_format(src, sliced)
    kept = "\n".join(p.text or "" for p in Document(str(sliced)).paragraphs)
    assert "第八章、投标文件格式" not in kept
    assert "我方已仔细研究了" in kept

    monkeypatch.setattr(
        "api.services.tenders.pdf_format._word_format_file",
        lambda invitation_id: sliced,
    )
    brief = default_brief()
    brief.layoutMode = "outline"
    brief.invitationId = "abc123abc123"
    brief.projectName = "测试项目"
    brief.tenderNo = "WT-9"
    brief.bidDate = "2026-10-21"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(id="o01", title="投标函", kind="letter", source="generate"),
    ]
    dest = tmp_path / "out.docx"
    _path, notes = assemble_bid_docx(brief, dest, qualification_pdf=None)
    doc = Document(str(dest))
    blob = "\n".join(p.text or "" for p in doc.paragraphs)
    assert "我方已仔细研究了招标文件的全部内容" in blob
    assert "第八章、投标文件格式" not in blob
    assert "错误！未定义书签" not in blob
    assert "PAGEREF" in doc.element.xml
    assert "TOC" in doc.element.xml
    toc = next(p for p in doc.paragraphs if (p.text or "").strip().startswith("投标函") and "研究" not in (p.text or ""))
    assert re.sub(r"\s+\d+$", "", (toc.text or "").strip()) == "投标函"
    assert re.search(r"\d+$", (toc.text or "").strip())
    assert toc._p.getnext() is None or toc._p.getnext().tag != qn("w:tbl")
    legal = next(p for p in doc.paragraphs if (p.text or "").startswith("法定代表人身份证明"))
    assert re.sub(r"\s+\d+$", "", (legal.text or "").strip()) == "法定代表人身份证明"
    assert legal._p.getnext() is None or legal._p.getnext().tag != qn("w:tbl")
    assert "测试项目" in blob
    assert "WT-9" in blob
    assert any("原版" in n for n in notes)


def test_slice_pdf_keeps_requested_pages(tmp_path) -> None:
    from pypdf import PdfReader, PdfWriter

    from api.services.tenders.pdf_format import _slice_pdf_pages

    src = tmp_path / "full.pdf"
    writer = PdfWriter()
    for _ in range(4):
        writer.add_blank_page(width=595, height=842)
    with src.open("wb") as fh:
        writer.write(fh)
    out = tmp_path / "cut.pdf"
    _slice_pdf_pages(src, out, 2, 3)
    assert len(PdfReader(str(out)).pages) == 2


def test_convert_pages_sends_sliced_pdf(tmp_path, monkeypatch) -> None:
    from pypdf import PdfReader, PdfWriter

    from api.services.tenders.pdf_format import _convert_pages

    src = tmp_path / "full.pdf"
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=595, height=842)
    with src.open("wb") as fh:
        writer.write(fh)
    seen: dict[str, int] = {}

    def fake(pdf, dest) -> None:
        seen["pages"] = len(PdfReader(str(pdf)).pages)
        Document().save(dest)

    monkeypatch.setattr("api.services.tenders.aliyun_doc.convert_pdf_to_docx", fake)
    dest = tmp_path / "ab.format.docx.part"
    _convert_pages(src, dest, 2, 3)
    assert seen["pages"] == 2
    assert dest.is_file()
    assert (tmp_path / "ab.format.pdf").is_file()


def test_aliyun_fill_keeps_unmatched_text(tmp_path, monkeypatch) -> None:
    src = tmp_path / "fmt.docx"
    raw = Document()
    raw.add_paragraph("第八章、投标文件格式")
    raw.add_paragraph("此处保持原样，不要改。")
    no = raw.add_paragraph()
    no.add_run("招标编号：")
    gap = no.add_run("　　")
    gap.underline = True
    table = raw.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "地"
    table.rows[0].cells[1].text = "址："
    raw.save(src)
    src.with_name(src.name + ".source").write_text("aliyun", encoding="utf-8")
    monkeypatch.setattr(
        "api.services.tenders.pdf_format._ready_format_docx",
        lambda invitation_id: src if invitation_id else None,
    )
    monkeypatch.setattr(
        "api.services.tenders.pdf_format._word_format_file",
        lambda invitation_id: None,
    )
    brief = default_brief()
    brief.layoutMode = "outline"
    brief.invitationId = "abc123abc123"
    brief.tenderNo = "WT-9"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(id="o01", title="投标函", kind="letter", source="generate"),
    ]
    dest = tmp_path / "out.docx"
    _path, notes = assemble_bid_docx(brief, dest, qualification_pdf=None)
    doc = Document(str(dest))
    blob = "\n".join(p.text or "" for p in doc.paragraphs)
    assert "第八章、投标文件格式" in blob  # 同页标题无独立分页证据，保留原样
    assert "此处保持原样，不要改。" in blob
    assert "WT-9" in blob
    assert doc.tables[0].rows[0].cells[0].text.strip() == "地"
    assert any("原版" in n for n in notes)


def test_aliyun_mark_still_uses_format_file(tmp_path, monkeypatch) -> None:
    from api.services.tenders.pdf_format import _word_format_file

    invite = "abc123abc123"
    dest = tmp_path / f"{invite}.format.docx"
    raw = Document()
    raw.add_paragraph("投标函")
    raw.add_paragraph("我方已仔细研究了招标文件的全部内容。")
    raw.save(dest)
    dest.with_name(dest.name + ".source").write_text("aliyun", encoding="utf-8")
    monkeypatch.setattr(
        "api.services.tenders.pdf_format.format_docx_path",
        lambda invitation_id: dest if invitation_id == invite else None,
    )
    got = _word_format_file(invite)
    assert got is None


def test_word_replaces_aliyun_mark(tmp_path, monkeypatch) -> None:
    from api.services.tenders.pdf_format import _word_format_file

    invite = "abc123abc124"
    dest = tmp_path / f"{invite}.format.docx"
    Document().save(dest)
    dest.with_name(dest.name + ".source").write_text("aliyun", encoding="utf-8")
    word = tmp_path / f"{invite}.docx"
    src = Document()
    src.add_paragraph("第八章、投标文件格式")
    src.add_paragraph("我方已仔细研究了招标文件的全部内容。")
    src.save(word)
    monkeypatch.setattr(
        "api.services.tenders.pdf_format.format_docx_path",
        lambda invitation_id: dest if invitation_id == invite else None,
    )
    got = _word_format_file(invite)
    assert got == dest
    kept = "\n".join(p.text or "" for p in Document(str(dest)).paragraphs)
    assert "我方已仔细研究了" in kept
    assert dest.with_name(dest.name + ".source").read_text(encoding="utf-8").strip() == "docx-raw"


def test_appendix_table_fill_keeps_cells(tmp_path) -> None:
    from api.services.tenders.pdf_format import _fill_word_blanks

    doc = Document()
    doc.add_paragraph("（二）投标函附录")
    table = doc.add_table(rows=4, cols=5)
    table.rows[0].cells[0].text = "项目名称"
    table.rows[1].cells[0].text = "投标人"
    table.rows[2].cells[0].text = "投标范围"
    fee = table.rows[3]
    fee.cells[0].text = "安全生产措施费（元）"
    fee.cells[1].text = "大写："
    fee.cells[3].text = "小写："
    brief = default_brief()
    brief.projectName = "郑州项目"
    brief.bidderName = "河南伟泰光电科技有限公司"
    brief.bidContent = "电站施工"
    brief.safetyFeeYuan = 100
    _fill_word_blanks(doc, brief)
    assert len(doc.tables) == 1
    assert len(doc.tables[0].rows) == 4
    assert table.rows[0].cells[1].text.strip() == "郑州项目"
    assert "伟泰" in table.rows[1].cells[1].text
    assert table.rows[2].cells[1].text.strip() == "电站施工"
    assert "壹佰元整" in fee.cells[2].text
    assert "100.00" in fee.cells[4].text
    assert "大写" in fee.cells[1].text
    assert "小写" in fee.cells[3].text


def _under_tab(para):
    from docx.oxml import OxmlElement

    run = para.add_run()
    run.underline = True
    run._r.append(OxmlElement("w:tab"))
    return run


def test_resume_keeps_pm_blank_and_perf_off_work_history() -> None:
    from api.services.tenders.pdf_format import _fill_slice_sheets, _fill_word_blanks
    from api.services.tenders.schema import PerformanceLine

    doc = Document()
    doc.add_paragraph("（一）项目经理简历表")
    resume = doc.add_table(rows=2, cols=4)
    resume.rows[0].cells[0].text = "姓名"
    resume.rows[0].cells[2].text = "年龄"
    resume.rows[1].cells[0].text = "拟在本工程任职"
    doc.add_paragraph("（二）项目经理主要工作经历")
    work = doc.add_table(rows=2, cols=4)
    for i, title in enumerate(("时间", "参加过的类似项目名称", "工程概况说明", "发包人及联系电话")):
        work.rows[0].cells[i].text = title
    doc.add_paragraph("（三）类似项目情况")
    perf = doc.add_table(rows=2, cols=3)
    for i, title in enumerate(("项目名称", "合同金额", "发包人")):
        perf.rows[0].cells[i].text = title
    brief = default_brief()
    brief.legalPersonName = "郭志伟"
    brief.pmName = ""
    brief.performanceLines = [
        PerformanceLine(projectName="待补充类似业绩1", summary="待补充", client="某业主")
    ]
    _fill_word_blanks(doc, brief)
    _fill_slice_sheets(doc, brief)
    assert "郭志伟" not in (resume.rows[0].cells[1].text or "")
    work_blob = "\n".join(c.text or "" for row in work.rows for c in row.cells)
    assert "待补充类似业绩" not in work_blob
    perf_blob = "\n".join(c.text or "" for row in perf.rows for c in row.cells)
    assert "待补充类似业绩1" in perf_blob


def test_matched_text_starts_at_second_cell() -> None:
    from api.services.tenders.pdf_format import _fill_word_blanks

    doc = Document()
    cover = doc.add_paragraph()
    _under_tab(cover)
    cover.add_run("（项目名称）")
    _under_tab(cover)
    cover.add_run("标段施工")
    bidder = doc.add_paragraph()
    bidder.add_run("投标人或联合体牵头人：")
    _under_tab(bidder)
    brief = default_brief()
    brief.projectName = "郑州高新技术产业开发区新能源充电站"
    brief.bidderName = "河南伟泰光电科技有限公司"
    _fill_word_blanks(doc, brief)
    lead = "\u3000"
    cover_text = cover.text or ""
    assert cover_text.startswith(lead + "郑州高新技术产业开发区新能源充电站")
    assert cover_text.endswith("标段施工")
    assert "w:tab" not in cover._p.xml
    bidder_text = bidder.text or ""
    assert bidder_text.startswith("投标人或联合体牵头人：" + lead + "河南伟泰光电科技有限公司")
    assert "w:tab" not in bidder._p.xml


def test_scrub_toc_drops_undefined_bookmark() -> None:
    from docx.enum.text import WD_BREAK

    from api.services.tenders.document import _add_pageref_run
    from api.services.tenders.pdf_format import _scrub_undefined_toc

    doc = Document()
    doc.add_paragraph("目录")
    hit = doc.add_paragraph("投标函")
    _add_pageref_run(hit, "toc_letter", "错误！未定义书签")
    miss = doc.add_paragraph("不存在的附件")
    _add_pageref_run(miss, "toc_missing", "错误！未定义书签")
    doc.add_paragraph("授权委托书")
    br = doc.add_paragraph()
    br.add_run().add_break(WD_BREAK.PAGE)
    doc.add_paragraph("投标函")
    doc.add_paragraph("我方已仔细研究了招标文件的全部内容。")
    _scrub_undefined_toc(doc)
    blob = "\n".join(p.text or "" for p in doc.paragraphs)
    assert "未定义书签" not in blob
    assert "PAGEREF" not in doc.element.xml
    assert "投标函" in (hit.text or "")
    assert re.search(r"\d", hit.text or "")
    assert "不存在的附件" in (miss.text or "")
    assert not re.search(r"\d", miss.text or "")


def test_outline_generate_requires_format_confirm() -> None:
    from api.services.tenders.records import require_format_confirm
    from common.errors import AppError

    blocked = default_brief()
    blocked.layoutMode = "outline"
    with pytest.raises(AppError, match="确认使用这份格式章模板") as raised:
        require_format_confirm(blocked)
    assert raised.value.status_code == 422
    ok = default_brief()
    ok.layoutMode = "outline"
    ok.formatConfirmed = True
    require_format_confirm(ok)
    require_format_confirm(default_brief())


def test_missing_format_does_not_rewrite(tmp_path, monkeypatch) -> None:
    from common.errors import AppError

    monkeypatch.setattr("api.services.tenders.pdf_format._word_format_file", lambda invitation_id: None)
    brief = default_brief()
    brief.layoutMode = "outline"
    brief.invitationId = "deadbeef0001"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(id="o01", title="投标函", kind="letter", source="generate"),
    ]
    dest = tmp_path / "stopped.docx"
    with pytest.raises(AppError, match="未截到投标文件格式章") as raised:
        assemble_bid_docx(brief, dest, qualification_pdf=None)
    assert raised.value.status_code == 422
    assert not dest.is_file()


def test_toc_page_break_before_body() -> None:
    from api.services.tenders.pdf_format import _separate_toc_page

    doc = Document()
    doc.add_paragraph("目录")
    doc.add_paragraph("一、投标函")
    doc.add_paragraph("二、法定代表人身份证明")
    doc.add_paragraph("三、授权委托书")
    doc.add_paragraph("投标函")
    doc.add_paragraph("我方已仔细研究了招标文件的全部内容。")
    _separate_toc_page(doc)
    seen_break = False
    broke_before_body = False
    for el in doc.element.body:
        if el.tag != qn("w:p"):
            continue
        text = re.sub(r"\s+", "", "".join(t.text or "" for t in el.iter(qn("w:t"))))
        has_break = any((n.get(qn("w:type")) or "") == "page" for n in el.iter(qn("w:br")))
        if has_break:
            seen_break = True
            continue
        if text == "投标函" and seen_break:
            broke_before_body = True
    assert broke_before_body


def test_toc_page_break_not_doubled() -> None:
    from docx.enum.text import WD_BREAK

    from api.services.tenders.pdf_format import _separate_toc_page

    doc = Document()
    doc.add_paragraph("目录")
    doc.add_paragraph("一、投标函")
    doc.add_paragraph("二、身份证明")
    doc.add_paragraph("三、授权委托书")
    gap = doc.add_paragraph()
    gap.add_run().add_break(WD_BREAK.PAGE)
    doc.add_paragraph("投标函")
    doc.add_paragraph("我方已仔细研究了招标文件的全部内容。")
    before = doc.element.xml.count('w:type="page"')
    _separate_toc_page(doc)
    assert doc.element.xml.count('w:type="page"') == before


def test_split_note_returns_below_table() -> None:
    from api.services.tenders.pdf_format import _fill_word_blanks

    doc = Document()
    table = doc.add_table(rows=2, cols=3)
    table.rows[0].cells[0].text = "序号"
    table.rows[0].cells[1].text = "名称"
    table.rows[0].cells[2].text = "备注"
    table.rows[1].cells[0].text = "备注：本表所列分包仅限"
    table.rows[1].cells[1].text = "于承包人自行施工"
    table.rows[1].cells[2].text = "范围内的非主体工程。"
    kept = table.rows[0].cells[0]._tc.find(qn("w:tcPr")).find(qn("w:tcW")).get(qn("w:w"))
    _fill_word_blanks(doc, default_brief())
    assert len(table.rows) == 1
    assert (table.rows[0].cells[0].text or "").strip() == "序号"
    nxt = table._tbl.getnext()
    note = "".join(t.text or "" for t in nxt.iter(qn("w:t")))
    assert note.startswith("备注：")
    assert "非主体工程" in note
    got = table.rows[0].cells[0]._tc.find(qn("w:tcPr")).find(qn("w:tcW")).get(qn("w:w"))
    assert got == kept


def _set_exact(row, twips: int) -> None:
    from docx.oxml import OxmlElement

    tr_pr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:trHeight")
    el.set(qn("w:val"), str(twips))
    el.set(qn("w:hRule"), "exact")
    tr_pr.append(el)


def _pin_tcw(cell, twips: int) -> str:
    from docx.oxml import OxmlElement

    tc_pr = cell._tc.get_or_add_tcPr()
    node = tc_pr.find(qn("w:tcW"))
    if node is None:
        node = OxmlElement("w:tcW")
        tc_pr.append(node)
    node.set(qn("w:type"), "dxa")
    node.set(qn("w:w"), str(twips))
    return node.get(qn("w:w"))


def test_note_inside_row_drops_table_float() -> None:
    from docx.oxml import OxmlElement

    from api.services.tenders.pdf_format import _settle_table_note

    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "序号"
    table.rows[0].cells[1].text = "名称"
    table.rows[1].cells[0].text = "备注：本表所列分包仅限"
    table.rows[1].cells[1].text = "于承包人自行施工范围内的非主体工程。"
    kept = _pin_tcw(table.rows[0].cells[0], 1800)
    pr = table._tbl.tblPr
    floated = OxmlElement("w:tblpPr")
    floated.set(qn("w:horzAnchor"), "margin")
    floated.set(qn("w:vertAnchor"), "text")
    pr.append(floated)
    _settle_table_note(table._tbl)
    assert table._tbl.tblPr.find(qn("w:tblpPr")) is None
    assert len(table.rows) == 1
    nxt = table._tbl.getnext()
    note = "".join(t.text or "" for t in nxt.iter(qn("w:t")))
    assert note.startswith("备注：")
    assert "非主体工程" in note
    got = table.rows[0].cells[0]._tc.find(qn("w:tcPr")).find(qn("w:tcW")).get(qn("w:w"))
    assert got == kept


def test_exact_row_opens_after_fill() -> None:
    from api.services.tenders.pdf_format import _fill_word_blanks

    doc = Document()
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "地址："
    table.rows[0].cells[1].text = ""
    _set_exact(table.rows[0], 280)
    kept = _pin_tcw(table.rows[0].cells[1], 1400)
    _fill_word_blanks(doc, default_brief())
    th = table.rows[0]._tr.find(qn("w:trPr")).find(qn("w:trHeight"))
    assert th.get(qn("w:hRule")) == "atLeast"
    assert th.get(qn("w:val")) == "280"
    got = table.rows[0].cells[1]._tc.find(qn("w:tcPr")).find(qn("w:tcW")).get(qn("w:w"))
    assert got == kept
    assert "郑州" in (table.rows[0].cells[1].text or "")


def test_exact_row_stays_when_text_fits() -> None:
    from api.services.tenders.pdf_format import _relax_exact_rows

    doc = Document()
    table = doc.add_table(rows=1, cols=1)
    table.rows[0].cells[0].text = "名称"
    _set_exact(table.rows[0], 900)
    kept = _pin_tcw(table.rows[0].cells[0], 2400)
    _relax_exact_rows(table._tbl)
    th = table.rows[0]._tr.find(qn("w:trPr")).find(qn("w:trHeight"))
    assert th.get(qn("w:hRule")) == "exact"
    assert th.get(qn("w:val")) == "900"
    got = table.rows[0].cells[0]._tc.find(qn("w:tcPr")).find(qn("w:tcW")).get(qn("w:w"))
    assert got == kept


def _dashed_table(tbl) -> bool:
    for node in tbl.iter():
        if node.tag == qn("w:top") and (node.get(qn("w:val")) or "") == "dashed":
            return True
    return False


def test_unmatched_library_item_gets_dashed_box() -> None:
    from api.services.tenders.pdf_format import _attach_library

    doc = Document()
    doc.add_paragraph("营业执照")
    doc.add_paragraph("后文保持不动")
    _attach_library(doc, default_brief(), {})
    assert doc.element.body.find(qn("w:tbl")) is None


def _rule_border(table) -> None:
    from docx.oxml import OxmlElement

    for cell in table.rows[0].cells:
        tc_pr = cell._tc.get_or_add_tcPr()
        borders = OxmlElement("w:tcBorders")
        edge = OxmlElement("w:bottom")
        edge.set(qn("w:val"), "single")
        edge.set(qn("w:sz"), "4")
        edge.set(qn("w:space"), "0")
        edge.set(qn("w:color"), "000000")
        borders.append(edge)
        tc_pr.append(borders)


def test_pdf_blank_rule_stays_on_one_line(tmp_path, monkeypatch) -> None:
    from api.services.tenders.pdf_format import assemble_from_word_format

    invite = "abc123abc125"
    src = tmp_path / f"{invite}.format.docx"
    raw = Document()
    raw.add_paragraph("投标人：")
    line = raw.add_table(rows=1, cols=1)
    _rule_border(line)
    raw.add_paragraph("（盖单位章）")
    kept = raw.add_table(rows=2, cols=3)
    kept.rows[0].cells[0].text = "序号"
    kept.rows[0].cells[1].text = "名称"
    kept.rows[0].cells[2].text = "金额"
    kept.rows[1].cells[0].text = "1"
    raw.save(src)
    src.with_name(src.name + ".source").write_text("pdf", encoding="utf-8")
    monkeypatch.setattr(
        "api.services.tenders.pdf_format._word_format_file",
        lambda invitation_id: src if invitation_id == invite else None,
    )
    brief = default_brief()
    brief.layoutMode = "outline"
    brief.invitationId = invite
    dest = tmp_path / "out.docx"
    _path, notes = assemble_from_word_format(brief, dest)
    doc = Document(str(dest))
    hit = next(p for p in doc.paragraphs if "投标人" in (p.text or "") and "盖单位章" in (p.text or ""))
    assert brief.bidderName in (hit.text or "")
    assert any(r.underline for r in hit.runs)
    blanks = "".join(r.text or "" for r in hit.runs if r.underline)
    assert blanks.count("\u3000") <= 8
    wrap = hit._p.find(qn("w:pPr")).find(qn("w:wordWrap"))
    assert wrap is not None and wrap.get(qn("w:val")) == "off"
    assert len(doc.tables) == 1
    assert doc.tables[0].rows[0].cells[0].text.strip() == "序号"
    assert any("预填横线" in n for n in notes)


def test_split_label_rule_becomes_one_underline() -> None:
    from api.services.tenders.pdf_format import _pull_pdf_blank_lines

    doc = Document()
    table = doc.add_table(rows=1, cols=4)
    _rule_border(table)
    table.rows[0].cells[0].text = "电话："
    table.rows[0].cells[1].text = ""
    table.rows[0].cells[2].text = "传真："
    table.rows[0].cells[3].text = ""
    _pull_pdf_blank_lines(doc)
    assert len(doc.tables) == 0
    text = "".join(p.text or "" for p in doc.paragraphs)
    assert "电话：" in text and "传真：" in text
    para = next(p for p in doc.paragraphs if "电话" in (p.text or ""))
    unders = [r for r in para.runs if r.underline]
    assert len(unders) == 2
    assert all((r.text or "").count("\u3000") <= 8 for r in unders)


def test_plain_line_is_not_a_placeholder() -> None:
    from api.services.tenders.pdf_format import _attach_library

    doc = Document()
    doc.add_paragraph("项目概况说明")
    _attach_library(doc, default_brief(), {})
    assert doc.element.body.find(qn("w:tbl")) is None


def test_paste_boxes_take_matching_scans(tmp_path) -> None:
    from PIL import Image

    from api.services.tenders.pdf_format import _attach_library

    files = {}
    for key, color in (("license", "blue"), ("finance", "red"), ("social", "green")):
        path = tmp_path / f"{key}.png"
        Image.new("RGB", (12, 8), color).save(path)
        files[key] = [path]
    doc = Document()
    doc.add_paragraph("此处粘贴营业执照复印件（在此粘贴扫描件）")
    doc.add_paragraph("此处粘贴纳税证明（在此粘贴扫描件）")
    doc.add_paragraph("此处粘贴社保证明（在此粘贴扫描件）")
    doc.add_paragraph("此处粘贴委托代理人身份证（在此粘贴扫描件）")
    doc.add_paragraph("项目概况说明")
    notes = _attach_library(doc, default_brief(), files)
    blob = "\n".join(p.text or "" for p in doc.paragraphs)
    assert "项目概况说明" in blob
    assert doc.element.body.find(qn("w:tbl")) is None
    # Photos have their own inline paragraphs after their material placeholders.
    for title in ("营业执照", "纳税", "社保"):
        holder = next(p for p in doc.paragraphs if title in p.text)
        assert next(holder._p.iter(qn("w:drawing")), None) is None
        assert next(holder._p.getnext().iter(qn("w:drawing")), None) is not None
        assert "在此粘贴扫描件" not in holder.text
    id_line = next(p for p in doc.paragraphs if "身份证" in (p.text or ""))
    assert "在此粘贴扫描件" in (id_line.text or "")
    assert next(id_line._p.iter(qn("w:drawing")), None) is None
    assert any("已贴上营业执照" in n for n in notes)
    assert any("已贴上纳税证明" in n for n in notes)
    assert any("已贴上社保证明" in n for n in notes)
    assert any(n.startswith("缺") and "身份证" in n for n in notes)


def test_paste_box_without_file_keeps_placeholder() -> None:
    from api.services.tenders.pdf_format import _attach_library

    doc = Document()
    doc.add_paragraph("此处粘贴营业执照（在此粘贴扫描件）")
    notes = _attach_library(doc, default_brief(), {})
    assert "在此粘贴扫描件" in (doc.paragraphs[0].text or "")
    assert any(n.startswith("缺") and "营业执照" in n for n in notes)
    assert doc.element.body.find(qn("w:tbl")) is None
    assert next(doc.element.body.iter(qn("w:drawing")), None) is None


def test_auth_letter_gets_legal_id_when_no_id_line(tmp_path) -> None:
    from PIL import Image

    from api.services.tenders.pdf_format import _paste_id_pages

    front = tmp_path / "front.png"
    back = tmp_path / "back.png"
    Image.new("RGB", (8, 8), "red").save(front)
    Image.new("RGB", (8, 8), "green").save(back)
    doc = Document()
    doc.add_paragraph("法定代表人授权委托书")
    doc.add_paragraph("")
    doc.add_paragraph("本人系投标人的法定代表人。")
    notes = _paste_id_pages(doc, default_brief(), {"id_legal": [front, back]})
    host = doc.paragraphs[1]._p
    assert list(host.iter(qn("w:drawing")))
    assert any("已贴上" in n and "身份证" in n for n in notes)
    assert "本人系投标人" in (doc.paragraphs[2].text or "")


def test_filee_convert_is_preferred(tmp_path, monkeypatch) -> None:
    from api.services.tenders import pdf_to_docx as mod

    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    dest = tmp_path / "a.docx"

    def fake_run(cmd, **kwargs):
        del cmd, kwargs
        Document().save(dest)

        class Done:
            returncode = 0
            stdout = ""
            stderr = ""

        return Done()

    monkeypatch.setattr(mod.shutil, "which", lambda name: "filee.exe" if name == "filee" else None)
    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    note = mod.convert_pdf_to_docx(pdf, dest)
    assert note.startswith("已用 Filee")
    assert dest.is_file() and dest.stat().st_size > 1000


def test_slice_copy_fills_only_existing_sheets(tmp_path, monkeypatch) -> None:
    from api.services.tenders.pdf_format import assemble_from_word_format
    from api.services.tenders.schema import PerformanceLine, QuoteLineIn

    invite = "abc123abc126"
    src = tmp_path / f"{invite}.format.docx"
    raw = Document()
    info = raw.add_table(rows=2, cols=2)
    info.rows[0].cells[0].text = "项目名称"
    info.rows[1].cells[0].text = "监理电话"
    raw.add_paragraph("监理单位：")
    quote = raw.add_table(rows=2, cols=4)
    for i, title in enumerate(("序号", "名称", "单位", "金额")):
        quote.rows[0].cells[i].text = title
    tech = raw.add_table(rows=1, cols=2)
    tech.rows[0].cells[0].text = "技术方案"
    raw.add_paragraph("附：法定代表人身份证")
    raw.add_paragraph("正文不要被改成另一套。")
    raw.save(src)
    src.with_name(src.name + ".source").write_text("docx-raw", encoding="utf-8")
    monkeypatch.setattr(
        "api.services.tenders.pdf_format._word_format_file",
        lambda invitation_id: src if invitation_id == invite else None,
    )
    brief = default_brief()
    brief.layoutMode = "outline"
    brief.invitationId = invite
    brief.projectName = "郑州项目"
    brief.legalPersonName = "郭志伟"
    brief.quoteLines = [QuoteLineIn(name="充电桩", unit="台", qty=2, unitPrice=10, amount=20)]
    brief.performanceLines = [PerformanceLine(projectName="某某医院配电工程", amountYuan=300000)]
    brief.techPlanNote = "按本标图纸施工"
    dest = tmp_path / "filled.docx"
    _path, notes = assemble_from_word_format(brief, dest)
    doc = Document(str(dest))
    blob = "\n".join(p.text or "" for p in doc.paragraphs)
    blob += "\n" + "\n".join(cell.text or "" for table in doc.tables for row in table.rows for cell in row.cells)
    assert "郑州项目" in blob
    assert "充电桩" in blob
    assert "台" in blob
    assert "按本标图纸施工" in blob
    assert "某某医院配电工程" not in blob
    assert "正文不要被改成另一套。" in blob
    assert "监理电话" in blob
    assert any("没有业绩空表" in n for n in notes)
    assert any("对不上的空保持原样" in n and "监理" in n for n in notes)
    assert any("郭志伟" in n and "未贴上" in n for n in notes)
    assert src.with_name(src.name + ".source").read_text(encoding="utf-8") == "docx-raw"


def test_pdf_layout_becomes_docx(tmp_path, monkeypatch) -> None:
    import fitz

    from api.services.tenders.pdf_to_docx import convert_pdf_to_docx

    monkeypatch.setattr("api.services.tenders.pdf_to_docx.shutil.which", lambda name: None)
    pdf = tmp_path / "bid.pdf"
    src = fitz.open()
    page = src.new_page()
    page.insert_text((72, 80), "投标文件格式", fontsize=16, fontname="china-s")
    src.save(pdf)
    src.close()
    dest = tmp_path / "bid.docx"
    note = convert_pdf_to_docx(pdf, dest)
    assert "转成 Word" in note
    text = "\n".join(p.text or "" for p in Document(str(dest)).paragraphs)
    assert "投标文件格式" in text


def test_format_toc_pages_follow_body(tmp_path) -> None:
    from api.services.tenders.pdf_format import _link_toc_body

    doc = Document()
    doc.add_paragraph("目录")
    doc.add_paragraph("一、投标函及投标函附录")
    doc.add_paragraph("二、法定代表人身份证明")
    doc.add_paragraph("十、施工组织格式")
    doc.add_paragraph("一、投标函及投标函附录")
    for _ in range(40):
        doc.add_paragraph("我方已仔细研究了招标文件的全部内容，愿意按约定完成承包工程。")
    doc.add_paragraph("二、法定代表人身份证明")
    doc.add_paragraph("本人系投标人的法定代表人。")
    for _ in range(40):
        doc.add_paragraph("附：法定代表人身份证复印件。投标文件应由法定代表人签署。")
    doc.add_paragraph("十、施工组织设计")
    doc.add_paragraph("施工组织设计正文。")
    n = _link_toc_body(doc)
    assert n == 3
    lines = ["".join((p.text or "").split()) for p in doc.paragraphs[:4]]
    assert lines[0] == "目录"
    nums = [re.search(r"(\d+)$", line).group(1) for line in lines[1:]]
    assert nums[0] == "1"
    assert int(nums[1]) > int(nums[0])
    assert int(nums[2]) > int(nums[1])
