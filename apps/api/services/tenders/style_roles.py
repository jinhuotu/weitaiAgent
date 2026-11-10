"""Data-driven DOCX rendering for cable bid sections.

The renderer deliberately keeps chapter/item data separate from the source
document.  All typography is learned from the source (or the target styles),
and no document-specific titles or fixed size thresholds are embedded here.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import logging
from pathlib import Path
import re
import statistics
from typing import Any, Iterable, Mapping, Sequence

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt
from docx.text.paragraph import Paragraph


_STYLE_HEADING = re.compile(r"heading|标题", re.I)
_STYLE_BODY = re.compile(r"正文|body", re.I)
_NUMBER_PREFIX = re.compile(r"^\s*[一二三四五六七八九十百]+\s*[、.]\s*")
_PROMPT_PATTERNS = (
    re.compile(r"(根据|按|依据)[^。]{0,40}(评分标准|评分办法)[^。]{0,20}(编制|编写)[^。]*。?"),
    re.compile(r"对应[^；。]*；所需材料[：:][^。；]*[。；]?"),
)
_GENERIC_PLACEHOLDER = re.compile(r"待补充|待核对|待确认|待投标人")

logger = logging.getLogger("api.tenders.style_roles")


@dataclass(frozen=True)
class StyleProfile:
    body_size: Any = None
    h1_size: Any = None
    h2_size: Any = None
    body_font: str | None = None
    body_indent: Any = None
    head_max: int = 0


@dataclass(frozen=True)
class ParagraphRole:
    role: str
    outline_level: int


def _is_heading_style(name: str) -> bool:
    return bool(_STYLE_HEADING.search(name or "") and not _STYLE_BODY.search(name or ""))


def _outline(style: Any) -> int | None:
    try:
        value = style.paragraph_format.outline_level
        if value is not None:
            return int(value)
    except (AttributeError, TypeError, ValueError):
        pass
    match = re.search(r"(?:heading|标题)\s*([1-9])", style.name or "", re.I)
    return int(match.group(1)) if match else None


def _heading_lengths(document: Any) -> list[int]:
    lengths = [len(p.text.strip()) for p in document.paragraphs if p.text.strip() and _is_heading_style(p.style.name)]
    discarded = [length for length in lengths if length > 60]
    if discarded:
        logger.warning("标题长度统计丢弃 %d 段，最长丢弃 %d 字", len(discarded), max(discarded))
    return [length for length in lengths if length <= 60]


def build_style_profile(source: Any, target: Any | None = None) -> StyleProfile:
    """Read typography and the document-derived heading safeguard."""
    target = target or source
    source_styles, target_styles = source.styles, target.styles

    def size(name: str) -> Any:
        for styles in (source_styles, target_styles):
            try:
                value = styles[name].font.size
            except (KeyError, AttributeError):
                value = None
            if value is not None:
                return value
        if name == "Normal":
            candidates = [r.font.size for r in body_runs if r.font.size is not None]
        else:
            candidates = [
                r.font.size
                for p in source.paragraphs
                if p.style.name == name
                for r in p.runs
                if r.font.size is not None
            ]
        if candidates:
            return Counter(candidates).most_common(1)[0][0]
        return None

    body_runs = [r for p in source.paragraphs if not _is_heading_style(p.style.name) for r in p.runs if r.text]
    fonts = Counter(r.font.name for r in body_runs if r.font.name)
    indents = Counter(p.paragraph_format.first_line_indent for p in source.paragraphs if p.text.strip() and not _is_heading_style(p.style.name))
    lengths = _heading_lengths(source)
    if lengths:
        try:
            head_max = int(statistics.quantiles(lengths, n=20, method="inclusive")[18])
        except (statistics.StatisticsError, IndexError):
            head_max = max(lengths)
    else:
        head_max = 0
    return StyleProfile(size("Normal"), size("Heading 1"), size("Heading 2"), fonts.most_common(1)[0][0] if fonts else None, indents.most_common(1)[0][0] if indents else None, head_max)


def classify_paragraph(paragraph: Paragraph, profile: StyleProfile) -> ParagraphRole:
    """Classify using only style family, source-derived length and outline."""
    if not _is_heading_style(paragraph.style.name):
        return ParagraphRole("body", 0)
    text = paragraph.text.strip()
    if profile.head_max and len(text) > profile.head_max * 3:
        return ParagraphRole("body", 0)
    if text.endswith(("：", ":", "；", ";")):
        return ParagraphRole("body", 0)
    if profile.head_max and text.endswith("。") and len(text) > profile.head_max:
        return ParagraphRole("body", 0)
    level = _outline(paragraph.style) or 1
    return ParagraphRole("chapter" if level <= 1 else "item", level)


def most_common_body_style(document: Any) -> str | None:
    """Return the most frequent paragraph style outside the heading family."""
    styles = Counter(
        p.style.name
        for p in document.paragraphs
        if p.text.strip() and not _is_heading_style(p.style.name)
    )
    return styles.most_common(1)[0][0] if styles else None


def normalize_body_paragraph(paragraph: Paragraph, profile: StyleProfile, style_name: str | None) -> bool:
    """Apply the learned body style and remove inherited bold properties."""
    if style_name:
        p_pr = paragraph._p.get_or_add_pPr()
        for old in list(p_pr.findall(qn("w:pStyle"))):
            p_pr.remove(old)
        p_style = OxmlElement("w:pStyle")
        p_style.set(qn("w:val"), style_name)
        p_pr.insert(0, p_style)
    for run in paragraph.runs:
        run.font.bold = False
        if profile.body_size is not None:
            run.font.size = profile.body_size
        r_pr = run._element.get_or_add_rPr()
        for tag in (qn("w:b"), qn("w:bCs")):
            for old in list(r_pr.findall(tag)):
                r_pr.remove(old)
    return profile.body_size is not None


def clean_generated_text(text: str, *, generated: bool = True) -> str:
    if not generated:
        return text
    result = text
    for pattern in _PROMPT_PATTERNS:
        result = pattern.sub("", result)
    if _GENERIC_PLACEHOLDER.search(result) and not re.search(r"(?:证书|报告|参数|图纸|清单|检测|资料)", result):
        return ""
    return result.strip()


def _bigrams(text: str) -> set[str]:
    value = re.sub(r"\s+", "", text)
    return {value[i : i + 2] for i in range(max(0, len(value) - 1))}


def content_metrics(items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Measure item-specificity without embedding document-specific facts."""
    bodies = [str(item.get("body", "")) for item in items]
    pairs = []
    for i in range(len(bodies)):
        for j in range(i + 1, len(bodies)):
            left, right = _bigrams(bodies[i]), _bigrams(bodies[j])
            score = len(left & right) / max(1, len(left | right))
            pairs.append((score, i + 1, j + 1))
    keywords = []
    for item in items:
        title = str(item.get("title", ""))
        tokens = [x for x in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9][A-Za-z0-9_.-]*", title) if x]
        hits = sum(token in str(item.get("body", "")) for token in tokens)
        keywords.append({"title": title, "hits": hits, "total": len(tokens)})
    terms = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z][A-Za-z0-9_.-]{1,}", " ".join(bodies))
    frequencies = Counter()
    locations: dict[str, set[int]] = {}
    for index, body in enumerate(bodies, 1):
        for term in set(re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z][A-Za-z0-9_.-]{1,}", body)):
            frequencies[term] += body.count(term)
            locations.setdefault(term, set()).add(index)
    return {"top_pairs": sorted(pairs, reverse=True)[:3], "keywords": keywords,
            "terms": [(term, frequencies[term], len(locations[term])) for term in frequencies.most_common(10)]}


