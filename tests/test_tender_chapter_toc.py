"""Native directories cover physical chapters while excluding table cell labels."""

import re

import pytest
from docx import Document
from docx.oxml.ns import qn

from api.services.tenders import native_format, pdf_format
from api.services.tenders.document import _pageref_bookmark, _toc_region
from api.services.tenders.outline import extract_outline, is_outline_chapter
from api.services.tenders.schema import BidBrief, OutlineItem

NON_CHAPTERS = (
    "投标函",
    "投标函附录",
    "附表设备表",
    "已标价工程量清单",
    "执业资格证明",
    "资格证明",
    "类似项目名称",
    "企业资质",
    "营业执照号",
    "中小企业声明函",
)


@pytest.mark.parametrize("title", NON_CHAPTERS)
def test_forms_are_not_chapters_even_with_stale_unknown_kind(title):
    assert not is_outline_chapter(OutlineItem(title=title, kind="unknown", level=1))


def test_recognition_marks_forms_as_non_chapters_without_deleting_fill_data():
    _, items = extract_outline(
        "第六章 投标文件格式\n一、投标函\n致：采购人\n"
        "二、技术方案\n（一）施工组织设计\n说明方案内容。"
    )
    assert items
    for item in items:
        if "投标函" in item.title:
            assert item.tocEligible is False
        assert item.tocEligible == is_outline_chapter(item)


def _case():
    doc = Document()
    doc.add_paragraph("原版封面：投标文件")
    doc.add_page_break()
    doc.add_page_break()  # A duplicate break from an old preview must not make a blank page.
    doc.add_paragraph("目录")
    for text in ("资格文件", *NON_CHAPTERS, "技术文件"):
        doc.add_paragraph(text).add_run().add_tab()
    doc.add_page_break()
    doc.add_paragraph("第一部分资格文件", style="Heading 1")
    doc.add_paragraph("（一）资格审查说明", style="Heading 2")
    doc.add_paragraph("资格审查说明正文。")
    for title in NON_CHAPTERS:
        doc.add_paragraph(title, style="Heading 2")
        doc.add_paragraph("原版表单正文必须保留。")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "安装实施"
    table.cell(0, 1).text = "营业执照号"
    doc.add_page_break()
    doc.add_paragraph("第二部分技术文件", style="Heading 1")
    doc.add_paragraph("（一）安装实施", style="Heading 2")
    doc.add_paragraph("安装正文。")
    doc.add_paragraph("（二）调试验收", style="Heading 2")
    doc.add_paragraph("验收正文。")
    items = [
        OutlineItem(id="qualification", title="资格文件", rawTitle="第一部分资格文件", level=1),
        OutlineItem(
            id="qualification-note", title="资格审查说明", rawTitle="（一）资格审查说明", level=2
        ),
        *(OutlineItem(id=f"form-{i}", title=t) for i, t in enumerate(NON_CHAPTERS)),
        OutlineItem(id="technology", title="技术文件", rawTitle="第二部分技术文件", level=1),
        OutlineItem(
            id="install", title="安装实施", rawTitle="（一）安装实施", level=2, kind="tech_chapter"
        ),
        OutlineItem(
            id="accept", title="调试验收", rawTitle="（二）调试验收", level=2, kind="tech_chapter"
        ),
    ]
    return doc, BidBrief(
        outlineChapter="第六章 投标文件格式", layoutMode="outline", outlineItems=items
    )


def _assert_directory(doc):
    rows = [p for p in doc.paragraphs if _pageref_bookmark(p)]
    # Original TOC rows are authoritative; unlisted body subheadings stay out.
    assert [p.text.split("\t")[0] for p in rows] == [
        "资格文件", *NON_CHAPTERS, "技术文件",
    ]
    assert all(p.text.rsplit("\t", 1)[-1].isdigit() for p in rows)
    assert all("待更新" not in p.text for p in rows)
    starts = list(doc.element.body.iter(qn("w:bookmarkStart")))
    names = [n.get(qn("w:name")) for n in starts]
    ids = [n.get(qn("w:id")) for n in starts]
    assert len(names) == len(set(names)) and len(ids) == len(set(ids))
    assert all(_pageref_bookmark(row) in names for row in rows)
    body = list(doc.element.body)
    tail = max(body.index(row._p) for row in rows)
    real_names = {
        n.get(qn("w:name")) for el in body[tail + 1 :] for n in el.iter(qn("w:bookmarkStart"))
    }
    assert all(_pageref_bookmark(row) in real_names for row in rows)
    _, region = _toc_region(doc)
    assert not any(p.text == "第一部分资格文件" for p in region)
    assert all(
        el not in pdf_format.toc_block_ids(doc)
        for el in body[tail + 1 :]
        if "".join(n.text or "" for n in el.iter(qn("w:t"))) == "第一部分资格文件"
    )
    instructions = "".join(n.text or "" for n in doc.element.body.iter(qn("w:instrText")))
    assert r"TOC \f N " in instructions and r"TOC \o" not in instructions
    return rows


