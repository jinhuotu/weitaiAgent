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
    _apply_toc_page_numbers,
    _bookmark_paragraph,
    _ensure_header,
    _estimate_bookmark_pages,
    _p_sectpr,
    _paragraph_has_page_break,
    _pin_cjk_fonts,
    _rewrite_date_line,
    _rewrite_labeled_underline,
    _sect_starts_new_page,
    _write_toc_line,
)
from api.services.tenders.format_rules import (
    apply_footer_page_number,
    apply_section_page,
    flatten_toc_tree,
    format_brief_notes,
    section_page_flags,
    toc_page_cache,
)
from api.services.tenders.modules import COPY_KINDS, MODULE_KINDS, is_seal_register, render_module
from api.services.tenders.outline import (
    build_outline_toc_tree,
    compact_title,
    dedupe_outline_items,
    drop_ocr_junk_lines,
    fill_copy_blanks,
    items_for_volume,
    looks_like_form_template,
    split_mashed_zhi_line,
    unfold_form_sign_lines,
)
from api.services.tenders.placeholders import (
    append_placeholder_section,
    collect_slots,
    draw_placeholder_box,
    id_slot,
    inline_id_scans,
)
from api.services.tenders.schema import BidBrief, DocumentFormat, OutlineItem, PlaceholderItem
from api.services.tenders.slots import attachments_for_slots

logger = logging.getLogger("api.tenders.assemble")

