"""按组卷大纲组装投标 Word：目录跟随大纲，未知函/表复制招标书原文并只填空。"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from api.services.tenders.document import (
    _add_bottom_sign_spacer,
    _bookmark_paragraph,
    _ensure_header,
    _pin_cjk_fonts,
    _write_toc_line,
)
from api.services.tenders.format_rules import (
    apply_footer_page_number,
    apply_section_page,
    format_brief_notes,
    section_page_flags,
    toc_item_label,
    toc_page_cache,
)
from api.services.tenders.modules import COPY_KINDS, MODULE_KINDS, render_module
from api.services.tenders.outline import compact_title, fill_copy_blanks, looks_like_form_template
from api.services.tenders.placeholders import (
    append_placeholder_section,
    collect_slots,
    draw_placeholder_box,
)
from api.services.tenders.schema import BidBrief, DocumentFormat, OutlineItem, PlaceholderItem
from api.services.tenders.slots import attachments_for_slots

logger = logging.getLogger("api.tenders.assemble")

_SONG = "宋体"
# 函/授权等可沿用招标书空白稿版式；报价/偏离/业绩仍走结构化模块。
_FORM_LAYOUT_KINDS = frozenset({"letter", "auth", "factory"})
_SIGN_TOKEN = re.compile(
    r"(投标人|供应商|法定代表人|授权代表|委托代理人|联系人|联系电话|电话|地址|日期|（章）|\(章\)|签字)"
)
_ATTACH_LINE = re.compile(
    r"^(附件[（(]?[一二三四五六七八九十0-9]+[)）]?[：:]?)(.*)$"
)
_INLINE_FORMAT_HEAD = re.compile(
    r"第[一二三四五六七八九十0-9]+[章节部分][^。\n]{0,24}(?:投标文件格式|响应文件格式)"
)


def assemble_bid_docx(
    brief: BidBrief,
    dest: Path,
    *,
    qualification_pdf: Path | None = None,
    catalog_slots: list | None = None,
    catalog_media: dict | None = None,
) -> tuple[Path, list[str]]:
    warnings: list[str] = []
    # 目录与正文都按 outlineItems 当前顺序；跳过项不入卷。前端可重排该数组。
    items = [
        item
        for item in (brief.outlineItems or [])
        if not item.skipped and (item.kind or "").strip() not in {"", "unknown"}
    ]
    if not items:
        warnings.append("组卷大纲为空或已全部跳过，未按大纲生成")
        from api.services.tenders.document import build_bid_docx

        return build_bid_docx(
            brief,
            dest,
            qualification_pdf=qualification_pdf,
            catalog_slots=catalog_slots,
            catalog_media=catalog_media,
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    fmt = brief.documentFormat or DocumentFormat()
    doc = Document()
    apply_section_page(doc.sections[0], fmt)
    _pin_cjk_fonts(doc)
    _write_cover(doc, brief, fmt)
    _add_next_section(doc, fmt)
    bookmarks = [f"toc_{(item.id or f'o{i + 1:02d}')}" for i, item in enumerate(items)]
    _write_toc(doc, items, brief.outlineChapter, fmt, bookmarks)
    body_section_index = 1
    if fmt.pageNumberStart == "body":
        _add_next_section(doc, fmt)
        body_section_index = 2
    copied = 0
    generated = 0
    title_only: list[str] = []
    for i, item in enumerate(items):
        if not (i == 0 and fmt.pageNumberStart == "body"):
            doc.add_page_break()
        kind = (item.kind or "unknown").strip() or "unknown"
        source = (item.source or "").strip() or "copy"
        raw_body = (item.body or "").strip()
        keep_form = kind in _FORM_LAYOUT_KINDS and looks_like_form_template(raw_body)
        use_copy = source == "copy" or kind in COPY_KINDS or keep_form
        use_module = (not use_copy) and kind in MODULE_KINDS
        bm = bookmarks[i]
        if use_module:
            _write_heading(doc, item.title, fmt, bm)
            for note in render_module(doc, brief, item):
                if note not in warnings:
                    warnings.append(note)
            generated += 1
            continue
        if kind == "scan" and not use_copy:
            _write_heading(doc, item.title, fmt, bm)
            draw_placeholder_box(
                doc,
                PlaceholderItem(key=item.id or "scan", title=item.title, hint="装订时附原件"),
            )
            continue
        contact = (brief.agentName or "").strip() or (brief.legalPersonName or "").strip()
        filled = fill_copy_blanks(
            raw_body,
            bidder=brief.bidderName,
            project=brief.projectName,
            tenderer=brief.tenderer,
            legal=brief.legalPersonName,
            bid_date=brief.bidDate,
            phone=brief.bidderPhone,
            address=brief.bidderAddress,
            contact=contact,
            delivery_days=brief.deliveryDays,
            validity_days=brief.bidValidityDays,
            quality=brief.quality,
        ).strip()
        if filled:
            copied_ok = False
            if keep_form or looks_like_form_template(filled):
                before = _visible_text_len(doc)
                start = len(doc.paragraphs)
                _write_copied_form(doc, filled, fmt)
                copied_ok = _visible_text_len(doc) - before >= 20
                if copied_ok:
                    _bookmark_first_new(doc, start, bm)
            if copied_ok:
                copied += 1
                continue
            if kind in MODULE_KINDS and source != "copy" and kind not in COPY_KINDS:
                _write_heading(doc, item.title, fmt, bm)
                for note in render_module(doc, brief, item):
                    if note not in warnings:
                        warnings.append(note)
                generated += 1
                continue
            _write_heading(doc, item.title, fmt, bm)
            _write_body(doc, filled, fmt)
            copied += 1
            continue
        title_only.append(item.title)
        _write_heading(doc, item.title, fmt, bm)
        if kind == "commitment_copy":
            warnings.append(
                f"「{item.title}」未从招标书复制到原文，未使用充电桩固定承诺书，请核对后手工补"
            )
        elif kind == "scan":
            draw_placeholder_box(
                doc,
                PlaceholderItem(key=item.id or "scan", title=item.title, hint="装订时附原件"),
            )
        else:
            warnings.append(f"「{item.title}」招标书格式章未提供本页空白稿，仅保留标题")
    if generated:
        warnings.append(f"已按组卷大纲用模块填写 {generated} 节")
    if copied:
        warnings.append(f"已按组卷大纲复制 {copied} 节招标书原文并保留格式，仅填入单位、项目和日期")
    if title_only:
        warnings.append("仅保留标题、未复制到正文：" + "、".join(title_only[:12]))
    warnings.extend(format_brief_notes(fmt))
    if fmt.coverNeedSeal:
        warnings.append("招标书要求封面加盖公章，请在打印后于封面预留处盖章")

    if brief.includePlaceholders:
        slots = (
            catalog_slots if catalog_slots is not None else collect_slots(brief.extraPlaceholders)
        )
        media = catalog_media if catalog_media is not None else attachments_for_slots(slots)
        n_filled, n_boxes, insert_notes = append_placeholder_section(doc, slots, media)
        if n_boxes:
            warnings.append(f"资料库扫描件 {n_boxes} 处用虚线框占位")
        for note in insert_notes:
            if note not in warnings:
                warnings.append(note)

    if qualification_pdf and qualification_pdf.is_file() and brief.attachQualifications:
        from api.services.tenders.document import _append_pdf_pages

        pages = _append_pdf_pages(doc, qualification_pdf)
        if pages:
            warnings.append(f"已插入资质文件 {pages} 页扫描件")
            warnings.append("资质彩页/扫描件未单独编页，装订时请按招标书对图纸、彩页的约定处理")

    for si, section in enumerate(doc.sections):
        numbered, restart = section_page_flags(si, fmt, body_section=body_section_index)
        _ensure_header(section, brief.projectName, cover=(si == 0))
        apply_footer_page_number(section, fmt, numbered=numbered, restart=restart)

    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dest))
    return dest, warnings


def _add_next_section(doc: Document, fmt: DocumentFormat):
    section = doc.add_section(WD_SECTION.NEW_PAGE)
    apply_section_page(section, fmt)
    return section


def _bookmark_first_new(doc: Document, start: int, name: str) -> None:
    for para in doc.paragraphs[start:]:
        if (para.text or "").strip():
            _bookmark_paragraph(para, name)
            return


def _write_cover(doc: Document, brief: BidBrief, fmt: DocumentFormat) -> None:
    font = fmt.fontName
    for _ in range(4):
        doc.add_paragraph()
    if fmt.coverShowProject:
        title = doc.add_paragraph()
        title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _run(
            title,
            (brief.projectName or "投标项目").strip() or "投标项目",
            size=fmt.coverTitleSizePt,
            bold=True,
            font=font,
        )
    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(sub, "投 标 文 件", size=fmt.coverDocSizePt, bold=True, font=font)
    if fmt.coverShowCopyMark:
        mark = doc.add_paragraph()
        mark.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _run(mark, fmt.coverCopyMark or "正本", size=16, bold=True, font=font)
    # 标题留在上半页；招标人/投标人/日期落到封面下半区，不贴页脚。
    _add_bottom_sign_spacer(doc, sign_cm=3.8, bottom_pad_cm=3.6)
    if fmt.coverShowTenderNo:
        no = (brief.tenderNo or "").strip() or "—"
        line = doc.add_paragraph()
        line.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _run(line, f"招标编号：{no}", size=14, font=font)
    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(meta, f"招标人：{(brief.tenderer or '').strip() or '—'}", size=14, font=font)
    if fmt.coverShowBidder:
        meta2 = doc.add_paragraph()
        meta2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _run(meta2, f"投标人：{(brief.bidderName or '').strip() or '—'}", size=14, font=font)
    if fmt.coverShowDate:
        meta3 = doc.add_paragraph()
        meta3.alignment = WD_ALIGN_PARAGRAPH.CENTER
        date_cn = fill_copy_blanks(
            "年 月 日",
            bidder="",
            project="",
            tenderer="",
            legal="",
            bid_date=brief.bidDate,
        )
        _run(meta3, date_cn, size=14, font=font)
    if fmt.coverNeedSeal:
        seal = doc.add_paragraph()
        seal.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _run(seal, "（封面加盖公章）", size=12, font=font)


def _write_toc(
    doc: Document,
    items: list[OutlineItem],
    chapter: str,
    fmt: DocumentFormat,
    bookmarks: list[str],
) -> None:
    del chapter
    head = doc.add_paragraph()
    head.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(head, "目录", size=fmt.tocTitleSizePt, bold=True, font=fmt.fontName)
    for i, item in enumerate(items):
        para = doc.add_paragraph()
        label = toc_item_label(i, fmt.tocNumbering)
        if not fmt.tocNeedPageNos:
            pf = para.paragraph_format
            pf.space_before = Pt(2)
            pf.space_after = Pt(2)
            _run(para, f"{label}{item.title}", size=fmt.tocItemSizePt, font=fmt.fontName)
            continue
        _write_toc_line(
            para,
            i,
            item.title,
            bookmarks[i],
            toc_page_cache(i, fmt),
            label=label,
        )


def _write_heading(doc: Document, title: str, fmt: DocumentFormat, bookmark: str | None = None):
    para = doc.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pf = para.paragraph_format
    pf.space_before = Pt(6)
    pf.space_after = Pt(12)
    _run(
        para,
        (title or "").strip() or "附件",
        size=fmt.headingSizePt,
        bold=True,
        font=fmt.fontName,
    )
    if bookmark:
        _bookmark_paragraph(para, bookmark)
    return para


def _write_copied_form(doc: Document, text: str, fmt: DocumentFormat) -> None:
    """按招标书空白稿的行角色还原标题、缩进和落款，而不是整页同一缩进。"""
    body = fmt.bodySizePt
    font = fmt.fontName
    for block in _paragraphs_from_body(text):
        block = _strip_inline_format_head(block)
        role = _form_line_role(block)
        if role == "skip":
            continue
        if role == "attach":
            matched = _ATTACH_LINE.match(block.strip())
            label = (matched.group(1) if matched else block).strip()
            rest = (matched.group(2) if matched else "").strip()
            _form_para(doc, label, size=body, bold=False, align="left", space_after=2, font=font)
            if rest:
                _form_para(
                    doc,
                    _spaced_title(rest),
                    size=max(body, 18),
                    bold=True,
                    align="center",
                    space_before=8,
                    space_after=12,
                    font=font,
                )
            continue
        if role == "title":
            _form_para(
                doc,
                _spaced_title(block),
                size=max(body, 18),
                bold=True,
                align="center",
                space_before=10,
                space_after=14,
                font=font,
            )
            continue
        if role == "salute":
            _form_para(doc, block, size=body, align="left", space_after=8, font=font)
            continue
        if role == "sign_row":
            _write_sign_columns(doc, _split_sign_columns(block) or [block], fmt)
            continue
        if role == "sign":
            _form_para(
                doc,
                block,
                size=body,
                align="left",
                space_after=2,
                line_spacing=1.15,
                font=font,
            )
            continue
        _form_para(
            doc,
            block,
            size=body,
            align="justify",
            indent=True,
            space_after=6,
            line_spacing=1.5,
            font=font,
        )


def _strip_inline_format_head(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    stripped, n = _INLINE_FORMAT_HEAD.subn("", raw, count=1)
    if n == 0:
        return raw
    return stripped.strip(" ：:、;；") or raw


def _visible_text_len(doc: Document) -> int:
    n = sum(len((p.text or "").strip()) for p in doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                n += len((cell.text or "").strip())
    return n


def _form_line_role(line: str) -> str:
    raw = (line or "").strip()
    if not raw or re.fullmatch(r"[-—–_＿]{6,}", raw):
        return "skip"
    n = compact_title(raw)
    if len(n) <= 40 and (n.endswith("投标文件") or "投标文件格式" in n or "响应文件格式" in n):
        return "skip"
    attach = _ATTACH_LINE.match(raw)
    if attach and len(n) <= 48:
        return "attach"
    compact = compact_title(raw)
    if (
        "：" not in raw
        and ":" not in raw
        and 2 <= len(compact) <= 8
        and re.fullmatch(r"[\u4e00-\u9fff]+", compact)
        and (compact.endswith(("函", "书")) or compact in {"竞标书"})
    ):
        return "title"
    if _split_sign_columns(raw):
        return "sign_row"
    if raw.startswith("致") or (
        (raw.endswith("：") or raw.endswith(":"))
        and ("有限公司" in raw or "致" in raw)
        and len(compact) <= 40
    ):
        return "salute"
    if _SIGN_TOKEN.search(raw) and len(compact) <= 48:
        return "sign"
    if re.search(r"年\S{0,6}月\S{0,6}日", raw) and len(compact) <= 24:
        return "sign"
    return "body"


def _spaced_title(text: str) -> str:
    compact = compact_title(text)
    if re.fullmatch(r"(?:\S\s)+\S", (text or "").strip()):
        return (text or "").strip()
    if (
        2 <= len(compact) <= 4
        and re.fullmatch(r"[\u4e00-\u9fff]+", compact)
        and compact.endswith(("函", "书"))
    ):
        return " ".join(compact)
    return (text or "").strip()


def _split_sign_columns(line: str) -> list[str] | None:
    parts = [p.strip() for p in re.split(r"\s{2,}", line or "") if p.strip()]
    if len(parts) < 2:
        return None
    hits = sum(1 for p in parts if _SIGN_TOKEN.search(p) or re.search(r"年\S{0,4}月", p))
    if hits < 2:
        return None
    if len(parts) == 2:
        return parts
    return ["  ".join(parts[:-1]), parts[-1]]


def _write_sign_columns(doc: Document, cols: list[str], fmt: DocumentFormat) -> None:
    table = doc.add_table(rows=1, cols=max(2, len(cols)))
    table.autofit = True
    _set_table_no_borders(table)
    row = table.rows[0]
    for i, text in enumerate(cols[: len(row.cells)]):
        cell = row.cells[i]
        cell.text = ""
        para = cell.paragraphs[0]
        para.alignment = WD_ALIGN_PARAGRAPH.LEFT
        pf = para.paragraph_format
        pf.space_before = Pt(0)
        pf.space_after = Pt(2)
        pf.line_spacing = 1.15
        _run(para, text, size=fmt.bodySizePt, bold=False, font=fmt.fontName)


def _set_table_no_borders(table) -> None:
    tbl = table._tbl
    tbl_pr = tbl.tblPr
    if tbl_pr is None:
        tbl_pr = OxmlElement("w:tblPr")
        tbl.insert(0, tbl_pr)
    for old in list(tbl_pr.findall(qn("w:tblBorders"))):
        tbl_pr.remove(old)
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "nil")
        el.set(qn("w:sz"), "0")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), "auto")
        borders.append(el)
    tbl_pr.append(borders)


def _form_para(
    doc: Document,
    text: str,
    *,
    size: float = 12,
    bold: bool = False,
    align: str = "left",
    indent: bool = False,
    space_before: float = 0,
    space_after: float = 6,
    line_spacing: float = 1.5,
    font: str = _SONG,
):
    para = doc.add_paragraph()
    if align == "center":
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif align == "justify":
        para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    else:
        para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    pf = para.paragraph_format
    pf.space_before = Pt(space_before)
    pf.space_after = Pt(space_after)
    pf.line_spacing = line_spacing
    if indent:
        pf.first_line_indent = Cm(0.74)
    _run(para, text, size=size, bold=bold, font=font)
    return para


def _write_body(doc: Document, text: str, fmt: DocumentFormat) -> None:
    for block in _paragraphs_from_body(text):
        para = doc.add_paragraph()
        pf = para.paragraph_format
        pf.space_before = Pt(0)
        pf.space_after = Pt(6)
        pf.line_spacing = 1.5
        first = para.paragraph_format
        first.first_line_indent = Cm(0.74)
        _run(para, block, size=fmt.bodySizePt, font=fmt.fontName)


def _paragraphs_from_body(text: str) -> list[str]:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [ln.strip() for ln in raw.split("\n")]
    lines = [ln for ln in lines if ln]
    if len(lines) <= 1 and len(raw) > 240:
        parts = re.split(r"(?<=[。；;])", raw)
        return [p.strip() for p in parts if p.strip()]
    return lines


def _run(para, text: str, *, size: float = 12, bold: bool = False, font: str = _SONG) -> None:
    name = font or _SONG
    run = para.add_run(text or "")
    run.bold = bold
    run.font.size = Pt(size)
    run.font.name = name
    run.font.color.rgb = RGBColor(0, 0, 0)
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:ascii"), name)
    rfonts.set(qn("w:hAnsi"), name)
    rfonts.set(qn("w:eastAsia"), name)
    proof = OxmlElement("w:noProof")
    rpr.append(proof)