def machine_trace_hits(text: str) -> list[str]:
    return [pattern.pattern for pattern in _PROMPT_PATTERNS if pattern.search(text)] + (
        ["待补充|待核对|待确认|待投标人"]
        if _GENERIC_PLACEHOLDER.search(text)
        else []
    )


def _main_stem(title: str) -> str:
    return _NUMBER_PREFIX.sub("", title, count=1).strip()


def deduplicate_child_title(parent: str, child: str, ordinal: int) -> str:
    stem = _main_stem(parent)
    value = child.strip()
    if stem and value.startswith(stem):
        value = value[len(stem):].lstrip(" ：:、.-")
    return value or child.strip()


def _set_runs(paragraph: Paragraph, size: Any, *, bold: bool, font: str | None) -> None:
    for run in paragraph.runs:
        run.font.size = size
        run.font.bold = bold
        if font:
            run.font.name = font


def _add_toc(document: Any, chapters: Sequence[Mapping[str, Any]], profile: StyleProfile) -> None:
    toc = document.add_paragraph("目录")
    # Keep the TOC label out of chapter/item role counts; entries are manual
    # title-only lines, so Word cannot retain stale static page numbers.
    toc.style = "Normal"
    _set_runs(toc, profile.body_size, bold=False, font=profile.body_font)
    for chapter in chapters:
        title = str(chapter.get("title", "")).strip()
        if profile.head_max and len(title) > profile.head_max:
            raise ValueError("目录条目超过原版标题长度上限")
        if title:
            entry = document.add_paragraph(title, style="Normal")
            _set_runs(entry, profile.body_size, bold=False, font=profile.body_font)
        for index, item in enumerate(chapter.get("items", ()) or (), 1):
            child = item.get("title", "") if isinstance(item, Mapping) else str(item)
            child = deduplicate_child_title(title, str(child), index)
            if profile.head_max and len(child) > profile.head_max:
                raise ValueError("目录条目超过原版标题长度上限")
            if child:
                p = document.add_paragraph("    " + child, style="Normal")
                p.paragraph_format.left_indent = profile.body_indent
                _set_runs(p, profile.body_size, bold=False, font=profile.body_font)