_SONG = "宋体"
# 函/授权/承诺书沿用招标书空白稿版式；报价/偏离/业绩仍走结构化模块。
_FORM_LAYOUT_KINDS = frozenset({"letter", "auth", "factory", "commitment_copy"})
_SIGN_LABEL = re.compile(
    r"^(投标人名称|供应商名称|投标人|供应商|法定代表人或授权代表|"
    r"法定代表人或其委托代理人|法定代表人或委托代理人|法定代表人|"
    r"委托代理人|授权代表|联系人|联系电话|电话|地址|住址)"
    r"(?:[（(]([^)）]*)[)）])?[：:]*(.*)$"
)
_SIGN_TOKEN = re.compile(
    r"(投标人|供应商|法定代表人|授权代表|委托代理人|联系人|联系电话|电话|地址|日期|（章）|\(章\)|签字)"
)
_ATTACH_LINE = re.compile(
    r"^(附件[（(]?[一二三四五六七八九十0-9]+[)）]?[：:]?)(.*)$"
)
_QUAL_PACK_MARKS = ("企业资质", "资质证明", "资质文件", "资格审查资料", "资格证明资料")
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
    volume: str | None = None,
) -> tuple[Path, list[str]]:
    warnings: list[str] = []
    vol = (volume or "").strip()
    if vol in {"business", "technical"}:
        items = items_for_volume(brief, vol)
    else:
        # 目录与正文都按 outlineItems 当前顺序；跳过项不入卷。前端可重排该数组。
        items = [
            item
            for item in (brief.outlineItems or [])
            if not item.skipped and (item.kind or "").strip() not in {"", "unknown"}
        ]
        items = dedupe_outline_items(items)
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
    _write_cover(doc, brief, fmt, volume=vol)
    _add_next_section(doc, fmt)
    bookmarks = [f"toc_{(item.id or f'o{i + 1:02d}')}" for i, item in enumerate(items)]
    _write_toc(doc, items, brief, fmt, bookmarks)
    body_section_index = 1
    if fmt.pageNumberStart == "body":
        _add_next_section(doc, fmt)
        body_section_index = 2
    copied = 0
    generated = 0
    title_only: list[str] = []
    slots = catalog_slots if catalog_slots is not None else collect_slots(brief.extraPlaceholders)
    media = catalog_media if catalog_media is not None else attachments_for_slots(slots)
    inlined_ids: set[str] = set()
    qual_tried = False
    for i, item in enumerate(items):
        if not (i == 0 and fmt.pageNumberStart == "body"):
            _chapter_break(doc)
        kind = (item.kind or "unknown").strip() or "unknown"
        source = (item.source or "").strip() or "copy"
        raw_body = (item.body or "").strip()
        if brief.attachQualifications and not qual_tried and _is_qual_pack_item(item) and vol != "technical":
            from api.services.tenders.document import qualification_attach_notes

            _write_heading(doc, item.title, fmt, bookmarks[i])
            notes = qualification_attach_notes(
                doc, qualification_pdf, title=False, leading_break=False
            )
            for note in notes:
                if note not in warnings:
                    warnings.append(note)
            if not any("已插入资质文件" in n for n in notes):
                draw_placeholder_box(
                    doc,
                    PlaceholderItem(key=item.id or "scan", title=item.title, hint="装订时附原件"),
                )
            qual_tried = True
            continue
        keep_form = kind in _FORM_LAYOUT_KINDS and looks_like_form_template(raw_body)
        # 身份证明从 PDF 抽出来会拆成「成立时 / 间 / 年 / 月 / 日」，用模块排版
        # 印鉴预留表是格子，OCR 粘成一行，不能当正文复制
        use_copy = (
            kind != "legal_id"
            and not is_seal_register(item)
            and (source == "copy" or kind in COPY_KINDS or keep_form)
        )
        use_module = (not use_copy) and kind in MODULE_KINDS
        bm = bookmarks[i]
        if use_module:
            _write_heading(doc, item.title, fmt, bm)
            for note in render_module(doc, brief, item, media=media, inlined=inlined_ids):
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
                if kind == "auth":
                    _inline_copied_id(
                        doc,
                        start=start,
                        key="id_agent",
                        media=media,
                        inlined=inlined_ids,
                        has_agent=bool((brief.agentName or "").strip()),
                    )
                copied += 1
                continue
            if kind in MODULE_KINDS and source != "copy" and kind not in COPY_KINDS:
                _write_heading(doc, item.title, fmt, bm)
                for note in render_module(doc, brief, item, media=media, inlined=inlined_ids):
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
            from api.services.tenders.commitment import write_commitment_body

            write_commitment_body(doc, brief)
            warnings.append(f"「{item.title}」未抽到招标书原文，已按常用承诺条款填写，请对照邀请书核对")
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
        from api.services.tenders.categories import slot_volume

        attach = []
        for s in slots:
            key = (s.key or "").strip()
            if key in inlined_ids:
                continue
            if vol in {"business", "technical"} and slot_volume(key, s.title) != vol:
                continue
            attach.append(s)
        if attach:
            n_filled, n_boxes, insert_notes = append_placeholder_section(
                doc, attach, media, heading=vol != "technical"
            )
        else:
            n_filled, n_boxes, insert_notes = 0, 0, []
        if n_boxes:
            warnings.append(f"资料库扫描件 {n_boxes} 处用虚线框占位")
        for note in insert_notes:
            if note not in warnings:
                warnings.append(note)

    if brief.attachQualifications and not qual_tried and vol != "technical":
        from api.services.tenders.document import qualification_attach_notes

        for note in qualification_attach_notes(doc, qualification_pdf):
            if note not in warnings:
                warnings.append(note)

    for si, section in enumerate(doc.sections):
        numbered, restart = section_page_flags(si, fmt, body_section=body_section_index)
        _ensure_header(section, brief.projectName, cover=(si == 0))
        apply_footer_page_number(section, fmt, numbered=numbered, restart=restart)

    _collapse_extra_page_breaks(doc)
    _fill_toc_pages(doc, fmt)

    dest.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(dest))
    return dest, warnings


def _add_next_section(doc: Document, fmt: DocumentFormat):
    section = doc.add_section(WD_SECTION.NEW_PAGE)
    apply_section_page(section, fmt)
    return section


def _clear_keep_next(p_el) -> None:
    p_pr = p_el.find(qn("w:pPr"))
    if p_pr is None:
        return
    for el in p_pr.findall(qn("w:keepNext")):
        p_pr.remove(el)