def test_native_toc_has_only_numbered_chapters_with_resolved_body_anchors():
    doc, brief = _case()
    before_tables = [t._tbl.xml for t in doc.tables]
    native_format.ensure_toc_rows(doc, brief)
    _assert_directory(doc)
    assert before_tables == [t._tbl.xml for t in doc.tables]
    assert all(title in [p.text for p in doc.paragraphs] for title in NON_CHAPTERS)
    assert len(native_format.body_headings(doc, brief)) == 5 + len(NON_CHAPTERS)
    assert all(el.tag == qn("w:p") for el, _, _ in native_format.body_headings(doc, brief))
    title = next(p for p in doc.paragraphs if p.text == "目录")
    before = next(i for i, p in enumerate(doc.paragraphs) if p._p is title._p)
    assert not any(
        n.get(qn("w:type")) == "page"
        for p in doc.paragraphs[:before]
        for n in p._p.iter(qn("w:br"))
    )


def test_repeated_native_toc_and_full_unification_keep_references_and_bookmarks_stable(tmp_path):
    doc, brief = _case()
    native_format.ensure_toc_rows(doc, brief)
    original = doc.element.body.xml
    for _ in range(3):
        native_format.ensure_toc_rows(doc, brief)
        _assert_directory(doc)
        assert doc.element.body.xml == original
    path = tmp_path / "full.docx"
    doc.save(path)
    pdf_format.unify_full_docx(path, brief)
    once = Document(path).element.body.xml
    for _ in range(2):
        pdf_format.unify_full_docx(path, brief)
        out = Document(path)
        _assert_directory(out)
        assert out.element.body.xml == once


def test_one_entry_directory_stops_before_body_heading_with_page_break():
    doc = Document()
    doc.add_paragraph("目录")
    entry = doc.add_paragraph("技术文件")
    entry.add_run().add_tab()
    head = doc.add_paragraph("技术文件", style="Heading 1")
    head.paragraph_format.page_break_before = True
    assert head._p not in pdf_format.toc_block_ids(doc)
    assert head._p not in [p._p for p in _toc_region(doc)[1]]


FORMAT_TOC = (
    "一、投标函及投标函附录",
    "（一）投标函",
    "（二）投标函附录",
    "二、法定代表人身份证明",
    "三、授权委托书",
    "四、投标保证金",
    "五、已标价工程量清单",
    "六、项目管理机构",
    "（一）项目管理机构组成表",
    "（二）主要人员简历表",
    "（三）承诺书",
    "七、拟分包项目情况表",
    "八、资格审查资料",
    "（一）投标人基本情况表",
    "（二）近年财务状况表",
    "（三）近年完成的类似项目情况表",
    "（四）正在施工和新承接的项目情况表",
    "（五）近年发生的诉讼及仲裁情况",
    "九、其他资料",
    "十、施工组织格式",
)


def test_format_directory_keeps_every_entry_as_chapter():
    text = (
        "第八章 投标文件格式\n" + "\n".join(FORMAT_TOC) + "\n一、投标函及投标函附录\n致：采购人\n"
    )
    _, items = extract_outline(text)
    titles = [item.rawTitle or item.title for item in items]
    assert len(items) >= 20
    assert all(item.tocEligible for item in items)
    assert titles[0].startswith("一、") and "投标函及投标函附录" in titles[0]
    child = next(
        item
        for item in items
        if (item.rawTitle or "").startswith("（一）") and item.title == "投标函"
    )
    assert child.level == 2 and is_outline_chapter(child)
    org = next(item for item in items if "项目管理机构组成表" in (item.rawTitle or item.title))
    assert org.level == 2
    qual = next(item for item in items if item.title == "资格审查资料")
    assert qual.level == 1 and is_outline_chapter(qual)
    assert any(item.title == "施工组织格式" for item in items)