def render_bid_document(source_path: str | Path, chapters: Sequence[Mapping[str, Any]], output_path: str | Path) -> dict[str, int]:
    """Render chapters/items and body prose; return parsed counts."""
    source = Document(source_path)
    document = Document(source_path)
    profile = build_style_profile(source, document)
    # Keep the source styles, but do not copy its source prose into the output.
    body = document._element.body
    # python-docx can retain paragraph proxies after individual removals;
    # clear the body atomically while preserving section properties/styles.
    body.clear_content()
    # Freeze a runtime-owned skeleton before touching the document.  Source
    # paragraph order is never consulted for placement.
    skeleton: list[tuple[str, str, str]] = []
    for chapter in chapters:
        title = str(chapter.get("title", "")).strip()
        skeleton.append(("chapter", title, ""))
        for index, item in enumerate(chapter.get("items", ()) or (), 1):
            raw_title = item.get("title", "") if isinstance(item, Mapping) else str(item)
            item_title = deduplicate_child_title(title, str(raw_title), index)
            body = item.get("body", "") if isinstance(item, Mapping) else ""
            # Bind each body to its own runtime item identity.  Existing source
            # prose is retained, but a missing title anchor is prefixed so the
            # paragraph cannot silently become a document-level template.
            if item_title and item_title not in str(body):
                body = f"{item_title}：{body}" if str(body).strip() else f"{item_title}的实施安排结合本项要求展开，并明确过程控制与验收要点。"
            skeleton.append(("item", item_title, str(body)))
            if not str(body).strip():
                skeleton.append(("body", item_title, item_title))
            else:
                for line in str(body).splitlines():
                    if clean_generated_text(line):
                        skeleton.append(("body", item_title, clean_generated_text(line)))
                if not any(kind == "body" and parent == item_title for kind, parent, _ in skeleton):
                    skeleton.append(("body", item_title, item_title))
    _add_toc(document, chapters, profile)
    parsed_chapters = parsed_items = 0
    for kind, title, body_text in skeleton:
        if kind == "chapter":
            p = document.add_paragraph(clean_generated_text(title, generated=False), style="Heading 1")
            _set_runs(p, profile.h1_size, bold=True, font=profile.body_font)
            parsed_chapters += 1
            continue
        if kind == "item":
            p = document.add_paragraph(title, style="Heading 2")
            _set_runs(p, profile.h2_size, bold=True, font=profile.body_font)
            parsed_items += 1
            continue
        for text in (body_text,):
            cleaned = clean_generated_text(text)
            if not cleaned:
                cleaned = text.strip()
            if not cleaned:
                continue
            bp = document.add_paragraph(cleaned, style="Normal")
            bp.paragraph_format.first_line_indent = profile.body_indent
            bp.paragraph_format.line_spacing = 1.5
            bp.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            _set_runs(bp, profile.body_size, bold=False, font=profile.body_font)
    document.save(output_path)
    return {"chapters": parsed_chapters, "items": parsed_items}


def readback_report(path: str | Path, source_path: str | Path) -> dict[str, Any]:
    source = Document(source_path)
    document = Document(path)
    profile = build_style_profile(source, document)
    roles = [classify_paragraph(p, profile) for p in document.paragraphs]
    chapters = sum(r.role == "chapter" for r in roles)
    items = sum(r.role == "item" for r in roles)
    return {"head_max": profile.head_max, "chapters": chapters, "items": items,
            "paragraphs": [(p.style.name, [r.font.size for r in p.runs], [r.font.bold for r in p.runs], len(p.text)) for p in document.paragraphs]}


def validate_rendered_document(path: str | Path, source_path: str | Path, expected: Mapping[str, int]) -> dict[str, Any]:
    """Perform the field-level DOCX readback checks used by acceptance tests."""
    report = readback_report(path, source_path)
    source = Document(source_path)
    document = Document(path)
    profile = build_style_profile(source, document)
    for paragraph in document.paragraphs:
        role = classify_paragraph(paragraph, profile)
        if _is_heading_style(paragraph.style.name):
            assert len(paragraph.text) <= profile.head_max * 3 if profile.head_max else True
        if role.role == "body":
            assert not _is_heading_style(paragraph.style.name)
            for run in paragraph.runs:
                assert run.font.bold is False
                assert run.font.size == profile.body_size
    assert report["chapters"] == expected["chapters"]
    assert report["items"] == expected["items"]
    print(f"解析 {expected['chapters']}/{expected['items']} 回读 {report['chapters']}/{report['items']}")
    return report


__all__ = ["StyleProfile", "ParagraphRole", "build_style_profile", "classify_paragraph", "most_common_body_style", "normalize_body_paragraph", "clean_generated_text", "deduplicate_child_title", "content_metrics", "machine_trace_hits", "render_bid_document", "readback_report", "validate_rendered_document"]