def _chapter_break(doc: Document) -> None:
    """上一节已经是分页/新节时不要再插一次，否则会多出一页只有页眉。"""
    body = list(doc.element.body)
    for child in reversed(body):
        if child.tag == qn("w:sectPr"):
            continue
        if child.tag == qn("w:tbl"):
            break
        if child.tag != qn("w:p"):
            continue
        if _paragraph_has_page_break(child):
            return
        sect = _p_sectpr(child)
        if sect is not None and _sect_starts_new_page(sect):
            return
        text = "".join(node.text or "" for node in child.iter(qn("w:t"))).replace("\u200b", "").strip()
        if text or _p_has_drawing(child):
            # 末段 keep_with_next 会把分页符粘走，整页只剩页眉
            _clear_keep_next(child)
            break
        _clear_keep_next(child)
    doc.add_page_break()


def _p_has_drawing(el) -> bool:
    return next(el.iter(qn("w:drawing")), None) is not None


def _is_qual_pack_item(item: OutlineItem) -> bool:
    kind = (item.kind or "").strip()
    if kind not in {"scan", "company"}:
        return False
    n = compact_title(item.title)
    return any(k in n for k in _QUAL_PACK_MARKS)


def _p_plain(el) -> str:
    return "".join(node.text or "" for node in el.iter(qn("w:t"))).replace("\u200b", "").strip()


def _is_blank_para(el) -> bool:
    return el.tag == qn("w:p") and not _p_plain(el) and not _p_has_drawing(el)


def _is_empty_break_para(el) -> bool:
    return _is_blank_para(el) and _paragraph_has_page_break(el)


def _strip_page_br(el) -> None:
    for run in list(el.iter(qn("w:r"))):
        for br in list(run.findall(qn("w:br"))):
            if br.get(qn("w:type")) == "page":
                run.remove(br)


def _set_el_page_break_before(el, on: bool = True) -> None:
    p_pr = el.find(qn("w:pPr"))
    if p_pr is None:
        p_pr = OxmlElement("w:pPr")
        el.insert(0, p_pr)
    for old in list(p_pr.findall(qn("w:pageBreakBefore"))):
        p_pr.remove(old)
    if on:
        node = OxmlElement("w:pageBreakBefore")
        node.set(qn("w:val"), "true")
        p_pr.append(node)


def _collapse_extra_page_breaks(doc: Document) -> None:
    """空段里的 w:br page 在预览里会单独占一页只剩页眉；改挂到下一节有内容的段落上。"""
    body = doc.element.body
    guard = 0
    while guard < 80:
        guard += 1
        target = None
        for child in list(body):
            if child.tag == qn("w:sectPr"):
                continue
            if _is_empty_break_para(child):
                target = child
                break
        if target is None:
            break
        nxt = target.getnext()
        while nxt is not None and nxt.tag == qn("w:p") and _is_blank_para(nxt):
            dead = nxt
            nxt = nxt.getnext()
            try:
                body.remove(dead)
            except ValueError:
                break
        if nxt is not None and nxt.tag == qn("w:p") and not _is_blank_para(nxt):
            _set_el_page_break_before(nxt, True)
            try:
                body.remove(target)
            except ValueError:
                break
            continue
        if nxt is not None and nxt.tag == qn("w:tbl"):
            _strip_page_br(target)
            _set_el_page_break_before(target, True)
            continue
        try:
            body.remove(target)
        except ValueError:
            break

    prev_new_page = False
    for child in list(body):
        if child.tag == qn("w:sectPr"):
            prev_new_page = False
            continue
        if child.tag == qn("w:tbl"):
            prev_new_page = False
            continue
        if child.tag != qn("w:p"):
            continue
        p_pr = child.find(qn("w:pPr"))
        has_before = p_pr is not None and p_pr.find(qn("w:pageBreakBefore")) is not None
        sect = _p_sectpr(child)
        if has_before and prev_new_page:
            _set_el_page_break_before(child, False)
            has_before = False
        if _p_plain(child) or _p_has_drawing(child):
            prev_new_page = bool(has_before or (sect is not None and _sect_starts_new_page(sect)))
        elif _paragraph_has_page_break(child) or has_before:
            prev_new_page = True


