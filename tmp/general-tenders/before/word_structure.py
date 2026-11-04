"""Resolve Word structure from actual style/numbering definitions, not style IDs."""

from __future__ import annotations

import re

from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph


def outline_level(doc, el):
    direct = el.find("w:pPr/w:outlineLvl", el.nsmap)
    if direct is not None:
        value = int(direct.get(qn("w:val"), "9"))
        return value + 1 if value < 9 else 0
    style = Paragraph(el, doc).style
    seen = set()
    while style is not None and style.style_id not in seen:
        seen.add(style.style_id)
        value = style.element.find("w:pPr/w:outlineLvl", style.element.nsmap)
        if value is not None:
            rank = int(value.get(qn("w:val"), "9"))
            return rank + 1 if rank < 9 else 0
        match = re.fullmatch(r"(?:Heading|标题)\s*(\d+)", style.name, re.I)
        if match:
            return int(match.group(1))
        style = style.base_style
    return 0


def numbering_info(doc, el):
    properties = el.find("w:pPr/w:numPr", el.nsmap)
    if properties is None:
        style = Paragraph(el, doc).style
        seen = set()
        while style is not None and style.style_id not in seen:
            seen.add(style.style_id)
            properties = style.element.find("w:pPr/w:numPr", style.element.nsmap)
            if properties is not None:
                break
            style = style.base_style
    if properties is None:
        return None
    ident = properties.find(qn("w:numId"))
    if ident is None or ident.get(qn("w:val")) == "0":
        return None
    num_id = ident.get(qn("w:val"))
    level = properties.find(qn("w:ilvl"))
    level = int(level.get(qn("w:val"), "0")) if level is not None else 0
    root = doc.part.numbering_part.element
    number = root.find(f'w:num[@w:numId="{num_id}"]', root.nsmap)
    if number is None:
        return None
    abstract = number.find(qn("w:abstractNumId"))
    if abstract is None:
        return None
    abstract = root.find(
        f'w:abstractNum[@w:abstractNumId="{abstract.get(qn("w:val"))}"]', root.nsmap
    )
    if abstract is None:
        return None
    definition = abstract.find(f'w:lvl[@w:ilvl="{level}"]', root.nsmap)
    override = number.find(f'w:lvlOverride[@w:ilvl="{level}"]', root.nsmap)
    if override is not None and override.find(qn("w:lvl")) is not None:
        definition = override.find(qn("w:lvl"))
    if definition is None:
        return None

    def value(name, default):
        node = definition.find(qn("w:" + name))
        return node.get(qn("w:val"), default) if node is not None else default

    start = int(value("start", "1"))
    if override is not None:
        start_override = override.find(qn("w:startOverride"))
        if start_override is not None:
            start = int(start_override.get(qn("w:val"), str(start)))
    fmt = value("numFmt", "decimal")
    text = value("lvlText", f"%{level + 1}.")
    # Independent child lists often have ilvl=0 too; '(一)' is not a level-1 root.
    rank = level + 1
    if level == 0 and re.match(r"^[（(]", text):
        rank = 2
    return num_id, level, fmt, text, start, min(4, rank)


def _number(value, fmt):
    if fmt.startswith("chineseCounting") or fmt in {
        "ideographTraditional",
        "ideographLegalTraditional",
    }:
        digits = "零一二三四五六七八九"
        if value < 10:
            return digits[value]
        if value < 100:
            tens, units = divmod(value, 10)
            return (digits[tens] if tens > 1 else "") + "十" + (digits[units] if units else "")
    if fmt in {"upperLetter", "lowerLetter"} and 1 <= value <= 26:
        return chr((65 if fmt == "upperLetter" else 97) + value - 1)
    return str(value)


def numbering_labels(doc, paragraphs):
    counters = {}
    result = {}
    for el in paragraphs:
        if el.tag != qn("w:p"):
            continue
        info = numbering_info(doc, el)
        if info is None:
            continue
        ident, level, fmt, pattern, start, rank = info
        own = counters.setdefault(ident, {})
        own[level] = own.get(level, start - 1) + 1
        for deeper in [key for key in own if key > level]:
            del own[deeper]
        label = re.sub(
            r"%(\d+)",
            lambda m, counts=own, kind=fmt: _number(counts.get(int(m.group(1)) - 1, 1), kind),
            pattern,
        )
        result[el] = (label, rank)
    return result
