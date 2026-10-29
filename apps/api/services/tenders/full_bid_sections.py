"""Full-volume-only guards for original bid letters and their composition lists."""

from __future__ import annotations

import re

from docx.oxml.ns import qn


def letter_region(doc, brief) -> set:
    from api.services.tenders.format_rules import peel_toc_lead
    from api.services.tenders.pdf_format import _business_slice_kinds, _el_text, toc_block_ids
    from api.services.tenders.word_structure import numbering_info, outline_level

    toc = set(toc_block_ids(doc))
    independent = _business_slice_kinds(doc, brief, toc)
    protected = set()
    inside = False
    letter_level = 1
    for node in doc.element.body:
        if node in toc or node.tag == qn("w:sectPr"):
            continue
        if node.tag == qn("w:p"):
            text = re.sub(r"\s+", "", _el_text(node))
            bare = peel_toc_lead(text)
            letter = bool(re.fullmatch(
                r"(?:投标|响应)函(?:及(?:投标|响应)函附录|附录)?", bare
            ))
            # A stale Heading style cannot promote '(6)施工组织设计' to a chapter.
            composition = bool(re.match(r"^[（(]\d+[）)]", text))
            numbered = numbering_info(doc, node)
            if inside and numbered is not None and numbered[2] == "decimal":
                composition = composition or numbered[-1] > letter_level
            if node in independent and not composition:
                if letter and not inside:
                    letter_level = numbered[-1] if numbered is not None else (
                        outline_level(doc, node) or 1
                    )
                inside = letter
            elif inside and not composition:
                level = outline_level(doc, node)
                if level and level <= letter_level:
                    inside = False
        if inside:
            protected.update(node.iter())
    return protected


def missing_technical_body(body: str) -> bool:
    return bool(re.fullmatch(
        r"【待补充】「[^」]+」技术方案正文未完整生成，请结合本项目招标要求补充。?",
        (body or "").strip(),
    ))