def _fill_toc_pages(doc: Document, fmt: DocumentFormat) -> None:
    raw = _estimate_bookmark_pages(doc)
    if not raw:
        return
    if fmt.pageNumberStart == "body":
        base = min(raw.values())
        pages = {k: max(1, v - base + 1) for k, v in raw.items()}
    elif fmt.pageNumberStart == "cover":
        pages = {k: max(1, v) for k, v in raw.items()}
    else:
        pages = {k: max(1, v - 1) for k, v in raw.items()}
    _apply_toc_page_numbers(doc, pages)


def _bookmark_first_new(doc: Document, start: int, name: str) -> None:
    for para in doc.paragraphs[start:]:
        if (para.text or "").strip():
            _bookmark_paragraph(para, name)
            return


def _write_cover(doc: Document, brief: BidBrief, fmt: DocumentFormat, *, volume: str = "") -> None:
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
    if volume == "technical":
        doc_title = "技 术 标 投 标 文 件"
    elif volume == "business":
        doc_title = "商 务 标 投 标 文 件"
    else:
        doc_title = "投 标 文 件"
    _run(sub, doc_title, size=fmt.coverDocSizePt, bold=True, font=font)
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
    brief: BidBrief,
    fmt: DocumentFormat,
    bookmarks: list[str],
) -> None:
    from api.services.tenders.categories import tech_plan_text

    head = doc.add_paragraph()
    head.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(head, "目录", size=fmt.tocTitleSizePt, bold=True, font=fmt.fontName)
    tree = build_outline_toc_tree(items, bookmarks, tech_text=tech_plan_text(brief))
    rows = flatten_toc_tree(tree, fmt.tocNumbering)
    for i, (label, title, level, bm) in enumerate(rows):
        para = doc.add_paragraph()
        _write_toc_line(
            para,
            i,
            title,
            bm,
            toc_page_cache(i, fmt),
            label=label,
            with_pages=True,
            level=level,
        )


def _inline_copied_id(
    doc: Document,
    *,
    start: int,
    key: str,
    media: dict | None,
    inlined: set[str],
    has_agent: bool,
) -> None:
    if key == "id_agent" and not has_agent:
        return
    before = None
    for p in doc.paragraphs[start:]:
        t = "".join((p.text or "").split())
        if t.startswith("投标人") and (
            "盖单位公章" in t or "（章）" in t or "(章)" in t or "签字" in t
        ):
            before = p
            break
    n = inline_id_scans(
        doc,
        (media or {}).get(key) if media else None,
        empty_slot=id_slot(key),
        before=before,
    )
    inlined.add(key)


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


def _write_copied_salute(doc: Document, block: str) -> None:
    n = compact_title(block)
    if not n.startswith("致"):
        _form_para(doc, block, size=12, align="left", space_after=8)
        return
    m = re.match(r"^致[：:]?(.*)$", n)
    value = re.sub(r"[＿_—\-－]+", "", (m.group(1) if m else "").strip())
    para = doc.add_paragraph()
    _rewrite_labeled_underline(
        para,
        [("致：", value, "")],
        align=WD_ALIGN_PARAGRAPH.LEFT,
        left_indent_cm=0,
        right_indent_cm=0.4,
        line_spacing=1.5,
        space_before=6,
        space_after=10,
        nowrap=True,
        line_em=22,
    )


