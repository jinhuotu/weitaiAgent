"""Limit automatic prose to explicit response sections, preserving original forms."""

from __future__ import annotations

import re

from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph


def manual_business_draft(item) -> bool:
    return bool((item.draftBody or "").strip()) and (item.draftStatus or "").lower() != "drafted"


def chapter_role(item) -> str:
    title = re.sub(r"\s+", "", item.title or "")
    if item.kind == "biz_dev" or any(w in title for w in ("商务偏离", "商务偏差", "商务差异")):
        return "deviation"
    if "优惠" in title or "特殊承诺" in title:
        return "offer"
    if any(w in title for w in (
        "响应承诺", "商务响应", "商务承诺", "合同条款响应", "商务条款响应", "合同响应",
    )):
        return "commitment"
    if item.kind == "commitment_copy" and manual_business_draft(item):
        return "commitment"
    return ""


def business_contexts(brief, *, doc=None) -> dict[str, str]:
    """Read actual form bodies; outline extraction can contain only the title."""
    from api.services.tenders.native_format import (
        _body_toc_block,
        _find_body_anchor,
        _physical_chapter,
        body_sections,
    )
    from api.services.tenders.pdf_format import _word_format_file

    contexts = {item.title: item.body or "" for item in brief.outlineItems}
    if doc is None:
        path = _word_format_file(brief.invitationId) if brief.invitationId else None
        if path is None or not path.is_file():
            return contexts
        doc = Document(path)
    children = list(doc.element.body)
    block = _body_toc_block(doc, brief)
    start = children.index(block[-1]) + 1 if block else 0
    paras = list(doc.element.body.iter(qn("w:p")))
    boundaries = {p for _, p, _ in body_sections(doc, brief)}
    boundaries.update(p for p in paras if p.getparent() is doc.element.body
                      and _physical_chapter(p, doc))
    for item in brief.outlineItems:
        if item.skipped or (item.kind != "commitment_copy" and not chapter_role(item)):
            continue
        heading, _ = _find_body_anchor(children, start, item.rawTitle or item.title, block)
        if heading is None:
            continue
        at = paras.index(heading) + 1
        end = next((n for n in range(at, len(paras)) if paras[n] in boundaries), len(paras))
        contexts[item.title] = "\n".join(Paragraph(p, doc).text for p in paras[at:end])
    return contexts


def protected_business_titles(brief, contexts) -> set[str]:
    protected = set()
    for item in brief.outlineItems:
        if manual_business_draft(item):
            continue
        role = chapter_role(item)
        if item.kind == "commitment_copy" and not role:
            protected.add(item.title)
        elif role in {"commitment", "offer"}:
            text = re.sub(r"\s+", "", contexts.get(item.title, ""))
            if len(text) >= 30 and re.search(
                r"(?:我方|本人|本公司|本单位|本企业|我单位|我公司).{0,20}(?:声明|承诺|保证)"
                r"|投标人(?:在此|郑重)?(?:声明|承诺|保证)"
                r"|特此承诺|特此声明", text,
            ):
                protected.add(item.title)
    return protected


def chapter_paragraphs(body: str) -> list[str]:
    from api.services.tenders.business_render import substantive_response

    # Generation-stage instructions and evidence disclaimers are not bid prose.
    meta = re.compile(
        r"本批|现阶段|概括响应|本(?:承诺书|章节).*汇总|(?:无依据|未经核实).{0,40}承诺"
        r"|(?:不|不得|不能|禁止).{0,10}(?:凭空|编造)|以已核实文件为准"
    )
    return [part.strip() for part in re.split(r"\n\s*\n|\n|[；;]", body)
            if substantive_response(part) and not meta.search(part)]


def business_draft_body(item) -> str:
    if manual_business_draft(item):
        return item.draftBody
    body = (item.draftBody or "").strip()
    # The existing fallback can include a checklist title before its one notice.
    if "【待补充】" in body and len(body) <= 160:
        return body
    parts = chapter_paragraphs(body)
    if parts:
        return "\n\n".join(dict.fromkeys(parts))
    # Keep the existing short missing-body notice, never a long pending clause dump.
    return ""