def test_format_toc_keeps_original_rows_when_outline_only_has_qualification():
    doc = Document()
    doc.add_paragraph("投标文件")
    doc.add_paragraph("目录")
    for text in FORMAT_TOC:
        doc.add_paragraph(text).add_run().add_tab()
    doc.add_page_break()
    for text in FORMAT_TOC:
        head = doc.add_paragraph(text, style="Heading 1")
        if re.match(r"^[一二三四五六七八九十]+、", text):
            head.paragraph_format.page_break_before = True
        doc.add_paragraph("本节正文。")
    brief = BidBrief(
        outlineChapter="第八章 投标文件格式",
        layoutMode="outline",
        outlineItems=[
            OutlineItem(id="qual", title="资格审查资料", rawTitle="八、资格审查资料", level=1)
        ],
    )
    native_format.ensure_toc_rows(doc, brief)
    rows = [p for p in doc.paragraphs if _pageref_bookmark(p)]
    assert [p.text.split("\t")[0] for p in rows] == list(FORMAT_TOC)
    pages = [p.text.rsplit("\t", 1)[-1] for p in rows]
    assert all(page.isdigit() for page in pages)
    assert all("待更新" not in p.text for p in rows)
    assert int(pages[-1]) > int(pages[0])
    names = {item.title for item in brief.outlineItems}
    assert {"投标函及投标函附录", "法定代表人身份证明", "授权委托书", "施工组织格式"} <= names
    again = [p.text for p in rows]
    native_format.ensure_toc_rows(doc, brief)
    rows = [p for p in doc.paragraphs if _pageref_bookmark(p)]
    assert [p.text for p in rows] == again


def test_page_cache_uses_body_origin_instead_of_first_later_chapter():
    doc = Document()
    doc.add_paragraph("投标文件封面")
    doc.add_paragraph("投标函", style="Heading 1")
    doc.add_paragraph("原版函件内容。")
    doc.add_page_break()
    doc.add_paragraph("技术方案", style="Heading 1")
    doc.add_paragraph("技术正文。")
    brief = BidBrief(
        outlineItems=[
            OutlineItem(id="form", title="投标函", kind="letter"),
            OutlineItem(id="tech", title="技术方案", kind="tech_plan"),
        ]
    )
    native_format.ensure_toc_rows(doc, brief)
    rows = [p for p in doc.paragraphs if _pageref_bookmark(p)]
    assert len(rows) == 1 and rows[0].text.endswith("\t2")


def test_response_directory_keeps_quote_last_when_last_row_has_page_break():
    """原版五条目录：末条分页不能把分项报价表当成正文第一条。"""
    titles = (
        "一、资格资信证明文件",
        "二、合同条款响应书",
        "三、技术和商务差异表",
        "四、响应方案",
        "五、分项报价表",
    )
    body = (
        "一、资格、资信证明文件",
        "A.公司简介",
        "二、合同条款响应书",
        "三、商务和技术偏差表",
        "四、响应方案",
        "五、分项报价表",
    )
    doc = Document()
    doc.add_paragraph("响应文件")
    doc.add_paragraph("目录")
    for i, text in enumerate(titles):
        para = doc.add_paragraph(text)
        if i == len(titles) - 1:
            para.paragraph_format.page_break_before = True
    for text in body:
        head = doc.add_paragraph(text)
        if re.match(r"^[一二三四五六七八九十]、", text):
            head.paragraph_format.page_break_before = True
        doc.add_paragraph("本节正文。")
    # 报价在大纲最前，模拟内置排序；目录仍按原版
    brief = BidBrief(
        outlineChapter="第五章响应文件格式",
        layoutMode="outline",
        outlineItems=[
            OutlineItem(
                id="quote",
                title="分项报价表",
                rawTitle="五、分项报价表",
                kind="quote",
                level=1,
                tocEligible=True,
            ),
            OutlineItem(
                id="qual",
                title="资格、资信证明文件",
                rawTitle="一、资格、资信证明文件",
                level=1,
                tocEligible=True,
            ),
            OutlineItem(
                id="intro",
                title="公司简介",
                rawTitle="A.公司简介",
                level=2,
                tocEligible=True,
            ),
            OutlineItem(
                id="contract",
                title="合同条款响应书",
                rawTitle="二、合同条款响应书",
                kind="commitment_copy",
                level=1,
                tocEligible=True,
            ),
            OutlineItem(
                id="diff",
                title="商务和技术偏差表",
                rawTitle="三、商务和技术偏差表",
                kind="tech_dev",
                level=1,
                tocEligible=True,
            ),
            OutlineItem(
                id="plan",
                title="响应方案",
                rawTitle="四、响应方案",
                kind="tech_plan",
                level=1,
                tocEligible=True,
            ),
        ],
    )
    native_format.ensure_toc_rows(doc, brief)
    rows = [p for p in doc.paragraphs if _pageref_bookmark(p)]
    assert [p.text.split("\t")[0] for p in rows] == list(titles)
    pages = [int(p.text.rsplit("\t", 1)[-1]) for p in rows]
    assert pages == sorted(pages) and pages[-1] > pages[0]
    body_heads = [p.text.strip() for p in doc.paragraphs if p.text.strip() in body]
    assert body_heads == list(body)
    assert sum(p.text.strip() == "五、分项报价表" for p in doc.paragraphs) == 1
