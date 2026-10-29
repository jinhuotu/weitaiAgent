"""Compact commercial responses without copying unanswered tender clauses."""

from __future__ import annotations

import re

from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def substantive_response(text: str) -> bool:
    value = re.sub(r"\s+", "", text or "")
    return bool(value) and not any(
        word in value for word in ("待补充", "待响应", "待确认", "待核实", "尚未响应")
    )


def response_rows(brief):
    """Keep source rows in the brief for QA; deduplicate only their presentation."""
    seen = set()
    rows = []
    for row in brief.bizDeviationLines:
        key = re.sub(r"\s+", "", row.requirement or "")
        if key and key not in seen:
            rows.append(row)
            seen.add(key)
    return rows


def pending_summary(count: int) -> str:
    return f"【待补充】{count} 项商务条款尚未响应，请核对后补充。" if count else ""


def response_section(brief, row):
    """Route evidence responses only to an existing matching business section."""
    if not substantive_response(row.response):
        return None
    groups = (
        ("营业执照", "独立承担民事责任"),
        ("财务", "审计"),
        ("业绩", "类似项目"),
        ("税收", "社保", "社会保障"),
        ("资质", "资格证书"),
    )
    for words in groups:
        if not any(word in row.requirement for word in words):
            continue
        candidates = [
            item
            for item in brief.outlineItems
            if not item.skipped
            and item.kind not in {
                "biz_dev", "tech_dev", "tech_plan", "tech_chapter", "quote", "commitment_copy",
            }
            and (item.draftStatus or "").lower() not in {"edited", "manual"}
            and any(word in item.title for word in words)
        ]
        if candidates:
            return candidates[0]
        # A general qualification chapter is a safe fallback for qualification evidence.
        if words[0] in {"营业执照", "资质"}:
            return next(
                (
                    item
                    for item in brief.outlineItems
                    if not item.skipped
                    and "资格" in item.title
                    and (item.draftStatus or "").lower() not in {"edited", "manual"}
                ),
                None,
            )
    return None


def commercial_rows(brief):
    """Display substantive, unrouted responses and a single compact pending row."""
    rows = response_rows(brief)
    visible = [
        row
        for row in rows
        if substantive_response(row.response) and response_section(brief, row) is None
    ]
    result = [
        (str(n), row.requirement, row.response, row.deviation, row.explanation)
        for n, row in enumerate(visible, 1)
    ]
    pending = sum(not substantive_response(row.response) for row in rows)
    if pending:
        result.append(
            (str(len(result) + 1), "未响应条款汇总", pending_summary(pending), "待确认", "")
        )
    return result


def finalize_business_responses(doc, brief, *, full_bid: bool = False):
    """Place routed answers and, when no deviation section exists, a final checklist.

    Runs before TOC/page processing. Named XML markers make repeated finalization safe.
    """
    if not getattr(brief, "autoDraftBizResp", False) or not brief.bizDeviationLines:
        return []
    from api.services.tenders.pdf_format import toc_block_ids

    block = toc_block_ids(doc)
    if full_bid:
        from api.services.tenders.full_bid_sections import letter_region

        block = [*block, *letter_region(doc, brief)]
    items = [item for item in brief.outlineItems if not item.skipped]

    def key(value):
        return re.sub(r"\s+", "", value or "")

    names = {key(name) for item in items for name in (item.title, item.rawTitle) if name}
    headings = [
        p
        for p in doc.paragraphs
        if p._p not in block
        and "PAGEREF" not in p._p.xml
        and not p._p.findall(".//w:tab", p._p.nsmap)
        and key(p.text) in names
    ]
    notes = []
    fallback = []
    for index, row in enumerate(response_rows(brief)):
        target = response_section(brief, row)
        if target is None:
            continue
        marker = f"biz_response_{index}"
        if doc.element.xpath(f'.//w:bookmarkStart[@w:name="{marker}"]'):
            continue
        heading = next(
            (p for p in headings if key(p.text) in {key(target.title), key(target.rawTitle)}), None
        )
        if heading is None:
            fallback.append(row)
            notes.append(f"商务响应未定位「{target.title}」，已移至文末核对表")
            continue
        parent = heading._p.getparent()
        following = next(
            (p for p in headings if parent.index(p._p) > parent.index(heading._p)), None
        )
        stop = parent.index(following._p) if following is not None else len(parent) - 1
        section_text = "".join(
            node.text or ""
            for node in list(parent)[parent.index(heading._p) : stop]
            for node in node.iter(qn("w:t"))
        )
        if key(row.response) in key(section_text):
            continue
        para = doc.add_paragraph(row.response)
        # Retain a stable marker without creating a new TOC heading.
        ids = [
            int(node.get(qn("w:id")))
            for node in doc.element.xpath(".//w:bookmarkStart")
            if (node.get(qn("w:id")) or "").isdigit()
        ]
        ident = str(max(ids, default=0) + 1)
        start, end = OxmlElement("w:bookmarkStart"), OxmlElement("w:bookmarkEnd")
        start.set(qn("w:id"), ident)
        start.set(qn("w:name"), marker)
        end.set(qn("w:id"), ident)
        para._p.insert(0, start)
        para._p.append(end)
        parent.insert(stop, para._p)

    has_deviation = any(
        item.kind == "biz_dev"
        or any(word in item.title for word in ("商务偏离", "商务偏差", "商务差异"))
        for item in items
    )
    display_rows = commercial_rows(brief)
    if has_deviation:
        # A copied source table may already contain text and be intentionally left
        # untouched by the filler. Do not silently lose responses in that case.
        table_rows = [
            key("".join(cell.text for cell in row.cells))
            for table in doc.tables
            for row in table.rows
        ]
        display_rows = [
            row
            for row in display_rows
            if not any(key(row[1]) in text and key(row[2]) in text for text in table_rows)
        ]
    if (not has_deviation or fallback or display_rows) and not doc.element.xpath(
        './/w:bookmarkStart[@w:name="biz_checklist"]'
    ):
        from api.services.tenders.document import _bookmark_paragraph

        title = doc.add_paragraph("商务响应完整性核对表")
        _bookmark_paragraph(title, "biz_checklist")
        rows = display_rows
        rows += [
            (str(len(rows) + n), row.requirement, row.response, row.deviation, row.explanation)
            for n, row in enumerate(fallback, 1)
        ]
        table = doc.add_table(rows=1, cols=4)
        for cell, text in zip(table.rows[0].cells, ("条款", "响应", "偏离", "说明"), strict=False):
            cell.text = text
        for _, requirement, response, deviation, explanation in rows:
            for cell, text in zip(
                table.add_row().cells, (requirement, response, deviation, explanation), strict=False
            ):
                cell.text = text or ""
    return notes