def _write_copied_sign_line(doc: Document, block: str) -> None:
    n = compact_title(block)
    date_m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", n)
    if "日期" in n or re.fullmatch(r"年\s*月\s*日", (block or "").strip()):
        para = doc.add_paragraph()
        y = mth = d = ""
        if date_m:
            y, mth, d = date_m.group(1), date_m.group(2), date_m.group(3)
        _rewrite_date_line(
            para,
            y,
            mth,
            d,
            align=WD_ALIGN_PARAGRAPH.LEFT,
            left_indent_cm=0,
            right_indent_cm=0.4,
            nowrap=True,
            label="日期：",
            label_width=5,
            line_em=8,
            space_before=8,
            space_after=4,
        )
        return
    matched = _SIGN_LABEL.match(n)
    if not matched:
        _form_para(doc, block, size=12, align="left", space_after=2, line_spacing=1.15)
        return
    label = matched.group(1) + "："
    rest = (matched.group(3) or "").lstrip("：:")
    suffix = f"（{matched.group(2)}）" if matched.group(2) else ""
    if not suffix:
        lead = re.match(r"^[（(]([^)）]*)[)）][：:]*(.*)$", rest)
        if lead:
            suffix = f"（{lead.group(1)}）"
            rest = lead.group(2) or ""
        else:
            suf_m = re.search(r"([（(][^)）]*[)）])\s*$", rest)
            suffix = suf_m.group(1) if suf_m else ""
            rest = rest[: suf_m.start()] if suf_m else rest
    else:
        suf_m = re.search(r"([（(][^)）]*[)）])\s*$", rest)
        if suf_m:
            rest = rest[: suf_m.start()]
    value = re.sub(r"[＿_—\-－\s：:]+", "", rest)
    if "签字" in f"{label}{suffix}":
        value = ""
    para = doc.add_paragraph()
    _rewrite_labeled_underline(
        para,
        [(label, value, suffix)],
        align=WD_ALIGN_PARAGRAPH.LEFT,
        left_indent_cm=0,
        right_indent_cm=0.4,
        line_spacing=1.5,
        space_before=8,
        space_after=4,
        nowrap=True,
        line_em=16,
        label_width=5 if len(matched.group(1)) <= 4 else None,
    )


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
            _write_copied_salute(doc, block)
            continue
        if role == "sign_row":
            for col in _split_sign_columns(block) or [block]:
                _write_copied_sign_line(doc, col)
            continue
        if role == "sign":
            _write_copied_sign_line(doc, block)
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
    if compact.startswith("致") or (
        (raw.endswith("：") or raw.endswith(":"))
        and ("有限公司" in compact or compact.startswith("致"))
        and len(compact) <= 40
        and not compact.startswith(("投标承诺书", "承诺函", "投标函"))
    ):
        return "salute"
    sign_hit = _SIGN_TOKEN.search(compact) or _SIGN_TOKEN.search(raw)
    if sign_hit and (
        len(compact) <= 48
        or compact.startswith(("投标人", "供应商", "法定代表人", "日期", "委托代理人"))
    ):
        return "sign"
    if re.search(r"年\S{0,6}月\S{0,6}日", compact) and len(compact) <= 28:
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
        lines = [p.strip() for p in re.split(r"(?<=[。；;])", raw) if p.strip()]
    spread: list[str] = []
    for ln in lines:
        spread.extend(split_mashed_zhi_line(ln))
    merged = _coalesce_broken_form_lines(drop_ocr_junk_lines(spread))
    out: list[str] = []
    for ln in merged:
        parts = unfold_form_sign_lines(ln).splitlines()
        out.extend(p.strip() for p in parts if p.strip())
    return out or merged


_UNDER_ONLY = re.compile(r"^[-—–_＿\s]{2,}$")
_YMD_BIT = re.compile(r"^[年月日]([：:].*)?$")
_FRAG_HEAD = re.compile(r"[时日名址性]$")
_FRAG_TAIL = re.compile(r"^[间期称址质][：:]?")


def _coalesce_broken_form_lines(lines: list[str]) -> list[str]:
    """把 OCR/PDF 拆开的「成立时」「间：」「年」「月」「日」拼回一行。"""
    out: list[str] = []
    for ln in lines:
        s = (ln or "").strip()
        if not s or _UNDER_ONLY.match(s):
            continue
        s = re.sub(r"(日)(经营期限)", r"\1\n\2", s)
        chunks = [p.strip() for p in s.split("\n") if p.strip()]
        for chunk in chunks:
            if out and (_YMD_BIT.match(chunk) or (_FRAG_HEAD.search(out[-1]) and _FRAG_TAIL.match(chunk))):
                out[-1] += chunk
                continue
            if (
                out
                and len(chunk) <= 3
                and chunk.endswith(("：", ":"))
                and compact_title(chunk) not in {"致：", "致:"}
                and not out[-1].endswith(("：", ":", "。", "；"))
            ):
                out[-1] += chunk
                continue
            out.append(chunk)
    return out


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
