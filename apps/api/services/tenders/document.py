"""克隆第五章空白 Word，按表单填空。LLM 只抽邀请书字段，不直接写 docx。"""

from __future__ import annotations

import io
import logging
import re
import shutil
from collections import Counter
from copy import deepcopy
from decimal import Decimal
from math import ceil, floor
from pathlib import Path

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor, Twips
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph
from docx.text.run import Run

from api.services.tenders.assets import find_chapter5_template
from api.services.tenders.categories import tech_plan_body, tech_plan_text
from api.services.tenders.commitment import append_commitment_letter
from api.services.tenders.format_rules import (
    apply_footer_page_number,
    apply_section_page,
    cn_ordinal,
    flatten_toc_tree,
    format_brief_notes,
    section_page_flags,
    title_canon,
    toc_page_cache,
    toc_display_title,
)
from api.services.tenders.outline import tech_chapter_titles, toc_title_key
from api.services.tenders.money import rmb_figure, rmb_lowercase, rmb_pair, rmb_uppercase
from api.services.tenders.placeholders import (
    TECH_DRAWING_KEY,
    TECH_DRAWING_SLOT,
    _set_row_height,
    draw_placeholder_box,
    fill_perf_placeholders,
    id_slot,
    inline_id_scans,
)
from api.services.tenders.quote import (
    QuoteSheet,
    apply_traffic_note,
    money_text,
    qty_text,
    resolve_quote_sheet,
    scale_quote,
)
from api.services.tenders.schema import BidBrief, DeviationLine, PerformanceLine
from api.services.tenders.slots import attachments_for_slots
from api.services.tenders.tables import (
    CHARGER_QUOTE_HEADERS,
    classify_quote_headers,
    name_amount_indexes,
    quote_role,
    width_ratios,
)
from api.services.tenders.value_policy import should_fill

logger = logging.getLogger("api.tenders")

_SONG = "宋体"

_HEADING_BOOKMARKS: tuple[tuple[str, str], ...] = (
    ("投标函及投标函附录", "toc_letter"),
    ("分项报价表", "toc_quote"),
    ("法定代表人身份证明", "toc_legal"),
    ("授权委托书", "toc_auth"),
    ("技术偏差表", "toc_dev"),
    ("原厂生产承诺", "toc_factory"),
    ("其他材料", "toc_other"),
    ("技术标（实施方案）", "toc_other"),
    ("投标函附录", "toc_letter_app"),
    ("支付条件", "toc_pay"),
)


def _compact(text: str) -> str:
    """去掉空白与括号，便于「技术标（实施方案）」与「技术标实施方案」互认。"""
    return re.sub(r"[\s/（）()]+", "", text or "")


def _font(run, size_pt: float | None = None, *, bold: bool | None = None) -> None:
    if bold is not None:
        run.bold = bold
    if size_pt is not None:
        run.font.size = Pt(size_pt)
    run.font.name = _SONG
    run.font.color.rgb = RGBColor(0, 0, 0)
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:ascii"), _SONG)
    rfonts.set(qn("w:hAnsi"), _SONG)
    rfonts.set(qn("w:eastAsia"), _SONG)
    for old in list(rpr.findall(qn("w:lang"))):
        rpr.remove(old)
    lang = OxmlElement("w:lang")
    lang.set(qn("w:val"), "zh-CN")
    lang.set(qn("w:eastAsia"), "zh-CN")
    rpr.append(lang)
    for old in list(rpr.findall(qn("w:noProof"))):
        rpr.remove(old)
    rpr.append(OxmlElement("w:noProof"))


def _title_style_name(name: str | None) -> bool:
    compact = re.sub(r"\s+", "", (name or "")).casefold()
    return compact in {
        *(f"heading{i}" for i in range(1, 10)),
        *(f"标题{i}" for i in range(1, 10)),
        "title", "subtitle", "标题", "副标题",
    }


def normalize_document_colors(doc: Document) -> int:
    """Make title styles and every explicit run color deterministic black."""
    style_names = {style.style_id: style.name for style in doc.styles}
    from api.services.tenders.directory_guard import cover_nodes
    cover = cover_nodes(doc)
    cover_styles = {value for node in cover if node.tag == qn('w:p')
                    for value in node.xpath('./w:pPr/w:pStyle/@w:val')}
    changed = 0

    def blacken(rpr) -> None:
        nonlocal changed
        color = rpr.find(qn("w:color"))
        if color is None:
            color = OxmlElement("w:color")
            rpr.append(color)
            changed += 1
        if color.get(qn("w:val")) != "000000":
            color.set(qn("w:val"), "000000")
            changed += 1
        for attr in ("themeColor", "themeShade", "themeTint"):
            key = qn(f"w:{attr}")
            if key in color.attrib:
                del color.attrib[key]
                changed += 1

    for style in doc.styles:
        if _title_style_name(style.name) and style.style_id not in cover_styles:
            blacken(style.element.get_or_add_rPr())

    for part in doc.part.package.parts:
        root = getattr(part, "element", None)
        if root is None:
            continue
        for rpr in root.iter(qn("w:rPr")):
            if rpr in cover:
                continue
            if rpr.find(qn("w:color")) is not None:
                blacken(rpr)
        for paragraph in root.iter(qn("w:p")):
            if paragraph in cover:
                continue
            ppr = paragraph.find(qn("w:pPr"))
            style_id = ppr.find(qn("w:pStyle")).get(qn("w:val")) if ppr is not None and ppr.find(qn("w:pStyle")) is not None else None
            if not _title_style_name(style_names.get(style_id)):
                continue
            for run in paragraph.iter(qn("w:r")):
                blacken(run.get_or_add_rPr())
    return changed


_TEXT_HEADING_NUMBER = re.compile(
    r"^\s*(?:[零〇一二三四五六七八九十百千万两]+、|"
    r"第\s*[零〇一二三四五六七八九十百千万两\d]+\s*章|"
    r"\d+(?:[.．]\d+)*[.．](?!\d)|\d+(?:[.．]\d+)+(?=\s|[^\d.．])|"
    r"[（(][零〇一二三四五六七八九十百千万两\d]+[）)])"
)


def _title_layout(doc: Document, fmt=None) -> tuple[int, list[str]]:
    """Fit body headings and project directory rows from their heading bookmarks."""
    from api.services.tenders.schema import DocumentFormat
    from api.services.tenders.technical_toc import _regions
    from api.services.tenders.word_structure import numbering_labels, outline_level

    regions = _regions(doc)
    from api.services.tenders.directory_guard import cover_nodes
    cover = cover_nodes(doc)
    excluded = {node for region in regions for host in region for node in host.iter()} | cover
    paragraphs = doc.paragraphs
    indexes = {p._p: i for i, p in enumerate(paragraphs)}
    widths = {}
    sections = iter(reversed(doc.sections))
    section = next(sections)
    for p in reversed(paragraphs):
        if _p_sectpr(p._p) is not None:
            section = next(sections, section)
        widths[p._p] = (section.page_width.pt - section.left_margin.pt - section.right_margin.pt
                        - (section.gutter.pt if section.gutter is not None else 0))

    def inherited_size(style):
        seen = set()
        while style is not None and style.style_id not in seen:
            seen.add(style.style_id)
            if style.font.size is not None:
                return style.font.size.pt
            style = style.base_style
        return None

    defaults = doc.styles.element.xpath("./w:docDefaults/w:rPrDefault/w:rPr/w:sz/@w:val")
    settings = fmt if fmt is not None else DocumentFormat()
    default_size = int(defaults[0]) / 2 if defaults else settings.bodySizePt

    def effective_size(p):
        base = inherited_size(p.style) or default_size
        return max((r.font.size.pt if r.font.size is not None else inherited_size(r.style) or base
                    for r in p.runs if r.text.strip()), default=base)

    levels = {}
    headings = {}
    body_sizes = Counter()
    for p in paragraphs:
        if p._p in excluded or not p.text.strip():
            continue
        level = outline_level(doc, p._p)
        if level or _title_style_name(p.style.name):
            levels[p._p] = level
            headings.setdefault(p.style.style_id, []).append(p)
        else:
            body_sizes[effective_size(p)] += 1
    body_size = body_sizes.most_common(1)[0][0] if body_sizes else (
        fmt.bodySizePt if fmt is not None else default_size
    )
    warnings = []
    changed = 0
    removed_numbering = set()

    # Body headings use direct paragraph alignment so the template's heading
    # hierarchy remains intact while every heading in the same hierarchy is
    # rendered consistently. Cover and table-cell paragraphs are excluded by
    # the structural scope above.
    for group in headings.values():
        for p in group:
            if p.alignment != WD_ALIGN_PARAGRAPH.CENTER:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                changed += 1

    def set_size(obj, points):
        nonlocal changed
        if obj.font.size != Pt(points):
            obj.font.size = Pt(points)
            changed += 1

    def inherited_numbering(style):
        seen = set()
        while style is not None and style.style_id not in seen:
            seen.add(style.style_id)
            number = style.element.xpath("./w:pPr/w:numPr/w:numId/@w:val")
            if number:
                return number[0] != "0"
            style = style.base_style
        return False

    for group in headings.values():
        for p in group:
            if not _TEXT_HEADING_NUMBER.match(p.text):
                continue
            props = p._p.get_or_add_pPr() if p._p.pPr is not None else None
            number = props.find(qn("w:numPr")) if props is not None else None
            inherited = inherited_numbering(p.style)
            if number is None and not inherited:
                continue
            props = p._p.get_or_add_pPr()
            if number is not None:
                props.remove(number)
            removed_numbering.add(p._p)
            indent = props.find(qn("w:ind"))
            if indent is not None:
                props.remove(indent)
            if inherited:
                # Word's zero list value suppresses inheritance. Put it on a
                # derived style so the paragraph keeps no list properties and
                # other paragraphs using the original list are unaffected.
                name = f"{p.style.name} (manual numbering)"
                try:
                    manual = doc.styles[name]
                except KeyError:
                    manual = doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
                    manual.base_style = p.style
                    manual.element.get_or_add_pPr().get_or_add_numPr().get_or_add_numId().val = 0
                p.style = manual
            changed += 1

    labels = numbering_labels(doc, [p._p for p in paragraphs if p._p not in excluded])
    for p in paragraphs:
        if p._p in levels and p._p in labels and not _TEXT_HEADING_NUMBER.match(p.text):
            warnings.append(f"标题仅有自动编号，待确认（段 {indexes[p._p]}）：{p.text.strip()}")

    def shown_title(p):
        text = p.text.strip()
        label = labels.get(p._p, ("", 0))[0]
        return label + text if label and not _TEXT_HEADING_NUMBER.match(text) else text

    def indent_width(p):
        formats = [p.paragraph_format]
        style = p.style
        seen = set()
        while style is not None and style.style_id not in seen:
            seen.add(style.style_id)
            formats.append(style.paragraph_format)
            style = style.base_style
        total = 0
        for name in ("left_indent", "right_indent", "first_line_indent"):
            value = next((getattr(f, name) for f in formats if getattr(f, name) is not None), None)
            if value is not None:
                total += max(0, value.pt)
        return total

    for style_id, group in headings.items():
        limits = []
        for p in group:
            width = widths[p._p] - indent_width(p)
            # Reserve one full-width glyph for font/renderer metrics.
            limits.append(width / (_em_len(shown_title(p)) + 1))
        size_limit = min(limits)
        style = doc.styles.get_by_id(style_id, WD_STYLE_TYPE.PARAGRAPH)
        family = _title_style_name(style.name)
        style_size = inherited_size(style) if family else None
        maximum = max(style_size or 0, *(effective_size(p) for p in group))
        if maximum > size_limit:
            size = max(body_size, floor(size_limit))
            shared_cover = any(node.tag == qn('w:p') and style_id in node.xpath('./w:pPr/w:pStyle/@w:val') for node in cover)
            if family and not shared_cover and (style_size is None or style_size > size):
                set_size(style, size)
            for p, limit in zip(group, limits, strict=True):
                if limit < body_size:
                    warnings.append(f"标题按正文字号仍可能折行（段 {indexes[p._p]}）：{p.text.strip()}")
                for run in p.runs:
                    if run.text.strip() and (run.font.size is None or run.font.size.pt > size):
                        set_size(run, size)

    anchors = {}
    for p in paragraphs:
        if p._p in excluded:
            continue
        for mark in p._p.findall(qn("w:bookmarkStart")):
            anchors.setdefault(mark.get(qn("w:name")), []).append(p)
    # Capture the outer field's closing run before any nested PAGEREF is removed.
    outer_ends = {}
    for region in regions:
        if not any(re.search(r"^\s*TOC\b", n.text or "", re.I)
                   for host in region for n in host.iter(qn("w:instrText"))):
            continue
        markers = [n for host in region for n in host.iter(qn("w:fldChar"))]
        if markers and markers[-1].get(qn("w:fldCharType")) == "end":
            run = markers[-1].getparent()
            if sum(n.get(qn("w:fldCharType")) == "begin" for n in markers) == sum(
                n.get(qn("w:fldCharType")) == "end" for n in markers
            ):
                outer_ends[run.getparent()] = run

    seen_targets = set()
    rejected_targets = set()
    toc_size = settings.tocItemSizePt
    for p in paragraphs:
        bookmark = _pageref_bookmark(p)
        if not bookmark or p._p not in excluded:
            continue
        candidates = anchors.get(bookmark, ())
        target = candidates[0] if len(candidates) == 1 else None
        level = levels.get(target._p, 0) if target is not None else 0
        title = shown_title(target) if level else ""
        original, _, cache = p.text.partition("\t")
        if (title and target._p not in removed_numbering
                and _TEXT_HEADING_NUMBER.sub("", original, count=1).strip()
                == _TEXT_HEADING_NUMBER.sub("", title, count=1).strip()):
            # An existing directory numbering scheme may use a different prefix.
            title = original
        indent = Twips(Cm(0.74 * max(0, level - 1)).twips)
        # Reserve the width of the page number and one glyph for the leader.
        room = (widths[p._p] - indent.pt) / toc_size - _em_len(cache) - 1
        title_room = (widths[target._p] - indent_width(target)) / effective_size(target) + 1 if level else 0
        reason = ""
        if not title or not original.strip():
            reason = "缺少有标题样式的正文书签"
        elif bookmark in seen_targets:
            reason = "重复指向同一正文标题"
        elif _em_len(original.strip()) > min(room, title_room) or _em_len(title) > min(room, title_room):
            warnings.append(f"目录长标题超出版心允许的单行长度，保留条目并允许折行：{original}")
        if reason:
            warnings.append(f"目录待确认（{reason}）：{original[:40] or bookmark}（{len(original)} 字）")
            rejected_targets.add(bookmark)
            node = p._p
            closing_run = outer_ends.get(node)
            if closing_run is not None and node.getprevious() is not None and node.getprevious().tag == qn("w:p"):
                node.getprevious().append(closing_run)
                outer_ends[node.getprevious()] = closing_run
            if _p_sectpr(node) is not None:
                _clear_runs(p)
            else:
                node.getparent().remove(node)
            changed += 1
            continue
        seen_targets.add(bookmark)
        for field in target._p.findall(qn("w:fldSimple")):
            instruction = field.get(qn("w:instr"), "")
            entry = re.fullmatch(r'\s*TC\s+"(?:\\.|[^"\\])*"(\s+\\f\s+(?:N|T)\b.*)', instruction)
            if entry:
                escaped = title.replace("\\", "\\\\").replace('"', '\\"')
                expected = f' TC "{escaped}"{entry[1]}'
                if instruction != expected:
                    field.set(qn("w:instr"), expected)
                    changed += 1
        if original != title:
            _replace_inline_span(p, 0, len(original), title)
            changed += 1
        style_name = f"TOC {level}"
        try:
            toc_style = doc.styles[style_name]
        except KeyError:
            toc_style = doc.styles.add_style(style_name, WD_STYLE_TYPE.PARAGRAPH)
            changed += 1
        set_size(toc_style, toc_size)
        if p.style != toc_style:
            p.style = toc_style
            changed += 1
        for run in p._p.iter(qn("w:r")):
            set_size(Run(run, p), toc_size)
        props = p.paragraph_format
        for field, value in (("left_indent", indent), ("first_line_indent", Twips(0)),
                             ("right_indent", Twips(0)), ("space_before", Pt(0)),
                             ("space_after", Pt(0)), ("line_spacing", 1.0)):
            if getattr(props, field) != value:
                setattr(props, field, value)
                changed += 1
        tabs = list(props.tab_stops)
        tab_position = Pt(widths[p._p]) - Pt(toc_size)
        tab_position = Twips(tab_position // 635)
        if (len(tabs) != 1 or tabs[0].position != tab_position or
                tabs[0].alignment != WD_TAB_ALIGNMENT.RIGHT or tabs[0].leader != WD_TAB_LEADER.DOTS):
            props.tab_stops.clear_all()
            props.tab_stops.add_tab_stop(tab_position, WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
            changed += 1
    for bookmark in rejected_targets - seen_targets:
        for target in anchors.get(bookmark, ()):
            for field in target._p.findall(qn("w:fldSimple")):
                if re.match(r"\s*TC\b.*\\f\s+(?:N|T)\b", field.get(qn("w:instr"), "")):
                    target._p.remove(field)
                    changed += 1
    return changed, warnings


def _clear_bold(rpr) -> None:
    for tag in (qn("w:b"), qn("w:bCs")):
        for old in list(rpr.findall(tag)):
            rpr.remove(old)


def _clone_fill_rpr(source_rpr, *, underline: bool = True):
    """从相邻正文复制字号/字体/字距，填空只加下划线、不加粗、不放大。"""
    rpr = OxmlElement("w:rPr")
    keep = {
        qn("w:rFonts"),
        qn("w:sz"),
        qn("w:szCs"),
        qn("w:spacing"),
        qn("w:kern"),
        qn("w:color"),
    }
    if source_rpr is not None:
        for child in list(source_rpr):
            if child.tag in keep:
                rpr.append(deepcopy(child))
    _clear_bold(rpr)
    if underline:
        for old in list(rpr.findall(qn("w:u"))):
            rpr.remove(old)
        u = OxmlElement("w:u")
        u.set(qn("w:val"), "single")
        rpr.append(u)
    return rpr


def _neighbor_rpr(para: Paragraph, skip_run) -> object | None:
    for run in para.runs:
        if run._element is skip_run._element:
            continue
        text = (run.text or "").strip()
        if not text:
            continue
        if _run_underlined(run) and (
            run._element.find(qn("w:tab")) is not None or not text
        ):
            continue
        rpr = run._element.find(qn("w:rPr"))
        if rpr is not None:
            return rpr
    p_pr = para._p.find(qn("w:pPr"))
    if p_pr is not None:
        return p_pr.find(qn("w:rPr"))
    return None


def _clear_runs(para: Paragraph) -> None:
    for run in list(para.runs):
        parent = run._element.getparent()
        if parent is not None:
            parent.remove(run._element)


def _clear_tab_stops(para: Paragraph) -> None:
    p_pr = para._p.find(qn("w:pPr"))
    if p_pr is None:
        return
    for old in p_pr.findall(qn("w:tabs")):
        p_pr.remove(old)


def _run_underlined(run) -> bool:
    rpr = run._element.find(qn("w:rPr"))
    line = rpr.find(qn("w:u")) if rpr is not None else None
    return line is not None and line.get(qn("w:val"), "single") not in {"none", "0", "false", "off"}


def _blank_runs(para: Paragraph) -> list:
    """模板空白栏：带下划线、且几乎只有制表符的 run。"""
    blanks = []
    for run in para.runs:
        if not _run_underlined(run):
            continue
        text = (run.text or "").strip()
        if text and text not in {"元"}:
            continue
        if run._element.find(qn("w:tab")) is None and text:
            continue
        blanks.append(run)
    return blanks


def _remove_line_drawings(para: Paragraph) -> int:
    """去掉模板里的形状/VML 手绘横线（比字符下划线更粗）。"""
    removed = 0
    for run in list(para.runs):
        r = run._element
        for child in list(r):
            tag = child.tag
            if (
                tag == qn("w:drawing")
                or tag == qn("w:pict")
                or tag.endswith("}AlternateContent")
            ):
                r.remove(child)
                removed += 1
        if any(c.tag in (qn("w:t"), qn("w:tab"), qn("w:br"), qn("w:cr")) for c in r):
            continue
        parent = r.getparent()
        if parent is not None:
            parent.remove(r)
    return removed


def _ensure_blank_underline(para: Paragraph) -> None:
    """若段落有制表空位但无下划线，补上与其它填空一致的单线、不加粗。"""
    if _blank_runs(para):
        return
    for run in para.runs:
        if run._element.find(qn("w:tab")) is None:
            continue
        if (run.text or "").strip():
            continue
        rpr = run._element.get_or_add_rPr()
        _clear_bold(rpr)
        if rpr.find(qn("w:u")) is None:
            u = OxmlElement("w:u")
            u.set(qn("w:val"), "single")
            rpr.append(u)
        return


def _normalize_blank_underlines(doc: Document) -> None:
    """签字空位等未填下划线：去掉加粗，避免比其它横线更粗。

    只扫正文段落，不进表格：报价/偏差表单元格很长时 row.cells/text 极慢。
    """
    for para in doc.paragraphs:
        for run in _blank_runs(para):
            rpr = run._element.find(qn("w:rPr"))
            if rpr is None:
                continue
            _clear_bold(rpr)


def _put_on_line(run, text: str, *, para: Paragraph | None = None) -> None:
    """把文字写进原有下划线空白：跟相邻正文字体字号一致，不加粗、不放大。"""
    from api.services.tenders.pdf_format import _blank_tokens, _write_blank_span
    para = para or getattr(run,'_parent',None)
    if para is not None:
        para.part.document.element._fill_styles = para.part.document.styles.element
    el = para._p if para is not None else run._element.getparent()
    owned = [token for token in _blank_tokens(el) if token[0] is run._element]
    if owned and (text or '').strip():
        _write_blank_span(el, owned[0][2], owned[-1][3], text)


def _fill_blanks(para: Paragraph, *values: str) -> int:
    from api.services.tenders.pdf_format import _blank_ranges, _blank_tokens, _run_under, _write_blank_span
    para.part.document.element._fill_styles = para.part.document.styles.element
    tokens = list(_blank_tokens(para._p))
    blanks = [span for span in _blank_ranges(para._p)
              if any(_run_under(run) and a < span.end() and b > span.start() for run,_,a,b,_ in tokens)]
    filled = 0
    for span, val in reversed(list(zip(blanks, values))):
        if not (val or "").strip():
            continue
        _write_blank_span(para._p, span.start(), span.end(), val)
        filled += 1
    return filled


_INLINE_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("lotName", ("分标名称", "分标名", "标段名称")),
    ("lotNo", ("分标编号", "分标号", "标段编号", "标段号")),
    ("packageName", ("包名称", "包名")),
    ("packageNo", ("包号", "包编号")),
    ("bidDate", ("投标日期", "投标日 期", "日期")),
)


def _iter_inline_label_paragraphs(doc: Document):
    """遍历正文和表格段落，供行内标签填空器使用。"""
    # XML traversal visits merged cells once and includes every nested table.
    for node in doc.element.body.iter(qn('w:p')):
        yield Paragraph(node, doc._body)


def fill_inline_labeled_blanks(doc: Document, brief: BidBrief, *, dry_run: bool = False, facts=None) -> list[dict[str, str | int]]:
    """Use the shared semantic filler for each labelled slot, including bare spaces."""
    from api.services.tenders.pdf_format import _inline_blank_records

    brief, _ = _fact_backed_brief(brief, facts)
    if not dry_run:
        doc.element._fill_styles = doc.styles.element
    records: list[dict[str, str | int]] = []
    for para in _iter_inline_label_paragraphs(doc):
        records.extend(_inline_blank_records(para._p, brief, dry_run=dry_run))
    return records


def _fact_backed_brief(brief, facts):
    """Only unambiguous, identity-scoped evidence may supply absent form fields."""
    from api.services.tenders.pdf_format import _LABEL_FIELDS
    fields = {
        'companyEstablished': ('company',), 'companyBusinessTerm': ('company',),
        'bidderNature': ('company',), 'companyRegisteredCapital': ('company',),
        'legalPersonTitle': ('person/legal', 'company'),
        'legalPersonIdNo': ('person/legal',),
        'legalPersonGender': ('person/legal',), 'legalPersonAge': ('person/legal',),
        'agentIdNo': ('person/legal', 'person/agent'),
    }
    resolved = brief.model_copy(deep=True)
    records = []
    for field, roles in fields.items():
        if getattr(brief, field, None):
            continue
        got = {}
        for bundle in facts or []:
            if bundle.get('role') not in roles:
                continue
            values = {item.get('name'): str(item.get('value') or '').strip()
                      for item in bundle.get('fields', []) if item.get('conf', 1) >= 0.6}
            if field.startswith(('legalPerson', 'agent')):
                identities = {values.get(name, '') for name in ('姓名', '法定代表人', '法定代表人姓名', '身份证号', '身份证号码')} - {''}
                selected = ({brief.agentName, brief.agentIdNo} if field.startswith('agent')
                            else {brief.legalPersonName, brief.legalPersonIdNo}) - {''}
                if not identities & selected:
                    continue
            aliases = (_LABEL_FIELDS['legalPersonIdNo'] if field == 'agentIdNo'
                       else (*_LABEL_FIELDS[field], '担任职务', '任职职务') if field == 'legalPersonTitle'
                       else _LABEL_FIELDS[field])
            for name in aliases:
                value = values.get(name)
                if value:
                    got.setdefault(value, []).append(bundle.get('fileId', ''))
        if len(got) == 1:
            value = next(iter(got))
            setattr(resolved, field, value)
            records.append({'field': field, 'source': got[value], 'status': 'sourced'})
        elif got:
            records.append({'field': field, 'status': 'conflicting_sources'})
    return resolved, records


def _style_fill_run(run, para: Paragraph | None = None, *, underline: bool = True) -> None:
    """新建填空 run：样式对齐段落正文，仅下划线。"""
    ref = None
    if para is not None:
        ref = _neighbor_rpr(para, run)
        if ref is None:
            p_pr = para._p.find(qn("w:pPr"))
            if p_pr is not None:
                ref = p_pr.find(qn("w:rPr"))
    r = run._element
    old = r.find(qn("w:rPr"))
    if old is not None:
        r.remove(old)
    r.insert(0, _clone_fill_rpr(ref, underline=underline))


def _write_tenderer_line(para: Paragraph, name: str) -> None:
    """公司名下划线填空，其后保留完整「（招标人名称）：」。与正文字体一致。"""
    align = para.alignment
    _clear_runs(para)
    name_run = para.add_run((name or "").strip())
    _style_fill_run(name_run, para, underline=True)
    hint_run = para.add_run("（招标人名称）：")
    _style_fill_run(hint_run, para, underline=False)
    if align is not None:
        para.alignment = align


def _use_body_style(para: Paragraph) -> None:
    """封面落款在模板里是 Heading1，OnlyOffice 会当大纲标题转换，又慢还容易「下载失败」。"""
    p_pr = para._p.get_or_add_pPr()
    for old in list(p_pr.findall(qn("w:pStyle"))):
        p_pr.remove(old)
    style = OxmlElement("w:pStyle")
    style.set(qn("w:val"), "BodyText")
    p_pr.insert(0, style)
    rpr = p_pr.find(qn("w:rPr"))
    if rpr is not None:
        p_pr.remove(rpr)


def _em_len(text: str) -> float:
    """宋体 12pt 下汉字≈1em、西文≈0.55em，用来估算填空线长度。"""
    n = 0.0
    for ch in text or "":
        code = ord(ch)
        if ch in {"\u3000", "\u2003"}:
            n += 1.0
        elif code <= 32:
            continue
        elif code < 127:
            n += 0.55
        else:
            n += 1.0
    return n


def _add_form_pad(para: Paragraph, ems: float, *, blank: bool = False) -> None:
    """空栏/补线一律全角空格 + 单实线下划线（w:u single），不用＿/短横，预览里才是实线。"""
    del blank
    n = int(round(max(0.0, ems)))
    if n <= 0:
        return
    _form_run(para, "\u3000" * n, underline=True)


def _set_word_wrap(para: Paragraph, *, enabled: bool) -> None:
    p_pr = para._p.get_or_add_pPr()
    for old in p_pr.findall(qn("w:wordWrap")):
        p_pr.remove(old)
    if enabled:
        return
    el = OxmlElement("w:wordWrap")
    el.set(qn("w:val"), "off")
    p_pr.append(el)


def _set_fill_tab_stop(para: Paragraph, pos_twips: int) -> None:
    """图三空栏：无前导符的左制表位，下划线 tab 拉成贴字底的长实线。"""
    p_pr = para._p.get_or_add_pPr()
    for old in p_pr.findall(qn("w:tabs")):
        p_pr.remove(old)
    tabs = OxmlElement("w:tabs")
    tab = OxmlElement("w:tab")
    tab.set(qn("w:val"), "left")
    tab.set(qn("w:leader"), "none")
    tab.set(qn("w:pos"), str(int(pos_twips)))
    tabs.append(tab)
    rpr = p_pr.find(qn("w:rPr"))
    if rpr is not None:
        rpr.addprevious(tabs)
    else:
        p_pr.append(tabs)


def _form_run(para: Paragraph, text: str, *, underline: bool = False) -> None:
    """与图三身份证明同款：继承正文，只加单实线下划线。"""
    run = para.add_run(text)
    _style_fill_run(run, para, underline=underline)
    _clear_bold(run._element.get_or_add_rPr())


def _add_form_blank(para: Paragraph) -> None:
    """空栏：全角空格实线下划线。不用制表符，避免预览把 tab 拉出页边。"""
    _add_form_pad(para, 8)


def _underline_value(para: Paragraph, value: str, target_em: int) -> None:
    """有字则字下划线再补空；空栏用全角空格+单实线下划线。绝不超出 target。"""
    target = max(0, int(target_em or 0))
    text = (value or "").strip()
    if text:
        _form_run(para, text, underline=True)
        return
    if target >= 1:
        _add_form_pad(para, target)


def _align_sign_label(label: str, width: int = 5) -> str:
    """短标签左右撑满与长标签同宽：空格插在字中间，冒号对齐。"""
    raw = (label or "").strip()
    if raw.endswith("：") or raw.endswith(":"):
        raw = raw[:-1]
    compact = "".join(ch for ch in raw if ch not in {"\u3000", " ", "\t"})
    if not compact:
        return "："
    n = len(compact)
    if n >= width:
        return compact + "："
    extra = width - n
    if n == 1:
        return compact + ("\u3000" * extra) + "："
    gaps = n - 1
    base, rem = divmod(extra, gaps)
    parts: list[str] = []
    for i, ch in enumerate(compact):
        parts.append(ch)
        if i < gaps:
            parts.append("\u3000" * (base + (1 if i < rem else 0)))
    return "".join(parts) + "："


def _line_em_budget(para: Paragraph, label: str, suffix: str, *, size_pt: float = 12.0) -> int:
    """当前段左右缩进后还能排下多少个汉字，用来截断填空线。"""
    doc = para.part.document
    sec = doc.sections[-1]
    usable = float(sec.page_width.cm) - float(sec.left_margin.cm) - float(sec.right_margin.cm)
    pf = para.paragraph_format
    if pf.left_indent:
        usable -= float(pf.left_indent.cm)
    if pf.right_indent:
        usable -= float(pf.right_indent.cm)
    # OnlyOffice/宋体略宽，多留余量，避免最后一个全角空格掉到下一行
    em_cm = max(0.36, size_pt * 2.54 / 72.0)
    remain = usable / em_cm - _em_len(label) - _em_len(suffix) - 1.2
    return max(0, int(remain))


_LETTER_CONTACT_KW: dict = {
    "align": WD_ALIGN_PARAGRAPH.LEFT,
    "left_indent_cm": 3.2,
    "line_spacing": 1.5,
    "space_before": 6,
    "space_after": 4,
    "line_em": 8,
}

# 落款块靠右半页：短标签冒号轴对齐，横线拉到同一右缘；长度按行宽预算，禁止换行
_LETTER_SIGN_RIGHT_KW: dict = {
    "align": WD_ALIGN_PARAGRAPH.LEFT,
    "left_indent_cm": 4.6,
    "right_indent_cm": 0.7,
    "line_spacing": 1.15,
    "space_before": 2,
    "space_after": 1,
    "nowrap": True,
    "line_em": 18,
    "label_width": 5,
    "fill_to_edge": True,
}

# 投标函网址/电话/传真/邮编：四字标签对齐，下划线拉到同一右缘
_LETTER_FIELD_KW: dict = {
    **_LETTER_CONTACT_KW,
    "line_em": 22,
    "nowrap": True,
}

_COVER_SIGN_KW: dict = {
    **_LETTER_CONTACT_KW,
    "left_indent_cm": 3.6,
    "space_before": 8,
    "space_after": 6,
    "line_em": 8,
}

_LETTER_ADDR_KW: dict = {
    **_LETTER_CONTACT_KW,
    "line_spacing": 2.0,
    "space_before": 8,
    "space_after": 8,
    "line_em": 8,
}

# 身份证明 / 授权委托书落款：标签冒号对齐、单行不换行
_SIGN_LABEL_WIDTH = 5
_SIGN_OFF_KW: dict = {
    "align": WD_ALIGN_PARAGRAPH.LEFT,
    "left_indent_cm": 3.2,
    "right_indent_cm": 0.25,
    "line_spacing": 1.5,
    "space_before": 4,
    "space_after": 4,
    "line_em": 8,
    "nowrap": True,
    "label_width": _SIGN_LABEL_WIDTH,
}

_SIGN_BOTTOM_PAD_CM = 2.2
_SIGN_MIN_GAP_CM = 1.5
# 手写签字格目标宽度；实际以行宽为准，宁短不挤换行
_SIGN_BLANK_MIN_EM = 10
_TWIPS_PER_CM = 567
_EMU_PER_CM = 360000.0


def _is_hand_sign_blank(label: str, value: str, suffix: str) -> bool:
    blob = f"{label}{suffix}{value}"
    return any(k in blob for k in ("签字", "签名", "手签"))


def _fit_hand_sign_fill(para: Paragraph, display: str, suffix: str, *, want_em: int) -> int:
    """签字横线长度：先保证「标签+线+（签字或盖章）」同排，再尽量拉长。"""
    room = _line_em_budget(para, display, suffix)
    if room >= want_em:
        return want_em
    # 预算不够时略减左缩进再量一次
    pf = para.paragraph_format
    cur = float(pf.left_indent.cm) if pf.left_indent else 0.0
    if cur > 2.0 and room < want_em:
        em_cm = 0.38
        need = want_em - room
        shrink = min(cur - 2.0, need * em_cm + 0.3)
        if shrink >= 0.2:
            pf.left_indent = Cm(cur - shrink)
            room = _line_em_budget(para, display, suffix)
    # 至少留一点签字空位，但绝不超过 room（否则后缀必换行）
    return max(4, min(want_em, room))


def _rewrite_labeled_underline(
    para: Paragraph,
    rows: list[tuple[str, str, str]],
    *,
    align=WD_ALIGN_PARAGRAPH.RIGHT,
    line_spacing: float = 1.5,
    space_before: float | None = 8,
    space_after: float | None = 4,
    left_indent_cm: float | None = None,
    right_indent_cm: float | None = 0.55,
    blank_width: int = 16,
    line_em: int = 8,
    nowrap: bool = False,
    label_width: int | None = None,
    fill_to_edge: bool = False,
) -> None:
    """标签 + 单实线下划线 + 后缀。空栏不换行，后缀与横线同一行。"""
    # Existing forms already define the label, underline and tab positions.
    # Reuse those slots; the construction path below is only for a new line.
    from api.services.tenders.pdf_format import _blank_ranges, _blank_tokens, _run_under, _fit_filled_run, _relax_signature_flow
    tokens = list(_blank_tokens(para._p))
    has_blank = any(re.search(r'[_＿]',span.group()) or any(_run_under(run) and a<span.end() and b>span.start()
                    for run,_,a,b,_ in tokens) for span in _blank_ranges(para._p))
    if has_blank:
        _fill_blanks(para, *[value for _,value,_ in rows])
        _relax_signature_flow(para._p)
        return
    if rows and all(not value.strip() or value.strip() in para.text for _,value,_ in rows) and any(run.underline for run in para.runs):
        for run in para.runs:
            if any(value.strip() == run.text for _,value,_ in rows if value.strip()):
                _fit_filled_run(para._p, run._r)
        _relax_signature_flow(para._p)
        return
    del blank_width
    _clear_runs(para)
    _clear_tab_stops(para)
    if align is not None:
        para.alignment = align
    pf = para.paragraph_format
    if space_before is not None:
        pf.space_before = Pt(space_before)
    if space_after is not None:
        pf.space_after = Pt(space_after)
    pf.line_spacing = line_spacing
    pf.first_line_indent = Cm(0)
    if left_indent_cm is not None:
        pf.left_indent = Cm(left_indent_cm)
    pf.right_indent = Cm(0.0 if right_indent_cm is None else right_indent_cm)
    _use_body_style(para)
    # 落款/填空一律禁止软换行，避免「（签字或盖章）」掉到下一行
    _set_word_wrap(para, enabled=False)
    soft_cap = max(0, int(line_em or 0))
    for i, (label, value, suffix) in enumerate(rows):
        if i:
            para.add_run().add_break()
        display = _align_sign_label(label, label_width) if label_width else label
        hand = _is_hand_sign_blank(label, value, suffix)
        fill = value
        if hand:
            fill_target = _fit_hand_sign_fill(
                para, display, suffix, want_em=_SIGN_BLANK_MIN_EM
            )
        else:
            room = _line_em_budget(para, display, suffix)
            if fill_to_edge:
                fill_target = room
            elif soft_cap:
                fill_target = min(soft_cap, room)
            else:
                fill_target = room
        _form_run(para, display, underline=False)
        _underline_value(para, fill, fill_target)
        if suffix:
            _form_run(para, suffix, underline=False)
    from api.services.tenders.pdf_format import _fit_filled_run, _relax_signature_flow
    for run in para.runs:
        if run.underline and (run.text or '').strip():
            _fit_filled_run(para._p, run._r)
    _relax_signature_flow(para._p)


def _rewrite_date_line(
    para: Paragraph,
    year: str,
    month: str,
    day: str,
    **kw,
) -> None:
    """日期与上方落款同一左缘；年/月/日数字下划线，不用制表虚线。"""
    from api.services.tenders.pdf_format import _blank_ranges, _fill_date_placeholders, _relax_signature_flow
    if _blank_runs(para) or (all(mark in para.text for mark in ('年','月','日')) and _blank_ranges(para._p)):
        if all(str(part or '').strip().isdigit() for part in (year,month,day)):
            if all(mark in para.text for mark in ('年','月','日')):
                from types import SimpleNamespace
                _fill_date_placeholders(para._p, SimpleNamespace(bidDate=f'{year}-{month}-{day}'))
            else:
                _fill_blanks(para, f'{year}年{month}月{day}日')
        _relax_signature_flow(para._p)
        return
    align = kw.get("align", WD_ALIGN_PARAGRAPH.LEFT)
    line_spacing = kw.get("line_spacing", 1.75)
    space_before = kw.get("space_before", 6)
    space_after = kw.get("space_after", 6)
    left_indent_cm = kw.get("left_indent_cm")
    _clear_runs(para)
    _clear_tab_stops(para)
    if align is not None:
        para.alignment = align
    pf = para.paragraph_format
    pf.space_before = Pt(space_before)
    pf.space_after = Pt(space_after)
    pf.line_spacing = line_spacing
    pf.first_line_indent = Cm(0)
    if left_indent_cm is not None:
        pf.left_indent = Cm(left_indent_cm)
    right_indent_cm = kw.get("right_indent_cm", 0.55)
    pf.right_indent = Cm(0.0 if right_indent_cm is None else right_indent_cm)
    _use_body_style(para)
    if kw.get("nowrap"):
        _set_word_wrap(para, enabled=False)
    if "label" in kw:
        label = str(kw.get("label") or "")
    else:
        label = "日期："
    label_width = kw.get("label_width")
    if label_width:
        label = _align_sign_label(label, int(label_width))
    soft_cap = int(kw.get("line_em") or 0)
    month = _two_digit_md(month) or month
    day = _two_digit_md(day) or day
    _form_run(para, label, underline=False)
    date_text = f"{year}年{month}月{day}日"
    room = _line_em_budget(para, label, "")
    if soft_cap >= 8 or kw.get("fill_to_edge"):
        fill_target = room if kw.get("fill_to_edge") else min(soft_cap, room)
        _underline_value(para, date_text, fill_target)
        return
    # 预算不够整段日期时，拆成年/月/日，仍不超出 room
    used = 0.0
    for val, tail in ((year, " 年 "), (month, " 月 "), (day, " 日")):
        chunk = f" {val} "
        if used + _em_len(chunk) + _em_len(tail) > room + 0.2:
            break
        _form_run(para, chunk, underline=True)
        _form_run(para, tail, underline=False)
        used += _em_len(chunk) + _em_len(tail)


def _scrub_hints(para: Paragraph, hints: tuple[str, ...]) -> None:
    for run in para.runs:
        raw = run.text
        if not raw:
            continue
        text = raw
        for hint in hints:
            text = text.replace(hint, "")
        text = text.replace("（）：", "").replace("（）", "")
        if text.strip() in {"）", "（", "："}:
            text = ""
        if text != raw:
            run.text = text


def _write_para(
    para: Paragraph,
    text: str,
    *,
    size: float | None = None,
    bold: bool | None = False,
) -> None:
    """整段替换：与正文同字号，仅下划线、不加粗。"""
    del size, bold
    align = para.alignment
    _clear_runs(para)
    run = para.add_run(text)
    _style_fill_run(run, para, underline=True)
    if align is not None:
        para.alignment = align


def _insert_paragraph_after(para: Paragraph) -> Paragraph:
    new_p = OxmlElement("w:p")
    para._element.addnext(new_p)
    return Paragraph(new_p, para._parent)


def _paragraph_has_page_break(p_el) -> bool:
    for br in p_el.iter(qn("w:br")):
        if br.get(qn("w:type")) == "page":
            return True
    return False


def _el_plain_text(el) -> str:
    return "".join(node.text or "" for node in el.iter(qn("w:t")))


def _page_body_cm(doc: Document) -> float:
    sec = doc.sections[-1]
    return max(
        16.0,
        float(sec.page_height.cm) - float(sec.top_margin.cm) - float(sec.bottom_margin.cm) - 0.6,
    )


def _drawing_height_cm(el) -> float:
    h = 0.0
    for ext in el.findall(".//" + qn("wp:extent")):
        cy = int(ext.get("cy") or 0)
        if cy > 0:
            h += cy / _EMU_PER_CM
    return h


def _tbl_height_cm(tbl) -> float:
    h = 0.0
    rows = tbl.findall(qn("w:tr"))
    for tr in rows:
        val = 0
        tr_pr = tr.find(qn("w:trPr"))
        if tr_pr is not None:
            th = tr_pr.find(qn("w:trHeight"))
            if th is not None:
                val = int(th.get(qn("w:val")) or 0)
        h += val / _TWIPS_PER_CM if val > 0 else 0.72
        h += _drawing_height_cm(tr)
    return max(h, 0.72)


def _p_spacing_cm(child) -> float:
    p_pr = child.find(qn("w:pPr"))
    if p_pr is None:
        return 0.0
    sp = p_pr.find(qn("w:spacing"))
    if sp is None:
        return 0.0
    before = int(sp.get(qn("w:before")) or 0)
    after = int(sp.get(qn("w:after")) or 0)
    return (before + after) / _TWIPS_PER_CM


def _estimate_cm_on_current_page(doc: Document, stop_el=None) -> float:
    """从本页开头估正文高度，用于把落款推到接近页脚且不翻页。"""
    used = 0.0
    body = doc.element.body
    for child in body:
        if stop_el is not None and child is stop_el:
            break
        if child.tag == qn("w:sectPr"):
            continue
        if child.tag == qn("w:tbl"):
            used += _tbl_height_cm(child)
            continue
        if child.tag != qn("w:p"):
            continue
        if _paragraph_has_page_break(child):
            used = 0.35
            continue
        p_pr = child.find(qn("w:pPr"))
        if p_pr is not None and (
            p_pr.find(qn("w:pageBreakBefore")) is not None or p_pr.find(qn("w:sectPr")) is not None
        ):
            used = 0.35
            continue
        draw_h = _drawing_height_cm(child)
        if draw_h > 0:
            used += draw_h + _p_spacing_cm(child) + 0.15
            continue
        text = _el_plain_text(child).strip()
        if not text:
            if p_pr is not None:
                sp = p_pr.find(qn("w:spacing"))
                if sp is not None and (sp.get(qn("w:lineRule")) or "") == "exact":
                    line = int(sp.get(qn("w:line")) or 0)
                    if line:
                        used += line / _TWIPS_PER_CM
                        continue
            used += 0.22
            continue
        lines = max(1, (len(text) + 29) // 30)
        used += 0.48 * lines + 0.22
        if len(text) <= 18:
            used += 0.2
    return used


def _sign_spacer_cm(
    doc: Document,
    *,
    sign_cm: float,
    stop_el=None,
    bottom_pad_cm: float | None = None,
    reserve_cm: float = 0.0,
    max_cm: float = 10.0,
) -> float:
    """No automatic signature padding; retain explicitly configured cover spacing."""
    if bottom_pad_cm is None:
        return 0.0
    # The cover renderer explicitly supplies its lower-page spacing. Ordinary
    # signature callers use the default and must not gain elastic blank space.
    used = _estimate_cm_on_current_page(doc, stop_el=stop_el)
    page = _page_body_cm(doc)
    remain = page - used
    reserve = max(0.0, float(reserve_cm or 0.0))
    cap = max(0.35, float(max_cm or 10.0))
    if remain < sign_cm + _SIGN_MIN_GAP_CM + reserve:
        return 0.0
    budget = remain - sign_cm - bottom_pad_cm - reserve
    if used > page * 0.78 or budget < 0.35:
        return 0.0
    return min(budget, cap)


def _glue_sign_off(doc: Document, spacer: Paragraph | None, lines: list[Paragraph]) -> None:
    """Allow natural breaks within a signature block; only glue split line hints."""
    del doc
    first = spacer or (lines[0] if lines else None)
    if first is None:
        return
    chain = ([spacer] if spacer is not None else []) + lines
    from api.services.tenders.pdf_format import _relax_signature_flow
    for p in chain:
        if p is None:
            continue
        p.paragraph_format.keep_with_next = False
        p.paragraph_format.keep_together = False
        _relax_signature_flow(p._p)


def _apply_sign_spacer_paragraph(para: Paragraph, height_cm: float) -> None:
    """用精确行距撑开空白。不用空表，避免预览把无边框表画成虚线方框。"""
    pf = para.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    pf.first_line_indent = Cm(0)
    pf.left_indent = Cm(0)
    pf.right_indent = Cm(0)
    p_pr = para._p.get_or_add_pPr()
    for old in list(p_pr.findall(qn("w:spacing"))):
        p_pr.remove(old)
    spacing = OxmlElement("w:spacing")
    twips = str(max(240, int(height_cm * _TWIPS_PER_CM)))
    spacing.set(qn("w:before"), "0")
    spacing.set(qn("w:after"), "0")
    spacing.set(qn("w:line"), twips)
    spacing.set(qn("w:lineRule"), "exact")
    p_pr.append(spacing)
    _clear_runs(para)
    run = para.add_run("\u200b")
    rpr = run._element.get_or_add_rPr()
    sz = OxmlElement("w:sz")
    sz.set(qn("w:val"), "2")
    rpr.append(sz)
    sz_cs = OxmlElement("w:szCs")
    sz_cs.set(qn("w:val"), "2")
    rpr.append(sz_cs)


def _add_bottom_sign_spacer(
    doc: Document,
    *,
    sign_cm: float,
    bottom_pad_cm: float | None = None,
) -> Paragraph:
    """在当前页正文后插入弹性空白，把随后落款压到接近页脚、且尽量不翻页。"""
    height = _sign_spacer_cm(doc, sign_cm=sign_cm, bottom_pad_cm=bottom_pad_cm)
    if height < 0.35:
        return None
    para = doc.add_paragraph()
    _apply_sign_spacer_paragraph(para, height)
    para.paragraph_format.keep_with_next = True
    return para


def _insert_bottom_sign_spacer_before(
    para: Paragraph,
    *,
    sign_cm: float,
    reserve_cm: float = 0.0,
    max_cm: float = 10.0,
) -> Paragraph | None:
    doc = para.part.document
    height = _sign_spacer_cm(
        doc,
        sign_cm=sign_cm,
        stop_el=para._element,
        reserve_cm=reserve_cm,
        max_cm=max_cm,
    )
    if height < 0.35:
        return None
    spacer = doc.add_paragraph()
    _apply_sign_spacer_paragraph(spacer, height)
    para._element.addprevious(spacer._element)
    spacer.paragraph_format.keep_with_next = True
    return spacer


def _insert_min_sign_gap(para: Paragraph, *, height_cm: float = 0.45) -> Paragraph:
    """弹性留白不够时，至少跟正文留一点空隙。"""
    doc = para.part.document
    spacer = doc.add_paragraph()
    _apply_sign_spacer_paragraph(spacer, max(0.35, height_cm))
    para._element.addprevious(spacer._element)
    spacer.paragraph_format.keep_with_next = True
    return spacer


def _insert_bottom_sign_spacer_after(para: Paragraph, *, sign_cm: float) -> Paragraph | None:
    doc = para.part.document
    height = _sign_spacer_cm(doc, sign_cm=sign_cm, stop_el=para._element.getnext())
    if height < 0.35:
        return None
    spacer = doc.add_paragraph()
    _apply_sign_spacer_paragraph(spacer, height)
    para._element.addnext(spacer._element)
    spacer.paragraph_format.keep_with_next = True
    return spacer


def _write_plain(para: Paragraph, text: str, *, size: float = 12, bold: bool = False) -> None:
    _clear_runs(para)
    run = para.add_run(text)
    _font(run, size, bold=bold)


def _tech_drawing_files(catalog_media: dict | None) -> list[Path]:
    if isinstance(catalog_media, dict):
        return list(catalog_media.get(TECH_DRAWING_KEY) or [])
    return []


def _insert_picture_after(para: Paragraph, source, *, width_cm: float = 15.5) -> Paragraph:
    pic = _insert_paragraph_after(para)
    pic.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pic.paragraph_format.space_before = Pt(4)
    pic.paragraph_format.space_after = Pt(4)
    pic.add_run().add_picture(source, width=Cm(width_cm))
    return pic


_DRAWING_MAX_FILES = 12
_DRAWING_PDF_PAGES = 4


def _insert_drawings_after(
    cursor: Paragraph, files: list[Path], *, max_pages: int = _DRAWING_PDF_PAGES
) -> tuple[Paragraph, int]:
    """按排版尺寸准备高清图纸并嵌入。"""

    from api.services.tenders.placeholders import _render_pdf_page_jpeg
    from api.services.cablepkg.image_quality import image_png

    inserted = 0
    for path in files[:_DRAWING_MAX_FILES]:
        suf = path.suffix.lower()
        try:
            if suf in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"} and path.is_file():
                from PIL import Image

                pic = _insert_paragraph_after(cursor)
                pic.alignment = WD_ALIGN_PARAGRAPH.CENTER
                pic.paragraph_format.space_before = Pt(4)
                pic.paragraph_format.space_after = Pt(4)
                with Image.open(path) as raw:
                    buf = image_png(raw, width_cm=15.5, height_cm=15.5 * raw.height / raw.width)
                    pic.add_run().add_picture(buf, width=Cm(15.5))
                cursor = pic
                inserted += 1
                continue
            if suf != ".pdf" or not path.is_file():
                continue
            try:
                import pypdfium2 as pdfium
            except ImportError:
                logger.warning("pypdfium2 missing, skip drawing pdf %s", path.name)
                continue
            pdf = pdfium.PdfDocument(str(path))
            count = min(len(pdf), max_pages)
            for i in range(count):
                page = pdf[i]
                buf = _render_pdf_page_jpeg(page)
                cursor = _insert_picture_after(cursor, buf)
                inserted += 1
        except Exception:
            logger.exception("insert tech drawing failed: %s", path)
    return cursor, inserted


def _embed_tech_drawings(doc: Document, after: Paragraph, catalog_media: dict | None) -> list[str]:
    files = [p for p in _tech_drawing_files(catalog_media) if p.is_file()]
    n = 0
    if files:
        _, n = _insert_drawings_after(after, files)
    if n > 0:
        notes = [f"已将 {n} 张实施方案图纸写入技术标"]
        skipped = max(0, len(files) - _DRAWING_MAX_FILES)
        if skipped:
            notes.append(f"另有 {skipped} 个图纸文件未写入，装订时可另附")
        return notes
    draw_placeholder_box(doc, TECH_DRAWING_SLOT, after=after)
    if files:
        return ["实施方案图纸未能写入 Word，已用虚线框占位"]
    return ["技术标实施方案尚未上传图纸，已用虚线框占位"]


def _is_toc_list_line(para: Paragraph) -> bool:
    """目录条目：一、 / 1.1 / 第一章。不要把正文里 List Paragraph 标题当成目录。"""
    style = para.style.name if para.style else ""
    if style in {"toc 1", "TOC 1", "toc 2", "TOC 2"}:
        return True
    raw = (para.text or "").strip()
    if re.match(r"^[一二三四五六七八九十]+[、.．]", raw):
        return True
    if re.match(r"^\d+(?:\.\d+)+", raw):
        return True
    if re.match(r"^第[一二三四五六七八九十0-9]+章", raw):
        return True
    return False


def _find_technical_heading(doc: Document) -> Paragraph | None:
    """定位正文「其他材料 / 技术标」标题，跳过目录里的同名条目。"""
    candidates: list[Paragraph] = []
    for para in doc.paragraphs:
        if _compact(para.text) not in {"其他材料", "技术标实施方案"}:
            continue
        if _is_toc_list_line(para):
            continue
        style = para.style.name if para.style else ""
        if style.startswith("Heading") or style in {"Title", "Body Text"}:
            return para
        candidates.append(para)
    return candidates[0] if candidates else None


def _fill_technical_section(doc: Document, brief: BidBrief, catalog_media: dict | None = None) -> list[str]:
    heading = _find_technical_heading(doc)
    if heading is None:
        return []
    _write_plain(heading, "技术标（实施方案）", size=16, bold=True)
    _bookmark_paragraph(heading, "toc_other")
    cursor = heading
    intro = _insert_paragraph_after(cursor)
    _write_plain(
        intro,
        "本部分为技术标。按本次招标要求提供方案说明及所需图纸。"
        "不响应技术要求会大量扣分。公司资格、业绩、报价与函件见商务标及附件。",
        size=12,
        bold=False,
    )
    cursor = intro
    sub = _insert_paragraph_after(cursor)
    _write_plain(sub, "一、文字描述", size=14, bold=True)
    _bookmark_paragraph(sub, "toc_other_text")
    body = _insert_paragraph_after(sub)
    _write_plain(body, tech_plan_body(brief), size=12, bold=False)
    cursor = body
    draw_title = _insert_paragraph_after(cursor)
    _write_plain(draw_title, "二、图纸", size=14, bold=True)
    _bookmark_paragraph(draw_title, "toc_other_draw")
    cursor = draw_title
    warnings = _embed_tech_drawings(doc, draw_title, catalog_media)
    if not tech_plan_text(brief):
        warnings.append("技术标实施方案文字说明待补，已在 Word 中标注或按供货期写入工期")
    return warnings


def _drop_para(para: Paragraph) -> None:
    el = para._element
    parent = el.getparent()
    if parent is not None:
        parent.remove(el)


def _strip_para_bottom(para: Paragraph) -> None:
    p_pr = para._p.find(qn("w:pPr"))
    if p_pr is None:
        return
    p_bdr = p_pr.find(qn("w:pBdr"))
    if p_bdr is not None:
        p_pr.remove(p_bdr)


def _no_underline(run) -> None:
    run.underline = False
    rpr = run._element.get_or_add_rPr()
    for uel in list(rpr.findall(qn("w:u"))):
        rpr.remove(uel)
    uel = OxmlElement("w:u")
    uel.set(qn("w:val"), "none")
    rpr.append(uel)


def _keep_one_para(cell: _Cell) -> None:
    tc = cell._tc
    paras = [child for child in list(tc) if child.tag == qn("w:p")]
    for extra in paras[1:]:
        tc.remove(extra)


def _write_cell(
    cell: _Cell,
    text: str,
    *,
    size: float = 10.5,
    bold: bool = False,
    underline: bool = True,
    center: bool = False,
    bottom_line: bool = False,
    tight: bool = False,
) -> None:
    """单元格填空：默认下划线、不加粗；表头等需加粗时显式传 bold=True。"""
    cell.text = ""
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    para = cell.paragraphs[0]
    _strip_para_bottom(para)
    pad = Pt(0) if tight else Pt(2)
    para.paragraph_format.space_before = pad
    para.paragraph_format.space_after = pad
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER if center else WD_ALIGN_PARAGRAPH.LEFT
    lines = (text or "").split("\n")
    for i, line in enumerate(lines):
        if i:
            para.add_run().add_break()
        run = para.add_run(line)
        # 填空内容不加粗；仅表头等显式 bold=True 时加粗。字号不大于正文常用值。
        _font(run, size, bold=bool(bold))
        if underline:
            run.underline = True
        else:
            run.underline = False
            rpr_u = run._element.get_or_add_rPr()
            for uel in list(rpr_u.findall(qn("w:u"))):
                rpr_u.remove(uel)
        if not bold:
            rpr = run._element.get_or_add_rPr()
            _clear_bold(rpr)
    if bottom_line:
        _cell_bottom_line(para)


def _cell_bottom_line(para: Paragraph) -> None:
    p_pr = para._p.get_or_add_pPr()
    p_bdr = p_pr.find(qn("w:pBdr"))
    if p_bdr is None:
        p_bdr = OxmlElement("w:pBdr")
        p_pr.append(p_bdr)
    for old in list(p_bdr):
        if old.tag == qn("w:bottom"):
            p_bdr.remove(old)
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "12")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "000000")
    p_bdr.append(bottom)


def _two_digit_md(part: str) -> str:
    """月、日个位数补 0，如 9 → 09。"""
    digits = "".join(ch for ch in (part or "") if ch.isdigit())
    if not digits:
        return part or ""
    try:
        n = int(digits)
    except ValueError:
        return part
    if 1 <= n <= 31:
        return f"{n:02d}"
    return digits


def _ymd(iso: str) -> tuple[str, str, str]:
    raw = (iso or "").strip()
    parts = raw.replace("/", "-").split("-")
    if len(parts) >= 3:
        month = _two_digit_md(parts[1]) or "01"
        day = _two_digit_md(parts[2]) or "01"
        return parts[0], month, day
    return "　　", "　", "　"


def _date_line(iso: str) -> str:
    year, month, day = _ymd(iso)
    return f"{year}    年    {month}    月    {day}    日"


def _set_dot_tab(paragraph: Paragraph, pos_cm: float, *, normalize_runs: bool = False) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    for old in p_pr.findall(qn("w:tabs")):
        p_pr.remove(old)
    tabs = OxmlElement("w:tabs")
    tab = OxmlElement("w:tab")
    tab.set(qn("w:val"), "right")
    tab.set(qn("w:leader"), "dot")
    tab.set(qn("w:pos"), str(int(Cm(pos_cm).twips)))
    tabs.append(tab)
    p_pr.append(tabs)
    if normalize_runs:
        # 原格式章复制来的左制表位和 Tab 字体不能与新增页码行混用。
        title_nodes = []
        for run in paragraph.runs:
            if any(True for _ in run._r.iter(qn("w:tab"))):
                _set_dot_leader_font(run)
                _no_underline(run)
            if not title_nodes or title_nodes[-1] is not None:
                if (next(run._r.iter(qn("w:tab")), None) is not None
                        or next(run._r.iter(qn("w:fldChar")), None) is not None):
                    title_nodes.append(None)
                else:
                    title_nodes.extend(run._r.iter(qn("w:t")))
        label_nodes = title_nodes[:title_nodes.index(None)] if None in title_nodes else title_nodes
        label = "".join(node.text or "" for node in label_nodes)
        leader = re.search(r"[.．·…]{3,}\s*$", label)
        if leader:
            remove = len(label) - leader.start()
            for node in reversed(label_nodes):
                old = node.text or ""
                count = min(remove, len(old))
                node.text = old[:len(old) - count]
                remove -= count
                if not remove:
                    break


def _toc_tab_pos_cm(para: Paragraph) -> float:
    for section in para.part.document.sections:
        usable = float(section.page_width.cm) - float(section.left_margin.cm) - float(section.right_margin.cm)
        if usable > 10:
            return max(usable - 0.15, 12.0)
    return 16.8


def _strip_toc_numbering(para: Paragraph) -> None:
    try:
        para.style = "Normal"
    except (KeyError, ValueError):
        pass
    p_pr = para._p.get_or_add_pPr()
    for tag in ("w:numPr", "w:ind", "w:pBdr"):
        for el in p_pr.findall(qn(tag)):
            p_pr.remove(el)
    rpr = p_pr.find(qn("w:rPr"))
    if rpr is None:
        rpr = OxmlElement("w:rPr")
        p_pr.append(rpr)
    for uel in list(rpr.findall(qn("w:u"))):
        rpr.remove(uel)
    none = OxmlElement("w:u")
    none.set(qn("w:val"), "none")
    rpr.append(none)
    para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    fmt = para.paragraph_format
    fmt.space_before = Pt(2)
    fmt.space_after = Pt(2)


def _set_dot_leader_font(run) -> None:
    """点线必须用西文字体，宋体会被 WPS 画成实线。"""
    run.bold = False
    run.underline = False
    run.font.size = Pt(10.5)
    run.font.color.rgb = RGBColor(0, 0, 0)
    rpr = run._element.get_or_add_rPr()
    for uel in list(rpr.findall(qn("w:u"))):
        rpr.remove(uel)
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:ascii"), "Times New Roman")
    rfonts.set(qn("w:hAnsi"), "Times New Roman")
    rfonts.set(qn("w:cs"), "Times New Roman")


def _next_bookmark_id(doc: Document) -> int:
    # 缓存在 document part，避免每次打书签都全表扫描
    cache_attr = "_weitai_next_bookmark_id"
    cached = getattr(doc, cache_attr, None)
    if isinstance(cached, int):
        setattr(doc, cache_attr, cached + 1)
        return cached
    used: list[int] = []
    for el in doc.element.iter(qn("w:bookmarkStart")):
        raw = el.get(qn("w:id"))
        if raw is None:
            continue
        try:
            used.append(int(raw))
        except ValueError:
            continue
    nxt = (max(used) + 1) if used else 1
    setattr(doc, cache_attr, nxt + 1)
    return nxt


def _set_bookmark_outline(doc: Document, levels: dict[str, int]) -> None:
    """只补正文标题的大纲元数据，不改变样式、字号、间距。"""
    for para in doc.paragraphs:
        for mark in para._p.findall(qn("w:bookmarkStart")):
            level = levels.get(mark.get(qn("w:name")))
            if level is None:
                continue
            ppr = para._p.get_or_add_pPr()
            for old in list(ppr.findall(qn("w:outlineLvl"))):
                ppr.remove(old)
            node = OxmlElement("w:outlineLvl")
            node.set(qn("w:val"), str(max(0, min(8, int(level) - 1))))
            ppr.append(node)


def _bookmark_paragraph(para: Paragraph, name: str) -> None:
    """在标题段打书签，供目录 PAGEREF 定位。"""
    body = para._p.getparent()
    if body is None:
        return
    # 只清理本段上的同名书签，避免每次全文档 iter（分页书签多时极慢）
    for start in list(para._p.findall(qn("w:bookmarkStart"))):
        if start.get(qn("w:name")) != name:
            continue
        bid = start.get(qn("w:id"))
        para._p.remove(start)
        for end in list(para._p.findall(qn("w:bookmarkEnd"))):
            if end.get(qn("w:id")) == bid:
                para._p.remove(end)
                break
    bookmark_id = str(_next_bookmark_id(para.part.document))
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), bookmark_id)
    start.set(qn("w:name"), name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), bookmark_id)
    para._p.insert(0, start)
    para._p.append(end)


def _subset_bookmarks(doc: Document, items) -> list[str]:
    """目录与正文共用分配：缺失、重复或不合法 ID 不生成同名书签。"""
    used = {el.get(qn("w:name")) for el in doc.element.iter(qn("w:bookmarkStart"))}
    names: list[str] = []
    for i, item in enumerate(items):
        stem = "toc_" + re.sub(r"[^\w]", "_", item.id or f"o{i + 1:02d}")[:30]
        name = stem
        suffix = 2
        while name in used:
            name = f"{stem}_{suffix}"
            suffix += 1
        used.add(name)
        names.append(name)
    return names


def _ensure_subset_toc_caches(doc: Document) -> None:
    """仅子集兜底；不可更新的引用显示明确占位，避免空白或伪造第 1 页。"""
    for para in doc.paragraphs:
        bookmark = _pageref_bookmark(para)
        if not bookmark or not bookmark.startswith("toc_"):
            continue
        in_result = False
        texts = []
        separator = None
        for run in para._p.iter(qn("w:r")):
            field = run.find(qn("w:fldChar"))
            if field is not None:
                kind = field.get(qn("w:fldCharType"))
                if kind == "separate":
                    in_result = True
                    separator = run
                elif kind == "end":
                    in_result = False
                continue
            if in_result:
                texts.extend(run.findall(qn("w:t")))
        if any((node.text or "").strip() for node in texts):
            continue
        if texts:
            texts[0].text = "待更新"
        elif separator is not None:
            result = OxmlElement("w:r")
            node = OxmlElement("w:t")
            node.text = "待更新"
            result.append(node)
            separator.addnext(result)


def _add_pageref_run(para: Paragraph, bookmark: str, cache: str) -> None:
    """插入 PAGEREF 域，打开 Word/WPS 后按实际页码更新。"""
    begin = para.add_run()
    fld = OxmlElement("w:fldChar")
    fld.set(qn("w:fldCharType"), "begin")
    begin._r.append(fld)
    _font(begin, 10.5)
    _no_underline(begin)

    instr = para.add_run()
    text = OxmlElement("w:instrText")
    text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    text.text = f" PAGEREF {bookmark} \\h "
    instr._r.append(text)
    _font(instr, 10.5)
    _no_underline(instr)

    sep = para.add_run()
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    sep._r.append(fld_sep)
    _font(sep, 10.5)
    _no_underline(sep)

    cache_run = para.add_run(cache)
    _font(cache_run, 10.5)
    _no_underline(cache_run)

    end = para.add_run()
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    end._r.append(fld_end)
    _font(end, 10.5)
    _no_underline(end)


def _clear_toc_tabs(para: Paragraph) -> None:
    p_pr = para._p.get_or_add_pPr()
    for old in p_pr.findall(qn("w:tabs")):
        p_pr.remove(old)


def _apply_toc_indent(para: Paragraph, level: int, *, preserve_spacing: bool = False) -> None:
    pf = para.paragraph_format
    pf.left_indent = Cm(0.74 * max(0, int(level) - 1))
    pf.first_line_indent = Cm(0)
    if not preserve_spacing:
        pf.space_before = Pt(3 if level <= 1 else 1)
        pf.space_after = Pt(3 if level <= 1 else 1)


def _write_toc_line(
    para: Paragraph,
    index: int,
    title: str,
    bookmark: str,
    cache: str,
    *,
    label: str | None = None,
    with_pages: bool = True,
    level: int = 1,
    preserve_title: bool = False,
) -> None:
    title = title.strip() if preserve_title else toc_display_title(title)
    if not title:
        _clear_runs(para)
        return
    _strip_toc_numbering(para)
    _apply_toc_indent(para, level)
    if label is not None:
        prefix = label
    else:
        prefix = f"{cn_ordinal(index + 1)}、"
    size = 12.0 if level <= 1 else 10.5
    bold = level <= 1
    # with_pages 留作调用兼容；每条目录都保留点线和页码域，未估页用 cache 占位。
    _set_dot_tab(para, _toc_tab_pos_cm(para))
    _clear_runs(para)
    title_run = para.add_run(f"{prefix}{title}")
    _font(title_run, size, bold=bold)
    _no_underline(title_run)
    tab_run = para.add_run()
    _set_dot_leader_font(tab_run)
    _no_underline(tab_run)
    tab_run.add_tab()
    _add_pageref_run(para, bookmark, cache)


def _chapter5_toc_tree(brief: BidBrief) -> list[dict]:
    note = tech_plan_text(brief)
    chaps = tech_chapter_titles(note)
    if len(chaps) >= 2:
        tech_kids = [{"title": t, "bookmark": "toc_other"} for t in chaps]
    else:
        tech_kids = [
            {"title": "文字描述", "bookmark": "toc_other_text"},
            {"title": "图纸", "bookmark": "toc_other_draw"},
        ]
    tree = [
        {
            "title": "投标函及投标函附录",
            "bookmark": "toc_letter",
            "children": [
                {"title": "投标函", "bookmark": "toc_letter"},
                {
                    "title": "投标函附录",
                    "bookmark": "toc_letter_app",
                    "children": [
                        {"title": "分项报价表", "bookmark": "toc_quote"},
                        {"title": "支付条件", "bookmark": "toc_pay"},
                    ],
                },
            ],
        },
        {
            "title": "法定代表人身份证明及授权委托书",
            "bookmark": "toc_legal",
            "children": [
                {"title": "法定代表人身份证明", "bookmark": "toc_legal"},
                {"title": "授权委托书", "bookmark": "toc_auth"},
            ],
        },
        {"title": "技术偏差表", "bookmark": "toc_dev"},
        {"title": "企业业绩", "bookmark": "toc_perf"},
        {"title": "原厂生产承诺", "bookmark": "toc_factory"},
        {
            "title": "技术标（实施方案）",
            "bookmark": "toc_other",
            "children": tech_kids,
        },
    ]
    if brief.includeCommitment:
        tree.append({"title": "投标承诺书", "bookmark": "toc_commit"})
    return tree


def _toc_region(doc: Document) -> tuple[Paragraph | None, list[Paragraph]]:
    """旧目录区：目录标题后到目录条目结束，或显式换页/分节/Heading 正文为止。"""
    toc_title: Paragraph | None = None
    lines: list[Paragraph] = []
    marked = False
    for para in doc.paragraphs:
        n = _compact(para.text)
        if n == "目录":
            toc_title = para
            continue
        if toc_title is None:
            continue
        # Body boundaries take precedence over the permissive legacy TOC matcher.
        style = para.style.name if para.style else ''
        explicit_entry = 'PAGEREF' in para._p.xml or style.upper().startswith('TOC')
        if not explicit_entry and (
            (style.startswith('Heading') and n)
            or _p_sectpr(para._p) is not None
            or _p_has_page_br(para._p) or _has_page_break_before(para._p)
        ):
            break
        if _looks_like_toc_entry(para):
            marked = True
            lines.append(para)
            continue
        if marked:
            text = (para.text or "").strip()
            if not text or any(mark in text for mark in ("备注", "应编制", "页码及内容")):
                lines.append(para)
                continue
            break
        style = para.style.name if para.style else ""
        if style.startswith("Heading") and n and n != "目录":
            break
        if _p_sectpr(para._p) is not None:
            break
        if _p_has_page_br(para._p) or _has_page_break_before(para._p):
            break
        lines.append(para)
    return toc_title, lines


def _scrub_toc_title_bookmarks(toc_title: Paragraph) -> None:
    for start in list(toc_title._p.findall(qn("w:bookmarkStart"))):
        name = start.get(qn("w:name")) or ""
        if name.startswith("toc_"):
            continue
        bid = start.get(qn("w:id"))
        toc_title._p.remove(start)
        for end in list(toc_title._p.findall(qn("w:bookmarkEnd"))):
            if end.get(qn("w:id")) == bid:
                toc_title._p.remove(end)


_TOC_TITLE_ALIASES = {
    "其他材料": "技术标",
    "技术标": "技术标",
    "技术标实施方案": "技术标",
    "技术标（实施方案）": "技术标",
    "企业业绩": "企业业绩",
    "近年完成的类似项目": "企业业绩",
    "类似项目业绩": "企业业绩",
    "类似业绩": "企业业绩",
    "技术偏差表": "技术偏差表",
    "技术偏离表": "技术偏差表",
    "商务和技术偏差表": "技术偏差表",
    "技术和商务差异表": "技术偏差表",
    "投标承诺书": "投标承诺书",
    "投标承诺函": "投标承诺书",
    "响应承诺书": "投标承诺书",
    "响应承诺函": "投标承诺书",
    "分项报价表": "分项报价表",
    "投标报价及分项报价表": "分项报价表",
    "投标报价表": "分项报价表",
    "原厂生产承诺": "原厂生产承诺",
    "原厂生产承诺书": "原厂生产承诺",
    "原厂承诺": "原厂生产承诺",
    "法定代表人身份证明": "法定代表人身份证明",
    "法定代表人身份证明文件": "法定代表人身份证明",
    "授权委托书": "授权委托书",
    "法定代表人授权委托书": "授权委托书",
}


def _toc_match_key(text: str) -> str:
    """目录与正文对齐用的标题键；仅做称谓归一，不改显示文字。"""
    key = toc_title_key(text)
    if key in _TOC_TITLE_ALIASES:
        return _TOC_TITLE_ALIASES[key]
    if "近年完成的类似项目" in key or "类似项目业绩" in key or "企业业绩" in key:
        return "企业业绩"
    if key.startswith("技术偏差") or key.startswith("技术偏离"):
        return "技术偏差表"
    if key.startswith("投标承诺") or key.startswith("响应承诺"):
        return "投标承诺书"
    return key


def _looks_like_toc_entry(para: Paragraph) -> bool:
    """目录行只认 PAGEREF 或 TOC 样式，避免把带「一、」的正文标题当成目录。"""
    if _pageref_bookmark(para) is not None:
        return True
    style = para.style.name if para.style else ""
    return style.lower().startswith("toc")


def _active_outline_items(brief: BidBrief | None) -> list:
    if brief is None:
        return []
    out = []
    for item in brief.outlineItems or []:
        title = (item.title or "").strip()
        if item.skipped or not title:
            continue
        if _compact(title) in {"目录", "封面"}:
            continue
        out.append(item)
    return out


def _body_paragraphs_after_toc(doc: Document, old: list[Paragraph]) -> list[Paragraph]:
    """目录区之后的正文档落；正文标题常带「一、」，不能再靠 _is_toc_list_line 判断。"""
    paragraphs = doc.paragraphs
    toc_idx = next(
        (i for i, para in enumerate(paragraphs) if _compact(para.text) == "目录"),
        None,
    )
    if toc_idx is None:
        return []
    old_elements = [para._p for para in old]
    body: list[Paragraph] = []
    for para in paragraphs[toc_idx + 1 :]:
        if any(para._p is element for element in old_elements):
            continue
        if _looks_like_toc_entry(para):
            continue
        body.append(para)
    return body


def _find_outline_body_heading(
    paragraphs: list[Paragraph],
    title: str,
    used: list[Paragraph],
) -> Paragraph | None:
    key = _toc_match_key(title)
    if not key:
        return None

    def available(para: Paragraph) -> bool:
        return para not in used and not _looks_like_toc_entry(para)

    bookmark = next(
        (bm for name, bm in _HEADING_BOOKMARKS if _compact(name) == _compact(title)),
        None,
    )
    if bookmark:
        for para in paragraphs:
            if not available(para):
                continue
            if any(
                start.get(qn("w:name")) == bookmark
                for start in para._p.findall(qn("w:bookmarkStart"))
            ):
                return para

    for para in paragraphs:
        if available(para) and _toc_match_key(para.text) == key:
            return para
    if len(key) < 4:
        return None
    for para in paragraphs:
        if not available(para):
            continue
        other = _toc_match_key(para.text)
        if len(other) >= 4 and (other in key or key in other):
            return para
    return None


def _ensure_outline_bookmark(
    para: Paragraph,
    item,
    assigned: set[str],
    all_names: set[str],
) -> str:
    """正文章节已有 toc_ 书签就复用；否则用大纲条目 ID 生成稳定书签。"""
    for start in para._p.findall(qn("w:bookmarkStart")):
        name = start.get(qn("w:name")) or ""
        if name.startswith("toc_") and name not in assigned:
            assigned.add(name)
            return name
    stem = "toc_" + re.sub(r"[^\w]", "_", (getattr(item, "id", "") or "").strip())[:30]
    if stem in {"toc_", "toc"}:
        stem = "toc_" + re.sub(r"[^\w]", "_", _toc_match_key(item.title) or "section")[:24]
    name = stem
    suffix = 2
    while name in all_names or name in assigned:
        name = f"{stem}_{suffix}"
        suffix += 1
    all_names.add(name)
    assigned.add(name)
    _bookmark_paragraph(para, name)
    return name


def _outline_toc_tree(matched: list[tuple]) -> list[dict]:
    levels = [max(1, min(4, int(getattr(item, "level", 1) or 1))) for item, _bm, _para in matched]
    base = min(levels) if levels else 1
    roots: list[dict] = []
    stack: list[tuple[int, dict]] = []
    for (item, bookmark, para), level in zip(matched, levels, strict=True):
        level = max(1, level - base + 1)
        title = (item.title or "").strip() or (para.text or "").strip()
        node = {"title": title, "bookmark": bookmark}
        if title_canon(title)[0]:
            node["bare"] = True
        while stack and stack[-1][0] >= level:
            stack.pop()
        if stack:
            stack[-1][1].setdefault("children", []).append(node)
        else:
            roots.append(node)
        stack.append((level, node))
    return roots


def _outline_toc_rows(
    doc: Document,
    brief: BidBrief,
    scheme: str,
    old: list[Paragraph],
) -> tuple[list[tuple[str, str, int, str]], list[str]]:
    """目录与正文同源：只收录 outlineItems 中、且正文确有同名标题的章节。"""
    items = _active_outline_items(brief)
    if not items:
        return [], []
    body = _body_paragraphs_after_toc(doc, old)
    if not body:
        return [], []
    all_names = {
        start.get(qn("w:name"))
        for start in doc.element.iter(qn("w:bookmarkStart"))
        if start.get(qn("w:name"))
    }
    assigned: set[str] = set()
    used: list[Paragraph] = []
    matched: list[tuple] = []
    skipped: list[str] = []
    for item in items:
        para = _find_outline_body_heading(body, item.title, used)
        if para is None:
            skipped.append(item.title)
            continue
        bookmark = _ensure_outline_bookmark(para, item, assigned, all_names)
        used.append(para)
        matched.append((item, bookmark, para))
    if not matched:
        return [], skipped
    return flatten_toc_tree(_outline_toc_tree(matched), scheme), skipped


def _rebuild_toc(
    doc: Document, brief: BidBrief, *, with_pages: bool, fmt=None
) -> list[str]:
    toc_title, old = _toc_region(doc)
    if toc_title is None:
        return []
    _scrub_toc_title_bookmarks(toc_title)
    scheme = fmt.tocNumbering if fmt is not None else "cn"
    rows, skipped = _outline_toc_rows(doc, brief, scheme, old)
    if not rows:
        # 没有可用大纲时保留原固定模板目录，避免生成空目录。
        rows = flatten_toc_tree(_chapter5_toc_tree(brief), scheme)
    for para in old:
        _drop_para(para)
    anchor = toc_title
    for i, (label, title, level, bm) in enumerate(rows):
        cache = toc_page_cache(i, fmt) if fmt is not None else "1"
        neo = _insert_paragraph_after(anchor)
        _write_toc_line(
            neo,
            i,
            title,
            bm,
            cache,
            label=label,
            with_pages=with_pages,
            level=level,
        )
        anchor = neo
    return skipped


def _ensure_toc_entries(
    doc: Document,
    *,
    include_commitment: bool = True,
    with_pages: bool = True,
    fmt=None,
    brief: BidBrief | None = None,
) -> list[str]:
    if brief is None:
        brief = BidBrief(includeCommitment=include_commitment)
    return _rebuild_toc(doc, brief, with_pages=with_pages, fmt=fmt)


# 模板第 9、12 节 header=0，页眉贴顶，OnlyOffice/部分 Word 会裁掉字头只剩横线。
_HEADER_DISTANCE = Cm(1.75)
_HEADER_TOP_MIN = Cm(2.6)


def _header_bottom_border(para: Paragraph) -> None:
    p_pr = para._p.get_or_add_pPr()
    for old in list(p_pr.findall(qn("w:pBdr"))):
        p_pr.remove(old)
    p_bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    # space 过大会把文字往页顶推，裁切更明显；1pt 让字紧贴横线
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), "4F4F4F")
    p_bdr.append(bottom)
    p_pr.append(p_bdr)


def _reset_header_para(para: Paragraph) -> None:
    fmt = para.paragraph_format
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(0)
    fmt.line_spacing = 1.0
    fmt.line_spacing_rule = WD_LINE_SPACING.SINGLE


def _write_header_para(para: Paragraph, project_name: str) -> None:
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _reset_header_para(para)
    title = (project_name or "").strip()
    if len(title) > 28:
        title = title[:28] + "…"
    _clear_runs(para)
    if title:
        run = para.add_run(title)
        _font(run, 10.5)
    _header_bottom_border(para)


def _ensure_header(section, project_name: str, *, cover: bool = False) -> None:
    """每一节都写页眉。封面节首页留空，其后各页显示项目名。"""
    section.header_distance = _HEADER_DISTANCE
    try:
        top = int(section.top_margin or 0)
    except (TypeError, ValueError):
        top = 0
    if top < int(_HEADER_TOP_MIN):
        section.top_margin = _HEADER_TOP_MIN
    header = section.header
    header.is_linked_to_previous = False
    para = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
    _write_header_para(para, project_name)
    if not cover:
        section.different_first_page_header_footer = False
        return
    section.different_first_page_header_footer = True
    first = section.first_page_header
    first.is_linked_to_previous = False
    first_para = first.paragraphs[0] if first.paragraphs else first.add_paragraph()
    _reset_header_para(first_para)
    _clear_runs(first_para)


def _deviation_rows(brief: BidBrief) -> list[tuple[str, str, str, str]]:
    lines = list(brief.deviationLines or [])
    if not lines:
        lines = [
            DeviationLine(
                seq="1",
                requirement="【待响应】尚未抽出技术条款",
                response="【待响应】",
                deviation="\u5f85\u786e\u8ba4",
            ),
        ]
    rows: list[tuple[str, str, str, str]] = []
    for i, line in enumerate(lines, start=1):
        req = (line.requirement or "").strip()
        if not req:
            continue
        rows.append(
            (
                str(i),
                req,
                (line.response or "【待响应】").strip() or "【待响应】",
                (line.deviation or "\u5f85\u786e\u8ba4").strip() or "\u5f85\u786e\u8ba4",
            )
        )
    return rows or [("1", "【待响应】尚未抽出技术条款", "【待响应】", "\u5f85\u786e\u8ba4")]


def _technical_dev_rows(brief: BidBrief) -> list[tuple[str, str, str, str, str]]:
    """Build technical deviations from parameter excerpts, independently of business rows."""
    pattern = re.compile(
        r"(?P<name>[\u4e00-\u9fff]{2,12}[：:]?)?\s*"
        r"(?P<val>[≤≥<>＝=±]?\s*[\d.]+(?:\s*[~～-]\s*[\d.]+)?\s*"
        r"(?:mm|kV|V|A|Ω|℃|kg|m|%|次|年|min|h)?)"
    )
    rows: list[tuple[str, str, str, str, str]] = []
    for item in brief.outlineItems or []:
        if item.kind not in {"tech_dev", "tech_chapter", "tech_plan"}:
            continue
        source = (item.inviteExcerpt or item.body or "").strip()
        for match in pattern.finditer(source):
            value = match.group("val").strip()
            if not value or not re.search(r"[≤≥±]", value):
                continue
            name = (match.group("name") or "参数项").rstrip("：:").strip()
            clause = f"{item.title}"
            rows.append((str(len(rows) + 1), clause, f"{name}：{value}", f"完全响应，满足{name}{value}", "无偏差"))
    if rows:
        return rows
    return [("1", "", "其他：—", "", "")]


def _commercial_dev_rows(brief: BidBrief) -> list[tuple[str, ...]]:
    """商务偏离只写交货/质保/付款等条款，不用报价单技术参数顶。"""
    if getattr(brief, "bizDeviationLines", None):
        rows = []
        for index, line in enumerate(brief.bizDeviationLines, 1):
            requirement = (line.requirement or "").strip()
            if not requirement:
                continue
            clause = requirement if re.search(r"\b\d+[.．]\d+", requirement) else f"商务条款-{index}"
            response = (line.response or "").strip()
            rows.append((str(len(rows) + 1), clause, response, (line.deviation or "\u5f85\u786e\u8ba4").strip(), (line.explanation or "").strip()))
        if rows:
            return rows
    rows: list[tuple[str, str, str, str]] = []

    def add(title: str, req: str, resp: str) -> None:
        req = (req or "").strip()
        if not req:
            return
        rows.append((str(len(rows) + 1), f"{title}：{req}", (resp or req).strip(), "无偏差"))

    if brief.deliveryDays:
        add("交货期", f"{brief.deliveryDays}天内交货", f"响应：中标通知后{brief.deliveryDays}天内交货")
    if brief.warrantyYears:
        add("质保期", f"{brief.warrantyYears}年", f"响应：质保期{brief.warrantyYears}年")
    if brief.bidValidityDays:
        add("投标有效期", f"{brief.bidValidityDays}日历天", f"响应：投标有效期{brief.bidValidityDays}日历天")
    quality = (brief.quality or "").strip()
    if quality:
        add("质量要求", quality, f"响应：{quality}")
    pay = []
    if brief.prepaidPct:
        pay.append(f"预付款{brief.prepaidPct}%")
    if brief.arrivalPct:
        pay.append(f"到货款{brief.arrivalPct}%")
    if brief.settlementPct:
        pay.append(f"验收款{brief.settlementPct}%")
    if brief.warrantyPct:
        pay.append(f"质保金{brief.warrantyPct}%")
    if pay:
        add("付款条件", "、".join(pay), "响应：" + "、".join(pay))
    return rows or [("1", "按招标文件商务条款执行", "完全响应招标文件商务条款，无偏差", "无偏差")]


def infer_factory_role(brief: BidBrief) -> str:
    role = (brief.factoryRole or "").strip()
    if role:
        return role
    return "投标产品生产厂商"


def _factory_addressee(brief: BidBrief) -> str:
    """致送对象：优先招标人，空则用项目名称（投标项目名称）。"""
    return (brief.tenderer or "").strip() or (brief.projectName or "").strip()


def _is_factory_body(n: str) -> bool:
    if not n:
        return False
    if any(mark in n for mark in ("见模", "投标产品生产厂商", "承担原厂责任")):
        return True
    if n.endswith("：") and any(k in n for k in ("公司", "局", "中心", "院")):
        return True
    return False


def _add_factory_run(para: Paragraph, text: str, *, underline: bool) -> None:
    if not text:
        return
    run = para.add_run(text)
    _style_fill_run(run, para, underline=underline)


def _write_factory_commitment(para: Paragraph, brief: BidBrief) -> Paragraph:
    """原厂承诺：致送单位单独一行，正文缩进，仅填空处下划线。返回正文段，便于后续落款。"""
    addressee = _factory_addressee(brief) or "　　　　"
    project = (brief.projectName or "").strip()
    _clear_runs(para)
    para.alignment = WD_ALIGN_PARAGRAPH.LEFT
    pf = para.paragraph_format
    pf.first_line_indent = Cm(0)
    pf.space_before = Pt(12)
    pf.space_after = Pt(8)
    pf.line_spacing = 1.5
    _add_factory_run(para, addressee, underline=True)
    _add_factory_run(para, "：", underline=False)

    body = _insert_paragraph_after(para)
    body.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    bf = body.paragraph_format
    bf.first_line_indent = Cm(0.74)
    bf.line_spacing = 1.5
    bf.space_before = Pt(6)
    bf.space_after = Pt(12)
    _add_factory_run(body, "我单位 ", underline=False)
    _add_factory_run(body, brief.bidderName, underline=True)
    _add_factory_run(body, " 作为", underline=False)
    _add_factory_run(body, infer_factory_role(brief), underline=True)
    _add_factory_run(
        body,
        "，承诺具备加工生产条件，"
        "在人员、设备、资金等方面具备相应的供货能力，对",
        underline=False,
    )
    if project:
        _add_factory_run(body, f"「{project}」", underline=True)
    else:
        _add_factory_run(body, "本次", underline=False)
    _add_factory_run(body, "投标产品的质量、供货期、质保期及售后服务承担原厂责任。本项目供货期 ", underline=False)
    _add_factory_run(body, str(brief.deliveryDays) if brief.deliveryDays else "", underline=True)
    _add_factory_run(body, " 日历天内，质保期 ", underline=False)
    _add_factory_run(body, str(brief.warrantyYears) if brief.warrantyYears else "", underline=True)
    _add_factory_run(body, " 年。特此承诺。", underline=False)
    return body


def _append_right_sign_block(after: Paragraph, brief: BidBrief, *, unit: str) -> None:
    # 标签是“法定代表人或其委托代理人”，用户只填法人时优先使用法人。
    signer = (brief.legalPersonName or "").strip() or (brief.agentName or "").strip()
    year, month, day = _ymd(brief.bidDate)
    spacer = _insert_bottom_sign_spacer_after(after, sign_cm=4.2)
    p1 = _insert_paragraph_after(spacer or after)
    _rewrite_labeled_underline(
        p1,
        [(unit, (brief.bidderName or "").strip(), "")],
        align=WD_ALIGN_PARAGRAPH.RIGHT,
        space_before=4,
        space_after=6,
    )
    p2 = _insert_paragraph_after(p1)
    _rewrite_labeled_underline(
        p2,
        [("法定代表人或其委托代理人（签字或盖章）：", signer, "")],
        align=WD_ALIGN_PARAGRAPH.RIGHT,
        space_before=8,
        space_after=6,
    )
    p3 = _insert_paragraph_after(p2)
    _rewrite_labeled_underline(
        p3,
        [("日期：", f"{year}年{month}月{day}日", "")],
        align=WD_ALIGN_PARAGRAPH.RIGHT,
        space_before=8,
        space_after=4,
    )
    _glue_sign_off(after.part.document, spacer, [p1, p2, p3])


def _auth_text(brief: BidBrief) -> str:
    legal = (brief.legalPersonName or "").strip() or "　　　"
    agent = (brief.agentName or "").strip()
    project = (brief.projectName or "").strip() or "　　　"
    if not agent:
        # 模板路径仍保留授权委托书原文的角色槽位；代理人槽位为空，
        # 不用“（不委托）”等机器说明文字代替用户未填内容。
        return (
            f"本人 {legal} （姓名）系 {brief.bidderName} （投标人名称）的法定代表人，"
            f"现委托 　　　（姓名）为我方代理人。代理人根据授权，以我方名义签署、澄清、说明、"
            f"补正、递交、撤回、修改 {project} 投标文件、签订合同和处理有关事宜，"
            "其法律后果由我方承担。"
        )
    until = (brief.agentAuthUntil or "").strip() or "至本项目投标有效期届满"
    return (
        f"本人 {legal} （姓名）系 {brief.bidderName} （投标人名称）的法定代表人，"
        f"现委托 {agent} （姓名）为我方代理人。代理人根据授权，以我方名义签署、澄清、说明、"
        f"补正、递交、撤回、修改 {brief.projectName} 投标文件、签订合同和处理有关事宜，"
        f"其法律后果由我方承担。委托期限：{until}。代理人无转委托权。"
    )


def _write_auth_paragraph(para: Paragraph, brief: BidBrief) -> None:
    """用唯一的整段授权书路径写入角色槽位，避免按空位顺序错配。"""
    # 调用统一文本生成器作为内容判据；下面仅把其四个结构化槽位恢复为版式下划线。
    _auth_text(brief)
    _clear_runs(para)
    para.paragraph_format.line_spacing = 1.5
    para.paragraph_format.space_before = Pt(6)
    para.paragraph_format.space_after = Pt(6)
    _use_body_style(para)
    legal = (brief.legalPersonName or "").strip()
    agent = (brief.agentName or "").strip()
    bidder = (brief.bidderName or "").strip()
    project = (brief.projectName or "").strip()
    _form_run(para, "本人 ", underline=False)
    _form_run(para, legal or "　　　", underline=True)
    _form_run(para, " （姓名）系 ", underline=False)
    _form_run(para, bidder, underline=True)
    _form_run(para, " （投标人名称）的法定代表人，现委托 ", underline=False)
    _form_run(para, agent or "　　　", underline=True)
    _form_run(para, " （姓名）为我方代理人。代理人根据授权，以我方名义签署、澄清、说明、"
                   "补正、递交、撤回、修改 ", underline=False)
    _form_run(para, project or "　　　", underline=True)
    _form_run(para, " 投标文件、签订合同和处理有关事宜，其法律后果由我方承担。", underline=False)
    _set_word_wrap(para, enabled=False)
    _scrub_hints(para, ("（姓名）", "（投标人名称）"))


def _auth_role_names(text: str) -> tuple[str, str]:
    """从授权书正文提取法人和代理人槽位，不依赖任何具体人名。"""
    raw = re.sub(r"[\r\n\t]", "", text or "")
    legal = ""
    agent = ""
    if "法定代表人" in raw:
        head = raw.split("法定代表人", 1)[0]
        if "本人" in head and "系" in head:
            legal = head.split("本人", 1)[1].rsplit("系", 1)[0]
    if "现委托" in raw and "为我方代理人" in raw:
        agent = raw.split("现委托", 1)[1].split("为我方代理人", 1)[0]
    for key in ("（姓名）", "(姓名)", "姓名", "：", ":"):
        legal = legal.replace(key, "")
        agent = agent.replace(key, "")
    legal = re.sub(r"[_＿—\-－]+", "", legal)
    agent = re.sub(r"[_＿—\-－]+", "", agent)
    return legal.replace(" ", "").replace("　", "").strip(), agent.replace(" ", "").replace("　", "").strip()


def normalize_auth_role_paragraphs(doc: Document, brief: BidBrief) -> int:
    """统一授权委托书正文的法人/代理人槽位，按语义一次性写入。"""
    changed = 0
    from api.services.tenders.directory_guard import cover_nodes
    cover = cover_nodes(doc)
    for para in doc.paragraphs:
        if para._p in cover:
            continue
        raw = para.text or ""
        if not (raw.strip().startswith("本人") and "法定代表人" in raw and "现委托" in raw):
            continue
        _write_auth_paragraph(para, brief)
        _validate_auth_paragraph(para, brief)
        changed += 1
    return changed


def _validate_auth_paragraph(para: Paragraph, brief: BidBrief) -> None:
    legal_in_text, agent_in_text = _auth_role_names(para.text)
    expected_legal = (brief.legalPersonName or "").strip()
    expected_agent = (brief.agentName or "").strip()
    if legal_in_text and legal_in_text != expected_legal:
        logger.error("授权委托书法人角色错位：text=%r brief=%r", legal_in_text, expected_legal)
    if agent_in_text and agent_in_text != expected_agent:
        logger.error("授权委托书代理人角色错位：text=%r brief=%r", agent_in_text, expected_agent)
    if not expected_agent and agent_in_text:
        logger.error("授权委托书代理人字段为空但正文出现人名，自动清空代理人槽位")
        _write_auth_paragraph(para, brief.model_copy(update={"agentName": "", "agentAuthUntil": ""}))


def _fill_paragraphs(
    doc: Document,
    brief: BidBrief,
    media: dict | None = None,
    inlined: set[str] | None = None,
) -> Paragraph | None:
    from api.services.tenders.pdf_format import _self_sign_note, _compact_signature_flow
    doc.element._fill_styles = doc.styles.element
    note = _self_sign_note(brief)
    if note:logger.info(note)
    price_cn = rmb_uppercase(brief.bidPriceYuan)
    price_en = rmb_figure(brief.bidPriceYuan)
    extra = (brief.extraNote or "").strip()
    year, month, day = _ymd(brief.bidDate)
    section = "cover"
    drop: list[Paragraph] = []
    quote_anchor: Paragraph | None = None
    letter_date_done = False
    letter_sign_gapped = False
    legal_sign_gapped = False
    auth_legal_id_done = False
    auth_agent_id_done = False
    auth_agent_line_done = False
    auth_body_para: Paragraph | None = None
    auth_sign_started = False
    hints_project = ("（项目名称）", "（项目名称")
    hints_bidder = ("（投标人名称）",)

    for para in doc.paragraphs:
        raw = para.text
        n = _compact(raw)
        style = para.style.name if para.style else ""
        heading = style.startswith("Heading")

        if n.startswith("第五章"):
            drop.append(para)
            continue
        if n.startswith("目录") or n == "目录":
            section = "toc"
            continue
        if section == "toc" and not heading:
            if "备注" in n or "应编制" in n or "页码及内容" in n:
                drop.append(para)
            continue
        if heading:
            for title, bookmark in _HEADING_BOOKMARKS:
                if n == _compact(title):
                    _bookmark_paragraph(para, bookmark)
                    break
        if n == "投标函及投标函附录" and heading:
            section = "letter"
            continue
        if n == "投标函附录":
            section = "appendix"
            _bookmark_paragraph(para, "toc_letter_app")
            continue
        if n == "投标函" and section == "letter":
            _bookmark_paragraph(para, "toc_letter")
        if n == "支付条件" and heading:
            section = "pay"
            continue
        if n == "分项报价表" and heading:
            section = "quote"
            continue
        if n == "法定代表人身份证明" and heading:
            section = "legal"
            continue
        if n == "授权委托书" and heading:
            section = "auth"
            continue
        if n == "技术偏差表" and heading:
            section = "dev"
            continue
        if "近年完成的类似项目" in n:
            section = "perf"
            _bookmark_paragraph(para, "toc_perf")
            continue
        if "正在供货和新承接" in n:
            section = "perf"
            continue
        if n == "原厂生产承诺" and heading:
            section = "factory"
            continue
        if n == "其他材料" and heading:
            section = "other"
            continue

        if section == "cover":
            if "项目名称" in n:
                _fill_blanks(para, brief.projectName)
                _scrub_hints(para, hints_project)
            elif n.startswith("投标人") and "盖单位公章" in n:
                mashed_legal = "法定代表人" in n
                _remove_line_drawings(para)
                _rewrite_labeled_underline(
                    para,
                    [("投标人：", brief.bidderName, "（盖单位公章）")],
                    **_COVER_SIGN_KW,
                )
                from api.services.tenders.style_roles import (
                    build_style_profile,
                    classify_paragraph,
                    most_common_body_style,
                    normalize_body_paragraph,
                )

                profile = build_style_profile(doc)
                role = classify_paragraph(para, profile)
                if role.role != "body":
                    logger.warning("落款段按 style_roles 判定为 %s，仍清理继承样式：%s", role.role, para.text[:40])
                if not normalize_body_paragraph(para, profile, most_common_body_style(doc)):
                    logger.warning("正文样式未取得正文大小，保留原字号：%s", para.text[:40])
                if mashed_legal and "法定代表人" not in _compact(para.text):
                    legal_p = _insert_paragraph_after(para)
                    _rewrite_labeled_underline(
                        legal_p,
                        [("法定代表人或其委托代理人：", "", "（签字）")],
                        **_COVER_SIGN_KW,
                    )
            elif "法定代表人或其委托代理人" in n and "盖单位公章" not in n:
                _rewrite_labeled_underline(
                    para,
                    [("法定代表人或其委托代理人：", "", "（签字）")],
                    **_COVER_SIGN_KW,
                )
            elif n.startswith("日期") or n == "年月日":
                _rewrite_date_line(para, year, month, day, **_COVER_SIGN_KW)
            continue

        if section == "letter":
            if "招标人名称" in n:
                _write_tenderer_line(para, brief.tenderer)
            elif "投标保证金" in n:
                if brief.bidBondYuan is None:
                    logger.warning("投标函未解析到投标保证金金额，保留空白")
                else:
                    _fill_blanks(
                        para,
                        rmb_uppercase(brief.bidBondYuan),
                        f"¥{rmb_figure(brief.bidBondYuan)}",
                    )
            elif "愿意以人民币投标总报价" in n or "我方已认真研究了" in n or "我方已仔细研究了" in n:
                _fill_blanks(
                    para,
                    brief.projectName,
                    price_cn,
                    price_en,
                    f"{brief.deliveryDays}日历天" if brief.deliveryDays else "",
                    brief.quality,
                )
                _scrub_hints(para, hints_project)
            elif "其他补充说明" in n:
                if extra:
                    appended = _insert_paragraph_after(para)
                    _clear_runs(appended)
                    run = appended.add_run(f"9．（其他补充说明）：{extra}。")
                    _style_fill_run(run, appended, underline=False)
            elif n.startswith("投标人") and "盖单位公章" in n:
                if not letter_sign_gapped:
                    _insert_bottom_sign_spacer_before(para, sign_cm=3.2)
                    letter_sign_gapped = True
                _rewrite_labeled_underline(
                    para,
                    [("投标人：", brief.bidderName, "（盖单位公章）")],
                    **{**_LETTER_SIGN_RIGHT_KW, "space_before": 16},
                )
                para.paragraph_format.keep_with_next = True
            elif "法定代表人或其委托代理人" in n:
                _rewrite_labeled_underline(
                    para,
                    [
                        (
                            "法定代表人或其委托代理人：",
                            (brief.agentName or "").strip(),
                            "（签字或盖章）",
                        )
                    ],
                    **_LETTER_SIGN_RIGHT_KW,
                )
                para.paragraph_format.keep_with_next = True
            elif n in {"签字或盖章", "签字", "盖章"} or n.startswith("签字或盖章"):
                drop.append(para)
            elif n.startswith(
                (
                    "地址",
                    "网址",
                    "电话",
                    "传真",
                    "邮政编码",
                    "邮编",
                    "邮箱",
                    "电子信箱",
                    "电子邮箱",
                    "项目联系人",
                )
            ):
                drop.append(para)
            elif n.startswith("日期") or n == "年月日":
                if letter_date_done:
                    drop.append(para)
                else:
                    _rewrite_date_line(
                        para,
                        year,
                        month,
                        day,
                        **{**_LETTER_SIGN_RIGHT_KW, "label": "日期："},
                    )
                    letter_date_done = True
            continue

        if section in {"appendix", "pay"}:
            if "盖单位公章" in n or "盖单位章" in n or (n.startswith("投标人") and "签字" in n):
                mashed_legal = "法定代表人" in n
                seal = "（盖单位公章）" if "公章" in n else "（盖单位章）"
                label = "投标人名称：" if "名称" in n else "投标人："
                _rewrite_labeled_underline(
                    para,
                    [(label, brief.bidderName, seal)],
                    **_LETTER_SIGN_RIGHT_KW,
                )
                if mashed_legal and "法定代表人" not in _compact(para.text):
                    legal_p = _insert_paragraph_after(para)
                    sign_hint = "（签字）" if "签字" in n else "（签字或盖章）"
                    _rewrite_labeled_underline(
                        legal_p,
                        [("法定代表人或委托代理人：", "", sign_hint)],
                        **_LETTER_SIGN_RIGHT_KW,
                    )
            elif n == "年月日" or n.startswith("日期"):
                _rewrite_date_line(para, year, month, day, **_LETTER_SIGN_RIGHT_KW)
            continue

        if section == "quote":
            if "附表" in n:
                quote_anchor = para
                align = para.alignment
                _clear_runs(para)
                run = para.add_run(
                    "备注：详见附表。工程量与招标清单一致，综合单价按投标总价折算。"
                )
                _font(run, 10.5)
                run.underline = False
                if align is not None:
                    para.alignment = align
            continue

        if section == "legal":
            if n.startswith("投标人名称"):
                _fill_blanks(para, brief.bidderName)
            elif n.startswith("单位性质"):
                _fill_blanks(para, brief.bidderNature)
            elif n.startswith("地址"):
                _fill_blanks(para, (brief.bidderAddress or "").strip())
                pf = para.paragraph_format
                pf.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
                pf.line_spacing = 2.0
                pf.space_before = Pt(12)
                pf.space_after = Pt(12)
            elif "成立时间" in n:
                fy, fm, fd = _ymd(brief.foundedDate) if (brief.foundedDate or "").strip() else ("", "", "")
                _fill_blanks(para, fy, fm, fd, (brief.businessTerm or "").strip())
            elif n.startswith("姓名"):
                _fill_blanks(
                    para,
                    (brief.legalPersonName or "").strip(),
                    (brief.legalPersonGender or "").strip(),
                    (brief.legalPersonAge or "").strip(),
                    (brief.legalPersonTitle or "").strip(),
                )
            elif "的法定代表人" in n:
                _fill_blanks(para, brief.bidderName)
                _scrub_hints(para, hints_bidder)
            elif n.startswith("投标人：") and "盖单位公章" in n:
                if not legal_sign_gapped:
                    inline_id_scans(
                        doc,
                        (media or {}).get("id_legal") if media else None,
                        empty_slot=id_slot("id_legal"),
                        before=para,
                    )
                    if inlined is not None:
                        inlined.add("id_legal")
                    _insert_bottom_sign_spacer_before(para, sign_cm=4.4)
                    legal_sign_gapped = True
                para.paragraph_format.keep_with_next = True
                para.paragraph_format.keep_together = True
                _rewrite_labeled_underline(
                    para,
                    [("投标人：", brief.bidderName, "（盖单位公章）")],
                    **_SIGN_OFF_KW,
                )
            elif n == "年月日":
                _rewrite_date_line(para, year, month, day, **_SIGN_OFF_KW)
            continue

        if section == "auth":
            if n.startswith("本人") and "法定代表人" in n:
                # 授权委托书正文统一走整段生成；同一段不再叠加旧的按位置填空路径。
                _write_auth_paragraph(para, brief)
                auth_body_para = para
            elif n.startswith("委托期限"):
                until = (brief.agentAuthUntil or "").strip() or "至本项目投标有效期届满"
                if not (brief.agentName or "").strip():
                    # 未委托时保留模板空线，不能写默认期限或任何解释性占位词。
                    until = ""
                    _clear_runs(para)
                _rewrite_labeled_underline(
                    para,
                    [("委托期限：", until, "。代理人无转委托权。" if until else "")],
                    align=WD_ALIGN_PARAGRAPH.LEFT,
                    line_spacing=1.5,
                    space_before=6,
                    space_after=6,
                )
            elif n.startswith("投标人") and (
                "法定代表人" in n or "盖单位公章" in n or "盖单位章" in n
            ):
                if not auth_sign_started:
                    if (brief.agentName or "").strip():
                        inline_id_scans(
                            doc,
                            (media or {}).get("id_agent") if media else None,
                            empty_slot=id_slot("id_agent"),
                            before=para,
                        )
                        if inlined is not None:
                            inlined.add("id_agent")
                    _insert_bottom_sign_spacer_before(para, sign_cm=6.6)
                auth_sign_started = True
                para.paragraph_format.keep_with_next = True
                para.paragraph_format.keep_together = True
                mashed_legal = "法定代表人" in n
                _rewrite_labeled_underline(
                    para,
                    [("投标人：", brief.bidderName, "（盖单位公章）")],
                    **_SIGN_OFF_KW,
                )
                if mashed_legal and "法定代表人" not in _compact(para.text):
                    legal_p = _insert_paragraph_after(para)
                    _rewrite_labeled_underline(
                        legal_p,
                        [
                            (
                                "法定代表人：",
                                "",
                                "（签字）",
                            )
                        ],
                        **_SIGN_OFF_KW,
                    )
            elif n.startswith("身份证号码") and "委托代理人" not in n:
                # 委托期限后、落款前的身份证行与签字区重复，丢掉。
                if not auth_sign_started:
                    drop.append(para)
                elif not auth_legal_id_done:
                    _rewrite_labeled_underline(
                        para,
                        # 本轮明确要求授权委托书中的法人证号留空，不从资料库或其它字段推导。
                        [("身份证号码：", "", "")],
                        **{**_SIGN_OFF_KW, "line_em": 18},
                    )
                    auth_legal_id_done = True
                elif auth_agent_id_done:
                    drop.append(para)
                else:
                    _rewrite_labeled_underline(
                        para,
                        # 代理人证号与代理人姓名同源；姓名为空时始终保持空线。
                        [("身份证号码：", "", "")],
                        **{**_SIGN_OFF_KW, "line_em": 18},
                    )
                    auth_agent_id_done = True
            elif "委托代理人" in n and "法定代表人" not in n:
                mashed_id = "身份证" in n
                if not auth_sign_started or auth_agent_line_done:
                    drop.append(para)
                else:
                    _rewrite_labeled_underline(
                        para,
                        [("委托代理人：", "", "（签字）")],
                        **{**_SIGN_OFF_KW, "line_em": 18},
                    )
                    auth_agent_line_done = True
                    if mashed_id:
                        if "身份证" in _compact(para.text):
                            _fill_blanks(para, "", "")
                        else:
                            id_p = _insert_paragraph_after(para)
                            _rewrite_labeled_underline(
                                id_p,
                                [("身份证号码：", "", "")],
                                **{**_SIGN_OFF_KW, "line_em": 18},
                            )
                        auth_agent_id_done = True
            elif n == "年月日":
                _rewrite_date_line(para, year, month, day, **_SIGN_OFF_KW)
            continue

        if section == "factory":
            if _is_factory_body(n):
                last = _write_factory_commitment(para, brief)
                _append_right_sign_block(last, brief, unit="承诺单位（盖章）：")
            continue

        if section == "other":
            # 去掉模板里「本部分附企业介绍…」说明段，待补资料改用后文小标题+方框
            if n:
                drop.append(para)
            continue

    for para in drop:
        _drop_para(para)
    if auth_body_para is not None:
        _validate_auth_paragraph(auth_body_para, brief)
    _compact_signature_flow(doc)
    return quote_anchor


def _fill_tables(doc: Document, brief: BidBrief, facts=None) -> list[str]:
    notes: list[str] = []
    price_cn = rmb_uppercase(brief.bidPriceYuan)
    price_en = rmb_figure(brief.bidPriceYuan)
    perf_iter = iter(_ordered_perf_lines(brief))
    for table in doc.tables:
        kind = _table_kind(table)
        if kind == "appendix":
            notes.extend(_fill_appendix_table(table, brief, price_cn, price_en, facts))
        elif kind == "pay":
            _fill_payment_table(table, brief)
        elif kind == "dev":
            _fill_deviation_table(table, brief)
        elif kind == "perf":
            _fill_perf_table(table, next(perf_iter, None))
    return notes


def _table_kind(table: Table) -> str:
    if not table.rows or not table.rows[0].cells:
        return ""
    joined = "".join(_compact(c.text) for c in table.rows[0].cells)
    if "招标文件要求" in joined:
        return "dev"
    if "支付阶段" in joined or "支付比例" in joined:
        return "pay"
    if "不含税综合单价" in joined or "技术参数要求" in joined:
        return "quote"
    first = _compact(table.rows[0].cells[0].text)
    if first.startswith("项目名称"):
        if len(table.rows[0].cells) >= 3:
            return "appendix"
        return "perf"
    return ""


def _usable_width_twips(doc: Document) -> int:
    """版心宽度（页宽减左右边距），再留一点余量避免表格画出页边。"""
    for section in doc.sections:
        usable = (
            int(section.page_width.twips)
            - int(section.left_margin.twips)
            - int(section.right_margin.twips)
        )
        if usable > 5000:
            return max(usable - 120, 5000)
    return int(Cm(16.0).twips)


def _distribute_twips(total: int, ratios: tuple[int, ...]) -> list[int]:
    weight = sum(ratios)
    parts = [total * r // weight for r in ratios]
    parts[-1] += total - sum(parts)
    return parts


def _apply_fixed_table_widths(table: Table, widths_twips: list[int]) -> None:
    """固定列宽并限制表宽，避免 OnlyOffice / Word 按内容撑出页边。"""
    total = sum(widths_twips)
    table.autofit = False
    tbl = table._tbl
    tbl_pr = tbl.tblPr
    if tbl_pr is None:
        tbl_pr = OxmlElement("w:tblPr")
        tbl.insert(0, tbl_pr)
    for old in tbl_pr.findall(qn("w:tblW")):
        tbl_pr.remove(old)
    tbl_w = OxmlElement("w:tblW")
    tbl_w.set(qn("w:w"), str(total))
    tbl_w.set(qn("w:type"), "dxa")
    tbl_pr.append(tbl_w)
    for old in tbl_pr.findall(qn("w:tblLayout")):
        tbl_pr.remove(old)
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tbl_pr.append(layout)

    grid = tbl.find(qn("w:tblGrid"))
    if grid is None:
        grid = OxmlElement("w:tblGrid")
        tbl_pr.addnext(grid)
    for child in list(grid):
        grid.remove(child)
    for width in widths_twips:
        col = OxmlElement("w:gridCol")
        col.set(qn("w:w"), str(width))
        grid.append(col)

    for row in table.rows:
        for cell, width in zip(row.cells, widths_twips, strict=False):
            cell.width = Twips(width)


def _pin_cjk_fonts(doc: Document) -> None:
    """去掉主题西文字体映射，正文固定 SimSun/宋体，避免 OnlyOffice 用 Liberation 撑高分页。"""
    styles = doc.styles.element
    defaults = styles.find(qn("w:docDefaults"))
    if defaults is None:
        return
    rpr_default = defaults.find(qn("w:rPrDefault"))
    if rpr_default is None:
        return
    rpr = rpr_default.find(qn("w:rPr"))
    if rpr is None:
        rpr = OxmlElement("w:rPr")
        rpr_default.append(rpr)
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for attr in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
        key = qn(f"w:{attr}")
        if key in rfonts.attrib:
            del rfonts.attrib[key]
    rfonts.set(qn("w:ascii"), "SimSun")
    rfonts.set(qn("w:hAnsi"), "SimSun")
    rfonts.set(qn("w:eastAsia"), "SimSun")
    rfonts.set(qn("w:cs"), "SimSun")
    lang = rpr.find(qn("w:lang"))
    if lang is None:
        lang = OxmlElement("w:lang")
        rpr.append(lang)
    lang.set(qn("w:eastAsia"), "zh-CN")


def _hide_proofing_marks(doc: Document) -> None:
    """关闭拼写/语法红波浪线，避免中文技术用语被标红。

    注意：不要开启 w:updateFields。分页改造后目录使用 PAGEREF，
    若强制打开时更新全部域，OnlyOffice/WPS 预览会卡住数秒到数十秒。
    目录页码已写入域缓存；需要精确页码时可在 Word 里手动更新域。
    """
    root = doc.settings.element
    for tag in ("hideSpellingErrors", "hideGrammaticalErrors"):
        el_tag = qn(f"w:{tag}")
        for old in list(root.findall(el_tag)):
            root.remove(old)
        el = OxmlElement(f"w:{tag}")
        el.set(qn("w:val"), "true")
        root.append(el)
    for old in list(root.findall(qn("w:updateFields"))):
        root.remove(old)


_SIGNING_LABELS = {
    '投标人', '投标人名称', '供应商', '供应商名称', '联合体牵头人', '联合体成员',
    '法定代表人', '法定代表人或其委托代理人', '法定代表人或委托代理人',
    '委托代理人', '授权委托人', '承诺人', '承诺单位', '项目经理', '授权代表',
    '投标人或联合体牵头人',
    '牵头人名称', '联合体牵头人名称', '联合体成员名称', '成员名称', '联合体名称',
}
_INFORMATION_FIELDS = {
    'bidderNature', 'bidderAddress', 'companyEstablished', 'companyBusinessTerm',
    'companyRegisteredCapital', 'bidderCreditCode', 'legalPersonName',
    'legalPersonGender', 'legalPersonAge', 'legalPersonTitle', 'legalPersonIdNo',
    'bidderPhone', 'bidderFax', 'bidderPostcode', 'bidderWebsite', 'bidderEmail',
}
_CONTACT_FIELDS = {'bidderAddress', 'bidderPhone', 'bidderFax', 'bidderPostcode', 'bidderWebsite', 'bidderEmail'}
_INFORMATION_LABELS = {'联系人', '身份证号码', '身份证号'}
_NOTE_LEAD = re.compile(r'^\s*(?:附注|说明|备注|注|(?:本表后)?附)\s*[:：]')


def _paragraph_role(paragraph):
    """Semantic roles precede inherited alignment or a nearby signing keyword."""
    from api.services.tenders.pdf_format import _inline_label_field
    text = ''.join(value for _, value in _inline_nodes(paragraph)).strip()
    if _NOTE_LEAD.match(text):
        return 'note', None
    match = _SIGNOFF_FIELD.fullmatch(text)
    if match is None:
        bare = re.fullmatch(r'(?P<label>[^:：()（）\n]+)\s*(?P<value>[（(][^（）()]+[）)])\s*', text)
        if (bare and re.sub(r'\s+', '', bare['label']) in _SIGNING_LABELS
                and _SIGNOFF_SEAL.search(bare['value'])):
            match = _SIGNOFF_FIELD.fullmatch(bare['label'].strip() + '：' + bare['value'])
    prefix = re.match(r'^\s*(?P<label>[^:：\n]+)[:：]', text)
    label = re.sub(r'\s+', '', match['label'] if match else prefix['label']) if match or prefix else ''
    role_label = re.sub(r'[（(][^（）()]*[）)]', '', label)
    role_label = re.sub(r'(?<=成员)[零〇一二三四五六七八九十百\d]+(?=名称)', '', role_label)
    value = match['value'].strip() if match else text
    field = _inline_label_field(label)
    if _SIGNOFF_DATE.fullmatch(value) and (not match or field == 'bidDate'):
        return 'date', match
    if match is not None and any(role_label.endswith(label) for label in _SIGNING_LABELS):
        return 'signing', match
    if field in _INFORMATION_FIELDS or label in _INFORMATION_LABELS:
        return 'information', match
    return 'body', match


def _left_role(paragraph):
    """Make an information or note row left aligned without rebuilding its layout."""
    original = paragraph._p.xml
    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    text = ''.join(value for _, value in _inline_nodes(paragraph))
    leading = re.match(r'^[ \t\u3000]+', text)
    if leading:
        _replace_inline_span(paragraph, 0, leading.end(), '')
    return int(paragraph._p.xml != original)


def _is_signoff_line(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    match = re.match(r'([^:：]+)[:：]', compact)
    return bool((match and match[1] in _SIGNING_LABELS) or _SIGNOFF_DATE.fullmatch(compact))


def normalize_signoff_block(paragraphs) -> None:
    """Compatibility hook: paragraph layout is deferred to the final document pass."""
    return None


_SIGNOFF_DATE_TEXT = r"\d{4}[ \t\u3000]*年[ \t\u3000]*\d{1,2}[ \t\u3000]*月[ \t\u3000]*\d{1,2}[ \t\u3000]*日"
_SIGNOFF_DATE = re.compile(rf"[ \t\u3000]*{_SIGNOFF_DATE_TEXT}[ \t\u3000]*")
_SIGNOFF_FIELD = re.compile(
    # A date followed by a copied note and prose with sentence punctuation
    # are not individual label/value rows.
    rf"(?![ \t\u3000]*{_SIGNOFF_DATE_TEXT})"
    r"(?P<label>[^：:。！？；;，,\n]+?)[ \t\u3000]*(?P<colon>[：:])(?P<value>[^。！？；\n]*)"
)
_SIGNOFF_SEAL = re.compile(r"签字|签名|签章|盖单位公章|盖单位章|盖章|印章|公章")
_BUSINESS_LITERAL = re.compile(r"[（(][ \u3000]*(姓名|签字|盖章)[ \u3000]*[）)]|[（(]?在此粘贴扫描件[）)]?")
_INLINE_TEXT = {qn("w:tab"): "\t", qn("w:br"): "\n", qn("w:cr"): "\n"}


def _inline_nodes(paragraph: Paragraph) -> list[tuple]:
    nodes = paragraph._p.xpath(
        "./w:r/* | ./w:hyperlink/w:r/* | ./w:sdt/w:sdtContent/w:r/* | ./w:ins/w:r/* | ./w:smartTag/w:r/*"
    )
    return [
        (node, (node.text or "") if node.tag == qn("w:t") else _INLINE_TEXT[node.tag])
        for node in nodes if node.tag == qn("w:t") or node.tag in _INLINE_TEXT
    ]


def _replace_inline_span(paragraph: Paragraph, start: int, end: int, replacement: str) -> None:
    """Change only the affected text or tabs, leaving the surrounding runs intact."""
    offset = 0
    for node, original in _inline_nodes(paragraph):
        limit = offset + len(original)
        if start < limit and end > offset:
            value = original[:max(start - offset, 0)]
            if offset <= start:
                value += replacement
            value += original[max(end - offset, 0):]
            for index, part in enumerate(value.split("\t")):
                if index:
                    node.addprevious(OxmlElement("w:tab"))
                if part:
                    text_node = OxmlElement("w:t")
                    text_node.text = part
                    if part != part.strip():
                        text_node.set(qn("xml:space"), "preserve")
                    node.addprevious(text_node)
            node.getparent().remove(node)
        if limit >= end:
            break
        offset = limit


def _split_role_paragraphs(doc: Document) -> int:
    """Split mixed roles before filling, preserving the original final section edge."""
    from api.services.tenders.directory_guard import directory_blocks, cover_nodes
    from api.services.tenders.pdf_format import _STAMP
    excluded = directory_blocks(doc) | cover_nodes(doc)
    changed = 0
    signing_labels = '|'.join(map(re.escape, sorted(_SIGNING_LABELS, key=len, reverse=True)))
    row_pattern = re.compile(r'(?:' + signing_labels + r')\s*(?:[（(][^（）()]*[）)])?\s*[:：]')
    for node in list(doc.element.body.iter(qn('w:p'))):
        if (node.getparent() is None or node in excluded or next(node.iter(qn('w:drawing')), None) is not None
                or node.xpath('./w:pPr/w:pBdr') or any(a.tag in {qn('w:tc'), qn('w:txbxContent')} for a in node.iterancestors())):
            continue
        p = Paragraph(node, doc._body)
        text = ''.join(value for _, value in _inline_nodes(p))
        if re.fullmatch(_STAMP, text.strip()):
            previous = node.getprevious()
            while (previous is not None and previous.tag == qn('w:p')
                   and not ''.join(t.text or '' for t in previous.iter(qn('w:t'))).strip()):
                if previous.xpath('.//w:sectPr | .//w:drawing | .//w:br[@w:type="page"]'):
                    break
                previous = previous.getprevious()
            if previous is not None and previous.tag == qn('w:p') and previous not in excluded:
                prior = Paragraph(previous, doc._body)
                if (_paragraph_role(prior)[0] == 'signing'
                        and not previous.xpath('.//w:sectPr | .//w:drawing | .//w:br[@w:type="page"]')
                        and not node.xpath('.//w:sectPr | .//w:br[@w:type="page"]')):
                    for child in list(node):
                        if child.tag != qn('w:pPr'):
                            previous.append(child)
                    node.getparent().remove(node)
                    changed += 1
                    continue
        rows = list(row_pattern.finditer(text))
        dated = re.match(rf'^(?P<date>\s*(?:[^:：\n]+[:：]\s*)?{_SIGNOFF_DATE_TEXT})\s*(?P<note>(?:附注|说明|备注|注|附)[:：].*)$', text, re.S)
        slices = []
        if dated:
            slices = [dated['date'].rstrip(), dated['note']]
        elif rows and _SIGNOFF_SEAL.search(text):
            prefix = text[:rows[0].start()]
            note_tail = (_NOTE_LEAD.match(prefix) and text.rstrip().endswith(('）', ')'))
                         and prefix.count('（') == prefix.count('）') and prefix.count('(') == prefix.count(')'))
            if prefix.strip() and not note_tail:
                continue
            if len(rows) > 1 or note_tail:
                slices = ([prefix.rstrip()] if note_tail else []) + [
                    text[row.start():rows[i + 1].start() if i + 1 < len(rows) else len(text)].strip()
                    for i, row in enumerate(rows)]
        if not slices:
            continue
        section = node.find('w:pPr/w:sectPr', node.nsmap)
        if section is not None:
            section.getparent().remove(section)
        cursor = node
        for value in slices[1:]:
            clone = deepcopy(node)
            for mark in list(clone.iter(qn('w:bookmarkStart'), qn('w:bookmarkEnd'), qn('w:fldSimple'))):
                mark.getparent().remove(mark)
            para = Paragraph(clone, doc._body)
            _replace_inline_span(para, 0, len(text), value)
            cursor.addnext(clone)
            cursor = clone
        _replace_inline_span(p, 0, len(text), slices[0])
        if section is not None:
            cursor.get_or_add_pPr().append(section)
        changed += 1
    return changed


def _signoff_blocks(doc):
    """Return contiguous body rows that can form one signoff block.

    Tables, text boxes, bordered paste frames and protected cover/directory
    regions are deliberately boundaries. Their fields retain the source
    layout and never enter signoff handling.
    """
    from api.services.tenders.directory_guard import cover_nodes, directory_blocks
    from api.services.tenders.technical_toc import _regions
    from api.services.tenders.word_structure import outline_level
    excluded = cover_nodes(doc) | directory_blocks(doc) | {
        n for region in _regions(doc) for host in region for n in host.iter()}
    blocks, current = [], []
    def finish():
        nonlocal current
        if current:
            blocks.append(current)
        current = []
    for node in doc.element.body:
        if node in excluded or node.tag != qn('w:p'):
            finish()
            continue
        p = Paragraph(node, doc._body)
        text = ''.join(value for _, value in _inline_nodes(p))
        if node.xpath('./w:pPr/w:pageBreakBefore[not(@w:val) or @w:val="1" or @w:val="true"]'):
            finish()
        role, match = _paragraph_role(p)
        frame = bool(node.xpath('./w:pPr/w:pBdr | .//w:txbxContent'))
        if frame:
            finish()
            continue
        if not text.strip() and not node.xpath('.//w:drawing | .//w:pict'):
            pass
        elif role in {'signing', 'information', 'date', 'note'}:
            current.append((p, role, match))
        elif match and not outline_level(doc, node):
            current.append((p, 'uncertain', match))
        else:
            finish()
        if role == 'date' or node.xpath('.//w:sectPr | .//w:br[@w:type="page"]'):
            finish()
    finish()
    return blocks


def _node_has_nonzero_indent(paragraph: Paragraph) -> bool:
    ind = paragraph._p.find('w:pPr/w:ind', paragraph._p.nsmap)
    if ind is None:
        return False
    return any(value not in {None, '', '0', '0pt', '0char'}
               for key, value in ind.attrib.items()
               if key in {qn('w:left'), qn('w:right'), qn('w:firstLine'), qn('w:hanging'),
                          qn('w:leftChars'), qn('w:rightChars'), qn('w:firstLineChars'),
                          qn('w:hangingChars')})


def _copy_paragraph_indent(source: Paragraph, target: Paragraph) -> bool:
    source_ind = source._p.find('w:pPr/w:ind', source._p.nsmap)
    if source_ind is None or _node_has_nonzero_indent(target):
        return False
    target_ppr = target._p.get_or_add_pPr()
    target_ind = target_ppr.find(qn('w:ind'))
    if target_ind is not None:
        target_ppr.remove(target_ind)
    target_ppr.append(deepcopy(source_ind))
    return True


def _paragraph_tab_snapshot(paragraph: Paragraph) -> list[dict[str, str | None]]:
    return [
        {"pos": tab.get(qn('w:pos')), "val": tab.get(qn('w:val')), "leader": tab.get(qn('w:leader'))}
        for tab in paragraph._p.xpath('./w:pPr/w:tabs/w:tab')
    ]


def _clear_signoff_tabs(paragraph: Paragraph) -> bool:
    """Remove tabs introduced by post-processing from a signoff/date row."""
    before = paragraph._p.xml
    for tab in list(paragraph._p.xpath('./w:pPr/w:tabs/w:tab')):
        tab.getparent().remove(tab)
    tabs = paragraph._p.find('w:pPr/w:tabs', paragraph._p.nsmap)
    if tabs is not None and len(tabs) == 0:
        tabs.getparent().remove(tabs)
    text = ''.join(value for _, value in _inline_nodes(paragraph))
    compact = text.replace('\t', '')
    leading = re.match(r'^[ \t\u3000]+', compact)
    if leading:
        compact = compact[leading.end():]
    if compact != text:
        _replace_inline_span(paragraph, 0, len(text), compact)
    return before != paragraph._p.xml


def _normalize_signoff_text(paragraph: Paragraph, match) -> bool:
    """Keep labels attached to their colon and retain only the source value gap."""
    text = ''.join(value for _, value in _inline_nodes(paragraph))
    if match is None:
        return _clear_signoff_tabs(paragraph)
    label = re.sub(r'\s+', '', match['label'])
    value_raw = match['value']
    value = value_raw.strip()
    gap = ' ' if value_raw and value_raw[0].isspace() and value else ''
    expected = f"{label}{match['colon']}{gap}{value}"
    if text != expected:
        _replace_inline_span(paragraph, 0, len(text), expected)
    return _clear_signoff_tabs(paragraph) or text != expected


def _information_context(block, index: int) -> bool:
    """Use following information rows as evidence for a repeated label."""
    after = any(role == 'information' for _, role, _ in block[index + 1:])
    return after


def _align_business_signoffs(doc: Document, seal_paragraphs=None) -> int:
    """Separate information rows from signed tails while retaining source layout."""
    changed, reports = _split_role_paragraphs(doc), []
    for block in _signoff_blocks(doc):
        actions = [i for i, (p, role, _) in enumerate(block)
                   if role == 'signing' and _SIGNOFF_SEAL.search(p.text)]
        dates = [(p, match) for p, role, match in block if role == 'date']
        info_indexes = [i for i, (_, role, _) in enumerate(block) if role == 'information']
        first_action = min(actions) if actions else None
        last_info = max(info_indexes, default=-1)
        confirmed = bool(actions or dates)
        fields = []
        information_rows = []
        for index, (p, role, match) in enumerate(block):
            if role in {'information', 'note'}:
                protected = bool(p._p.xpath('ancestor::w:tc | ancestor::w:txbxContent'))
                if not protected:
                    changed += _left_role(p)
                if role == 'information':
                    information_rows.append(index)
            if role != 'signing' or not match:
                continue
            has_seal = bool(_SIGNOFF_SEAL.search(p.text))
            repeated_in_info = _information_context(block, index)
            before_signed_tail = first_action is not None and index < first_action
            if not has_seal and (repeated_in_info or before_signed_tail):
                changed += _left_role(p)
                information_rows.append(index)
            elif has_seal or (confirmed and index > last_info):
                fields.append((p, match))
        sign_rows = [p for p, _ in fields] + [p for p, _ in dates]
        source_indent = next((p for p in sign_rows if _node_has_nonzero_indent(p)), None)
        before = {
            p._p.getroottree().getpath(p._p): {
                'indent': dict(p._p.xpath('./w:pPr/w:ind')[0].attrib)
                if p._p.xpath('./w:pPr/w:ind') else {},
                'tabs': _paragraph_tab_snapshot(p),
            }
            for p in sign_rows
        }
        if source_indent is not None:
            for target in sign_rows:
                if target is not source_indent and _copy_paragraph_indent(source_indent, target):
                    changed += 1
        for p, match in fields:
            changed += int(_normalize_signoff_text(p, match))
        for p, match in dates:
            changed += int(_normalize_signoff_text(p, match))
        record = {
            'block': block[0][0]._p.getroottree().getpath(block[0][0]._p),
            'fields': [p._p.getroottree().getpath(p._p) for p, _ in fields],
            'dates': [p._p.getroottree().getpath(p._p) for p, _ in dates],
            'information': [block[i][0]._p.getroottree().getpath(block[i][0]._p) for i in information_rows],
            'pending': [p._p.getroottree().getpath(p._p) for i, (p, role, _) in enumerate(block)
                        if role == 'uncertain' or (role == 'signing' and i not in information_rows
                                                  and all(p is not candidate for candidate, _ in fields))],
            'before': before,
            'after': {
                p._p.getroottree().getpath(p._p): {
                    'indent': dict(p._p.xpath('./w:pPr/w:ind')[0].attrib)
                    if p._p.xpath('./w:pPr/w:ind') else {},
                    'tabs': _paragraph_tab_snapshot(p),
                }
                for p in sign_rows
            },
        }
        if not fields and not dates:
            record['status'] = 'information_only' if information_rows else 'needs_review'
            reports.append(record)
            continue
        record.update(status='aligned' if not record['pending'] else 'needs_review')
        reports.append(record)
    import json
    for record in reports:
        logger.info('SIGNOFF_BLOCK %s', json.dumps(record, ensure_ascii=True, sort_keys=True))
    doc.element._signoff_audit = reports
    return changed


def postprocess_business_document(doc: Document, brief: BidBrief, *, layout=True) -> int:
    """Align signature fields and reconcile source-backed wording in the finished bid."""
    from api.services.tenders.assets import load_invitation_text
    from api.services.tenders.technical_render import _invitation_validity_phrase
    from api.services.tenders.directory_guard import cover_nodes
    cover = cover_nodes(doc)

    changed = 0
    seal_paragraphs = set()
    agent = "" if brief.agentNameSource.strip().lower() == "history" else brief.agentName.strip()
    validity = None
    validity_statement = re.compile(r"投标有效期\s*为\s*\d+(?:\s*(?:日历天|工作日|天|日))?(?=\s*(?:[。，、；,;）)]|$))")
    for element in doc.element.body.iter(qn("w:p")):
        if element in cover:
            continue
        para = Paragraph(element, doc._body)
        original = "".join(value for _, value in _inline_nodes(para))
        if _SIGNOFF_SEAL.search(original):
            seal_paragraphs.add(element)
        for match in reversed(list(_BUSINESS_LITERAL.finditer(original))):
            if match[1] in {'签字', '盖章'}:
                continue
            replacement = ""
            if match[1] == "姓名":
                is_agent = re.search(r"现委托[ \t\u3000]*$", original[:match.start()])
                replacement = agent if agent and is_agent else ""
            _replace_inline_span(para, match.start(), match.end(), replacement)
            changed += 1
        visible = "".join(value for _, value in _inline_nodes(para))
        for match in reversed(list(validity_statement.finditer(visible))):
            if validity is None:
                validity = (f"投标有效期为{brief.bidValidityDays}日历天" if brief.bidValidityDays
                            else _invitation_validity_phrase(load_invitation_text(brief.invitationId)))
                if not re.search(r'\d+\s*(?:日历天|工作日|天|日)', validity):
                    validity = ''
            if not validity:
                continue
            if match[0] != validity:
                _replace_inline_span(para, match.start(), match.end(), validity)
                changed += 1
    changed += sum(clean_visible_prompts(doc))
    return changed + (_align_business_signoffs(doc, seal_paragraphs) if layout else 0)


def _normalize_structure_roles(doc, *, layout=True, outline=True):
    from api.services.tenders.technical_toc import _regions
    from api.services.tenders.word_structure import outline_level, numbering_labels
    from api.services.tenders.directory_guard import cover_nodes
    cover = cover_nodes(doc)
    regions = _regions(doc)
    excluded = {node for region in regions for host in region for node in host.iter()} | cover
    body = doc.element.body
    front_end = min((body.index(region[0]) for region in regions), default=-1)
    cached = {_pageref_bookmark(p): p.text.split('\t', 1)[0]
              for p in doc.paragraphs if _pageref_bookmark(p)}
    body_paragraphs = [p for p in doc.paragraphs if p._p not in excluded and body.index(p._p) > front_end]
    declared = sorted({outline_level(doc, p._p) for p in body_paragraphs
                       if outline_level(doc, p._p) and _paragraph_role(p)[0] == 'body'})
    canonical = {level: index + 1 for index, level in enumerate(declared)}
    labels = numbering_labels(doc, [p._p for p in body_paragraphs])
    current_root = 0
    changed = 0
    for node in list(body.iter(qn('w:p'))):
        if node in cover:
            continue
        p = Paragraph(node, doc._body)
        role, _ = _paragraph_role(p)
        protected_container = bool(node.xpath('ancestor::w:tc | ancestor::w:txbxContent | ./w:pPr/w:pBdr'))
        host = node
        while host.getparent() is not body:
            host = host.getparent()
        front = body.index(host) < front_end
        level = outline_level(doc, node)
        if outline and not protected_container and level and (front or role in {'note', 'information'}):
            name = 'Body (' + p.style.style_id + ')'
            try:
                style = doc.styles[name]
            except KeyError:
                style = doc.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
                style.base_style = p.style
                style.element.get_or_add_pPr().get_or_add_outlineLvl().val = 9
            p.style = style
            node.get_or_add_pPr().get_or_add_outlineLvl().val = 9
            changed += 1
        elif node not in excluded and not protected_container and not front and role == 'body':
            names = [mark.get(qn('w:name')) for mark in node.iter(qn('w:bookmarkStart'))]
            old_title = next((cached[name] for name in names if name in cached), '')
            shown = p.text.strip()
            if not _TEXT_HEADING_NUMBER.match(shown):
                prefix = labels.get(node, ('', 0))[0]
                shown = prefix + shown if prefix else old_title or shown
            if level or old_title:
                normalized_level = canonical.get(level, level or 1)
                if re.match(r'^\s*[（(][零〇一二三四五六七八九十百千万\d]+[）)]', shown):
                    normalized_level = (current_root or 1) + 1
                if normalized_level == 1:
                    current_root = normalized_level
                if outline and level != normalized_level:
                    node.get_or_add_pPr().get_or_add_outlineLvl().val = normalized_level - 1
                    changed += 1
                if layout and p.alignment != WD_ALIGN_PARAGRAPH.LEFT and not (
                        level or old_title or _title_style_name(p.style.name)):
                    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                    changed += 1
            elif (node.getparent() is body and re.search(r'[。；;！？!?]', p.text)
                  and not node.xpath('./w:pPr/w:pBdr | .//w:drawing | .//w:pict')):
                # Complete body sentences include post-table requirements and
                # copied explanatory prose; they never inherit signing alignment.
                if layout and p.alignment != WD_ALIGN_PARAGRAPH.LEFT:
                    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                    changed += 1
        if layout and role in {'information', 'note'}:
            # Table cells and pasted frames are source-backed form regions.
            # Their alignment, indentation, line breaks and tabs are data.
            if not protected_container:
                changed += _left_role(p)
    return changed + normalize_document_colors(doc)


def _clean_template_residue(doc, brief):
    """Use field aliases and the generating module's own introduction templates."""
    from api.services.tenders.pdf_format import _LABEL_FIELDS, _inline_label_field
    from api.services.tenders.technical_render import _INTRO_STARTS, _INTRO_ENDINGS
    from api.services.tenders.directory_guard import directory_blocks, cover_nodes
    excluded = directory_blocks(doc) | cover_nodes(doc)
    def template_pattern(template):
        return ''.join(re.escape(part) if not part.startswith('{') else '.+?'
                       for part in re.split(r'(\{[^}]+\})', template))
    starts = [re.compile('^' + template_pattern(t) + r'[^。]*。') for t in _INTRO_STARTS]
    endings = [re.compile(template_pattern(t) + '$') for t in _INTRO_ENDINGS]
    changed = 0
    for node in list(doc.element.body.iter(qn('w:p'))):
        if node in excluded:
            continue
        p = Paragraph(node, doc._body)
        original = ''.join(value for _, value in _inline_nodes(p))
        text = original
        for field in ('projectName', 'tenderer'):
            value = getattr(brief, field, '')
            if not value:
                continue
            aliases = '|'.join(map(re.escape, _LABEL_FIELDS[field]))
            pattern = re.compile(r'[（(【\[]\s*(?:' + aliases + r')\s*[）)】\]]')
            for match in reversed(list(pattern.finditer(text))):
                present = text[:match.start()].rstrip().endswith(value) or text[match.end():].lstrip().startswith(value)
                text = text[:match.start()] + ('' if present else value) + text[match.end():]
        double = re.fullmatch(r'\s*(?P<label>[^:：\n]+)[:：]\s*[:：]\s*(?P<value>.*)', text, re.S)
        if double:
            field = _inline_label_field(double['label'])
            value = getattr(brief, field, '') if field else ''
            text = double['label'].strip() + '：' + (str(value) if value else double['value'])
        # These are unused role-name hints, never actual supplied member names.
        text = re.sub(r'[（(](?:所有|某)?(?:联合体(?:牵头人|成员)?|成员)(?:单位)?名称[）)]', '', text)
        intro = any(pattern.match(text.strip()) for pattern in starts)
        if intro:
            text = text.strip()
            for pattern in starts:
                text = pattern.sub('', text)
            for pattern in endings:
                text = pattern.sub('', text)
        if text != original:
            _replace_inline_span(p, 0, len(original), text)
            changed += 1
    return changed


def postprocess_document(doc, brief, *, facts=None, slots=None, media=None, layout=True):
    """Shared final body pass; decisions use paragraph roles, never volume names."""
    from api.services.tenders.pdf_format import (_LABEL_FIELDS, _fill_label_gap, _fill_empty_label_cells,
                                                _following_inline_unit, reconcile_person_field_values)
    from api.services.tenders.section_materials import reconcile_generated_slots
    backed, sources = _fact_backed_brief(brief, facts)
    changed = _clean_template_residue(doc, backed)
    changed += _split_role_paragraphs(doc)
    person_repairs = reconcile_person_field_values(doc, backed)
    changed += len(person_repairs)
    fields = fill_inline_labeled_blanks(doc, backed)
    duration_labels = '|'.join(map(re.escape, _LABEL_FIELDS['deliveryDays']))
    duration = re.compile(r'(?P<label>' + duration_labels + r')\s*[:：]\s*(?P<value>\d+)(?=[，,。；;）)]|$)')
    for node in list(doc.element.body.iter(qn('w:p'))):
        p = Paragraph(node, doc._body)
        text = ''.join(value for _, value in _inline_nodes(p))
        for match in reversed(list(duration.finditer(text))):
            if (backed.deliveryDays and int(match['value']) == backed.deliveryDays
                    and not _following_inline_unit(node, text[match.end():])):
                _replace_inline_span(p, match.start('value'), match.end('value'), match['value'] + '日历天')
                changed += 1
        _fill_label_gap(node, backed, records=fields)
    for table in doc.element.body.iter(qn('w:tbl')):
        _fill_empty_label_cells(table, backed, facts)
    materials = reconcile_generated_slots(doc, backed, slots=slots, media=media)
    changed += materials['changed']
    changed += postprocess_business_document(doc, backed, layout=False)
    changed += _normalize_structure_roles(doc, layout=False)
    if layout:
        changed += finalize_body_layout(doc, brief.documentFormat)
    result = {'changed': changed, 'fields': fields, 'sources': sources, 'materials': materials,
              'person_repairs': person_repairs}
    doc.element._postprocess_audit = result
    return result


def finalize_body_layout(doc, fmt=None):
    """Run after all content writers and directory recovery on the final object."""
    changed, notes = _title_layout(doc, fmt)
    doc.element._layout_notes = notes
    changed += _normalize_structure_roles(doc, outline=False)
    changed += _align_business_signoffs(doc)
    notes.extend('SIGNOFF_NEEDS_REVIEW paragraph=' + path
                 for block in getattr(doc.element, '_signoff_audit', []) for path in block['pending'])
    return changed


def clean_visible_prompts(doc: Document) -> tuple[int, int]:
    """Remove user-facing placeholder prompts from generated body paragraphs."""
    from docx.text.paragraph import Paragraph
    from api.services.tenders.directory_guard import cover_nodes
    cover = cover_nodes(doc)

    removed = 0
    blanked = 0
    patterns = (
        re.compile(r"状态：待投标人确认"),
        re.compile(r"其他资料（待核对归属）"),
        re.compile(r"其他资料\(待核对归属\)"),
        re.compile(r"\(?（?在此粘贴扫描件）?\)?"),
        re.compile(r"\u3010\u5f85\u8865\u5145\u3011[^\n]*"),
        re.compile(r"\u5f85\u6838\u5b9e[^\n。]{0,40}"),
    )
    for element in list(doc.element.body.iter(qn("w:p"))):
        if element in cover:
            continue
        para = Paragraph(element, doc)
        original = para.text or ""
        text = original
        material_pattern = re.compile(r"对应\s*([^；\n]{1,120})；所需材料：\s*([^\n]+)")
        def material_replacement(match):
            name = match.group(2).strip()
            suffix = "" if any(word in name for word in ("扫描件", "证明", "证书")) else "扫描件"
            return f"本表后附：{name}{suffix}。"
        text = material_pattern.sub(material_replacement, text)
        for pattern in patterns:
            text = pattern.sub("", text)
        text = text.replace("\u3010\u5f85\u8865\u5145\u3011", "").replace("需补充材料", "")
        if text == original:
            continue
        if not text.strip():
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)
                removed += 1
        else:
            _clear_runs(para)
            para.add_run(text)
            blanked += 1
    return removed, blanked


def _set_tbl_borders(table: Table) -> None:
    tbl_pr = table._tbl.tblPr
    if tbl_pr is None:
        tbl_pr = OxmlElement("w:tblPr")
        table._tbl.insert(0, tbl_pr)
    for old in tbl_pr.findall(qn("w:tblBorders")):
        tbl_pr.remove(old)
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), "4")
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), "000000")
        borders.append(el)
    tbl_pr.append(borders)


def apply_uniform_border(table: Table, sz: int = 4) -> None:
    """Make table borders explicit and consistent across Word renderers."""
    tbl_pr = table._tbl.tblPr
    if tbl_pr is None:
        tbl_pr = OxmlElement("w:tblPr")
        table._tbl.insert(0, tbl_pr)
    for old in list(tbl_pr.findall(qn("w:tblBorders"))):
        tbl_pr.remove(old)
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        node = OxmlElement(f"w:{edge}")
        node.set(qn("w:val"), "single")
        node.set(qn("w:sz"), str(sz))
        node.set(qn("w:space"), "0")
        node.set(qn("w:color"), "000000")
        borders.append(node)
    tbl_pr.append(borders)


def clamp_table_width(table: Table, avail_width: int) -> None:
    """Clamp tblGrid, tblW and every tcW together, leaving 2% page margin."""
    tbl = table._tbl
    grid = tbl.find(qn("w:tblGrid"))
    if grid is None:
        return
    cols = grid.findall(qn("w:gridCol"))
    widths = [int(col.get(qn("w:w"), "0") or 0) for col in cols]
    total = sum(widths)
    if total <= 0 or total <= avail_width:
        return
    target = max(1, int(avail_width * 0.98))
    ratio = target / total
    scaled = [max(1, int(width * ratio)) for width in widths]
    scaled[-1] += target - sum(scaled)
    for col, width in zip(cols, scaled, strict=True):
        col.set(qn("w:w"), str(width))
    tbl_pr = tbl.tblPr
    if tbl_pr is None:
        tbl_pr = OxmlElement("w:tblPr")
        tbl.insert(0, tbl_pr)
    for old in list(tbl_pr.findall(qn("w:tblW"))):
        tbl_pr.remove(old)
    tbl_w = OxmlElement("w:tblW")
    tbl_w.set(qn("w:type"), "dxa")
    tbl_w.set(qn("w:w"), str(target))
    tbl_pr.append(tbl_w)
    for row in table.rows:
        for index, cell in enumerate(row.cells):
            if index >= len(scaled):
                continue
            tc_pr = cell._tc.get_or_add_tcPr()
            tc_w = tc_pr.find(qn("w:tcW"))
            if tc_w is None:
                tc_w = OxmlElement("w:tcW")
                tc_pr.append(tc_w)
            tc_w.set(qn("w:type"), "dxa")
            tc_w.set(qn("w:w"), str(scaled[index]))


def normalize_table_layout(doc: Document, avail_width: int | None = None) -> None:
    """Apply width clamps and explicit single borders to generated tables."""
    if avail_width is None:
        avail_width = max(
            1,
            int(doc.sections[0].page_width.twips)
            - int(doc.sections[0].left_margin.twips)
            - int(doc.sections[0].right_margin.twips),
        )
    for table in doc.tables:
        clamp_table_width(table, avail_width)
        borders = table._tbl.tblPr.find(qn("w:tblBorders")) if table._tbl.tblPr is not None else None
        values = {node.get(qn("w:val"), "") for node in borders} if borders is not None else set()
        if borders is None or values & {"none", "nil", "0", ""}:
            apply_uniform_border(table)


def _forbid_row_split(table: Table) -> None:
    """表行不拆页；整表优先落在一页（印鉴备案等格子表）。"""
    for row in table.rows:
        tr_pr = row._tr.get_or_add_trPr()
        for old in tr_pr.findall(qn("w:cantSplit")):
            tr_pr.remove(old)
        tr_pr.append(OxmlElement("w:cantSplit"))


def _insert_table_after(doc: Document, para: Paragraph, rows: int, cols: int) -> Table:
    table = doc.add_table(rows=rows, cols=cols)
    para._p.addnext(table._tbl)
    return table


def _quote_warnings(sheet: QuoteSheet, target: Decimal, *, origin: str) -> list[str]:
    notes: list[str] = []
    n = len(sheet.lines)
    if origin == "template":
        notes.append(
            f"未解析到本次工程量清单，分项暂用内置模板《{sheet.title}》共 {n} 项。"
            "请上传招标工程量 Excel/Word，或在页面改分项后再生成"
        )
    else:
        source_name = origin if origin in {"file", "llm", "text", "form"} else "清单"
        label = {
            "file": "上传的工程量/邀请书表格",
            "llm": "邀请书正文抽取",
            "text": "邀请书表格文本",
            "form": "页面分项明细",
        }.get(source_name, "工程量清单")
        notes.append(f"分项报价来自{label}《{sheet.title}》，共 {n} 项")
    if target <= 0:
        notes.append(
            f"分项报价表暂按清单含税 {money_text(sheet.source_inc_tax)} 元填写，生成前请填写投标总价"
        )
    elif target != sheet.source_inc_tax and sheet.source_inc_tax > 0:
        notes.append(
            f"分项单价已按投标总价从清单含税 {money_text(sheet.source_inc_tax)} 元折算"
        )
    if sheet.source_inc_tax <= 0 and any(ln.unit_price <= 0 for ln in sheet.lines):
        notes.append("清单单价为空，无法按总价折算，请补单价或确认投标总价")
    return notes


def _fill_quote_section(
    doc: Document,
    brief: BidBrief,
    anchor: Paragraph | None,
    *,
    headers: list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    sheet, origin = resolve_quote_sheet(brief)
    if sheet is None or not sheet.lines:
        if origin == 'template':
            from api.services.tenders.quote import quote_source_notes

            return quote_source_notes(brief)
        return ["未上传报价清单，未填写报价表"]
    target = Decimal(str(brief.bidPriceYuan or 0)).quantize(Decimal("0.01"))
    filled = scale_quote(sheet, target)
    notes = _quote_warnings(sheet, target, origin=origin)
    if anchor is None:
        notes.append("未找到「分项报价表」备注段，未能插入报价明细")
        return notes

    charger = headers is None
    if charger:
        titles = list(CHARGER_QUOTE_HEADERS)
        roles = [quote_role(title) or "extra" for title in titles]
        ratios = (11, 20, 58, 10, 12, 26, 23)
    else:
        titles = [str(h).strip() for h in headers if str(h).strip()]
        layout = classify_quote_headers(titles) if titles else None
        if layout is not None:
            titles = list(layout.titles)
            roles = list(layout.roles)
        else:
            titles = list(CHARGER_QUOTE_HEADERS) if not titles else titles
            roles = [quote_role(title) or "extra" for title in titles]
        ratios = width_ratios(roles)

    extra = 3
    cols = len(titles)
    table = _insert_table_after(doc, anchor, rows=1 + len(filled.lines) + extra, cols=cols)
    _set_tbl_borders(table)
    table.autofit = False
    col_twips = _distribute_twips(_usable_width_twips(doc), ratios)
    for i, title in enumerate(titles):
        _write_cell(
            table.rows[0].cells[i],
            title,
            size=9,
            bold=True,
            underline=False,
            center=True,
            bottom_line=False,
            tight=True,
        )
    for i, line in enumerate(filled.lines, start=1):
        values = _quote_row_values(line, roles, traffic_note=brief.trafficFeeNote)
        for j, val in enumerate(values):
            if j >= cols:
                break
            center = roles[j] != "spec"
            _write_cell(
                table.rows[i].cells[j],
                val,
                size=8 if roles[j] == "spec" else 9,
                bold=False,
                underline=False,
                center=center,
                bottom_line=False,
                tight=True,
            )
    summaries = (
        ("不含税合计", money_text(filled.total_ex_tax)),
        ("税率", f"{(filled.tax_rate * 100).normalize()}%" if filled.tax_rate > 0 else '【待核验】'),
        ("含税合计", money_text(filled.total_inc_tax)),
    )
    base = 1 + len(filled.lines)
    start_seq = len(filled.lines) + 1
    name_i, amount_i = name_amount_indexes(roles)
    if amount_i >= cols:
        amount_i = cols - 1
    if name_i >= cols:
        name_i = 1 if cols > 1 else 0
    for offset, (label, value) in enumerate(summaries):
        row = table.rows[base + offset]
        if amount_i > name_i + 1:
            row.cells[name_i].merge(row.cells[amount_i - 1])
            _keep_one_para(row.cells[name_i])
        if "seq" in roles:
            _write_cell(
                row.cells[0],
                str(start_seq + offset),
                size=9,
                bold=True,
                underline=False,
                center=True,
                bottom_line=False,
                tight=True,
            )
        _write_cell(
            row.cells[name_i],
            label,
            size=9,
            bold=True,
            underline=False,
            center=False,
            bottom_line=False,
            tight=True,
        )
        _write_cell(
            row.cells[amount_i],
            value,
            size=9,
            bold=True,
            underline=False,
            center=True,
            bottom_line=False,
            tight=True,
        )
        _set_row_height(row, 0.62)
    _apply_fixed_table_widths(table, col_twips)
    return notes


def _quote_row_values(line, roles: list[str], *, traffic_note: str) -> list[str]:
    spec = apply_traffic_note(line.spec, traffic_note)
    groups = list(line.groups or ())
    gi = 0
    values: list[str] = []
    for role in roles:
        if role == "seq":
            values.append(line.seq)
        elif role == "group":
            values.append(groups[gi] if gi < len(groups) else "")
            gi += 1
        elif role == "name":
            values.append(line.name)
        elif role == "spec":
            values.append(spec)
        elif role == "unit":
            values.append(line.unit)
        elif role == "qty":
            values.append(qty_text(line.qty))
        elif role == "price":
            values.append(money_text(line.unit_price))
        elif role == "amount":
            values.append(money_text(line.amount))
        else:
            values.append("")
    return values


def _fill_appendix_table(table: Table, brief: BidBrief, price_cn: str, price_en: str, facts=None) -> list[str]:
    from api.services.tenders.outline import _clean_pm_name

    notes: list[str] = []
    pm = _clean_pm_name(brief.pmName or "")
    cert = (brief.pmCertNo or "").strip()
    if cert in {"", "/", "／", "无"}:
        cert = ""
    grade = (brief.pmGrade or "").strip()
    if grade in {"/", "／", "无"}:
        grade = ""
    valid = ""
    if brief.bidValidityDays:
        valid = f"自投标截止之日起{int(brief.bidValidityDays)}日历天"
    if price_cn and price_en:
        total_price = f"（大写）{price_cn}（¥{price_en}元）"
    elif price_cn:
        total_price = f"（大写）{price_cn}"
    elif price_en:
        total_price = f"（¥{price_en}元）"
    else:
        total_price = ""
    invitation = "invitation:"
    mapping = {
        "项目名称": (brief.projectName or "", invitation),
        "投标人": (brief.bidderName or "", invitation),
        "投标总报价": (total_price, invitation),
        "投标范围": ((brief.bidContent or "").strip(), invitation),
        "投标内容": ((brief.bidContent or "").strip(), invitation),
        "投标质量": (brief.quality, invitation),
        "交货期": (f"{brief.deliveryDays}日历天" if brief.deliveryDays else "", invitation),
        "工期": (f"{brief.deliveryDays}日历天" if brief.deliveryDays else "", invitation),
        "投标工期": (f"{brief.deliveryDays}日历天" if brief.deliveryDays else "", invitation),
        "质保期": (f"基础要求{brief.warrantyYears}年" if brief.warrantyYears else "", invitation),
        "投标有效期": (valid, invitation),
        "安全生产措施费": (rmb_pair(brief.safetyFeeYuan) if brief.safetyFeeYuan is not None else "", invitation),
        "暂列金额": (rmb_pair(brief.provisionalYuan) if brief.provisionalYuan is not None else "", invitation),
        "专业工程暂估价": (rmb_pair(brief.primeCostYuan) if brief.primeCostYuan is not None else "", invitation),
        "项目经理": (pm, "qual:"),
        "注册证号": (cert, "qual:"),
        "专业等级": (grade, "qual:"),
        "缺陷责任期": ((brief.defectPeriod or "").strip(), invitation),
        "履约担保金额": ((brief.perfBond or "").strip(), invitation),
        "履约担保": ((brief.perfBond or "").strip(), invitation),
        "需要说明的问题": ((brief.extraNote or "").strip(), ""),
    }
    if facts:
        from api.services.tenders.facts import fact_fill

        for key in ("项目经理", "注册证号", "专业等级"):
            cur = mapping.get(key, ("", ""))[0]
            blank = {"", "________", "无"} if key == "专业等级" else {"", "________"}
            if cur and cur not in blank:
                continue
            val, note = fact_fill(key, "pm", facts)
            if note and note not in notes:
                notes.append(note)
            if val:
                mapping[key] = (val, "qual:")
    seen: set[str] = set()
    for row in table.rows:
        label = _compact(row.cells[0].text)
        for key, (raw_value, source) in sorted(mapping.items(), key=lambda item: len(item[0]), reverse=True):
            if key in label:
                value = raw_value if should_fill(key, source, raw_value) else ""
                if not value:
                    warning = f"{key} 无依据，已留空"
                    if warning not in notes:
                        notes.append(warning)
                _write_cell(row.cells[-1], value, underline=False)
                seen.add(key)
                break
    need = (
        "项目名称",
        "投标人",
        "投标范围",
        "投标总报价",
        "安全生产措施费",
        "暂列金额",
        "专业工程暂估价",
        "项目经理",
        "注册证号",
        "投标有效期",
        "缺陷责任期",
        "履约担保金额",
        "专业等级",
    )
    for key in need:
        if key in seen:
            continue
        if key == "投标范围" and "投标内容" in seen:
            continue
        if key == "履约担保金额" and "履约担保" in seen:
            continue
        row = table.add_row()
        _write_cell(row.cells[0], key, underline=False, bold=True, center=True, bottom_line=False)
        if len(row.cells) > 1:
            raw_value, source = mapping.get(key, ("", ""))
            value = raw_value if should_fill(key, source, raw_value) else ""
            if not value:
                warning = f"{key} 无依据，已留空"
                if warning not in notes:
                    notes.append(warning)
            _write_cell(row.cells[-1], value, underline=False)
    return notes


def _fill_payment_table(table: Table, brief: BidBrief) -> None:
    pcts = {
        "预付款": brief.prepaidPct,
        "到货款": brief.arrivalPct,
        "结算款": brief.settlementPct,
        "质保金": brief.warrantyPct,
    }
    template_p = None
    for row in table.rows[1:]:
        if len(row.cells) < 3:
            continue
        for para in row.cells[2].paragraphs:
            if _blank_runs(para) and "支付" in (para.text or ""):
                template_p = deepcopy(para._p)
                break
        if template_p is not None:
            break

    for row in table.rows[1:]:
        stage = _compact(row.cells[1].text) if len(row.cells) > 1 else ""
        for key, pct in pcts.items():
            if key not in stage:
                continue
            cell = row.cells[2]
            if template_p is not None:
                para = _replace_cell_paragraph(cell, template_p)
                _fill_blanks(para, str(pct))
            else:
                target = next((p for p in cell.paragraphs if _blank_runs(p)), None)
                if target is not None:
                    _fill_blanks(target, str(pct))
                else:
                    _write_payment_pct(cell, pct)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            break


def _replace_cell_paragraph(cell: _Cell, p_element) -> Paragraph:
    tc = cell._tc
    for child in list(tc):
        if child.tag == qn("w:p"):
            tc.remove(child)
    tc.append(deepcopy(p_element))
    return cell.paragraphs[0]


def _write_payment_pct(cell: _Cell, pct: int | str) -> None:
    """无模板空位时，按预付款格：支付至（常规）+ 下划线数字（不加粗）+ %。"""
    cell.text = ""
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    para = cell.paragraphs[0]
    _strip_para_bottom(para)
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    head = para.add_run("支付至")
    _font(head, 10.5, bold=False)
    num = para.add_run(f" {pct} ")
    _font(num, 10.5, bold=False)
    num.underline = True
    tail = para.add_run("%")
    _font(tail, 10.5, bold=False)


def _strip_cell_underline(cell: _Cell) -> None:
    for para in cell.paragraphs:
        p_pr = para._p.find(qn("w:pPr"))
        if p_pr is not None:
            p_bdr = p_pr.find(qn("w:pBdr"))
            if p_bdr is not None:
                p_pr.remove(p_bdr)
        for run in para.runs:
            run.underline = False
            rpr = run._element.find(qn("w:rPr"))
            if rpr is None:
                continue
            for uel in rpr.findall(qn("w:u")):
                rpr.remove(uel)


def _center_cell(cell: _Cell, *, horizontal: bool) -> None:
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    align = WD_ALIGN_PARAGRAPH.CENTER if horizontal else WD_ALIGN_PARAGRAPH.LEFT
    for para in cell.paragraphs:
        para.alignment = align


def _is_short_cell(text: str) -> bool:
    compact = _compact(text)
    if not compact:
        return True
    if compact in {"无偏差", "……", "...", "序号"}:
        return True
    return len(compact) <= 12


def _fill_deviation_table(table: Table, brief: BidBrief) -> None:
    rows = _deviation_rows(brief)
    _ensure_deviation_rows(table, len(rows))
    data_rows = [row for row in table.rows[1:] if "…" not in _compact(row.cells[0].text)]
    for i, item in enumerate(rows):
        if i >= len(data_rows):
            break
        row = data_rows[i]
        for j, val in enumerate(item):
            if j >= len(row.cells):
                continue
            center = j in (0, 3) or _is_short_cell(val)
            _write_cell(
                row.cells[j],
                val,
                size=9,
                bold=(j == 0),
                underline=False,
                center=center,
                bottom_line=False,
            )
    for extra in data_rows[len(rows) :]:
        for j, cell in enumerate(extra.cells):
            if j == 0:
                continue
            _write_cell(cell, "", size=9, bold=False, underline=False, bottom_line=False)
    # 对齐已在 _write_cell 完成；不再二次遍历 cell.text（长技术参数时很慢）


def _ensure_deviation_rows(table: Table, needed: int) -> None:
    """保证偏差表有足够数据行（最后一行省略号保留）。"""
    if needed <= 0 or len(table.rows) < 2:
        return
    last = table.rows[-1]
    ellipsis = "…" in _compact(last.cells[0].text)
    data_count = len(table.rows) - 1 - (1 if ellipsis else 0)
    while data_count < needed:
        src = table.rows[-2] if ellipsis and len(table.rows) > 2 else table.rows[-1]
        new_tr = deepcopy(src._tr)
        if ellipsis:
            last._tr.addprevious(new_tr)
        else:
            table._tbl.append(new_tr)
        data_count += 1


def _ordered_perf_lines(brief: BidBrief) -> list[PerformanceLine]:
    from api.services.tenders.performance import bid_performance_lines, rank_performance_lines

    lines = bid_performance_lines(brief.performanceLines)
    req = brief.performanceRequirement
    done = rank_performance_lines([item for item in lines if not item.ongoing], requirement=req)
    doing = rank_performance_lines([item for item in lines if item.ongoing], requirement=req)
    return done + doing


def _perf_amount_text(amount: float) -> str:
    if amount <= 0:
        return ""
    if amount >= 10000:
        wan = amount / 10000
        if abs(wan - round(wan)) < 0.005:
            return f"{int(round(wan))}万元"
        compact = f"{wan:.2f}".rstrip("0").rstrip(".")
        return f"{compact}万元"
    return f"{amount:,.2f}元"


def _fill_perf_table(table: Table, line: PerformanceLine | None) -> None:
    from api.services.tenders.performance import is_perf_placeholder

    if line is None or not (line.projectName or "").strip():
        fill_perf_placeholders(table)
        return
    pending = is_perf_placeholder(line)
    amount = _perf_amount_text(float(line.amountYuan or 0))
    name_hits = 0
    for row in table.rows:
        if len(row.cells) < 2:
            continue
        label = _compact(row.cells[0].text)
        value = ""
        if "项目概况" in label or "履约情况" in label:
            value = (line.summary or "").strip()
        elif "规格" in label:
            value = (line.spec or "").strip()
        elif "买方联系人" in label:
            value = (line.contact or "").strip()
        elif "买方名称" in label:
            value = (line.client or "").strip()
        elif "合同价格" in label or "签约合同价" in label:
            value = amount
        elif "备注" in label:
            value = (line.note or "").strip()
        elif "项目名称" in label:
            name_hits += 1
            value = (line.projectName or "").strip() if name_hits == 1 else (
                line.location or line.projectName or ""
            ).strip()
        if not value and pending and "项目名称" not in label:
            value = "待补充"
        if value:
            _write_cell(row.cells[-1], value, size=10.5, bold=False, underline=False)


def _append_pdf_pages(
    doc: Document,
    pdf_path: Path,
    *,
    max_pages: int = 6,
    title: bool = True,
    leading_break: bool = True,
) -> int:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        logger.warning("pypdfium2 missing, skip qualification pages")
        return 0

    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
    except Exception:
        logger.exception("open qualification pdf failed")
        return 0

    try:
        count = min(len(pdf), max_pages)
        if count <= 0:
            return 0
        from api.services.tenders.placeholders import _render_pdf_page_jpeg

        if leading_break:
            doc.add_page_break()
        if title:
            para = doc.add_paragraph()
            para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = para.add_run("附件：企业资质文件扫描件")
            _font(run, 16, bold=True)
        inserted = 0
        for i in range(count):
            try:
                page = pdf[i]
                buf = _render_pdf_page_jpeg(page)
                if i > 0:
                    doc.add_page_break()
                cap = doc.add_paragraph()
                cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                cap_run = cap.add_run(f"资质文件 第 {i + 1} 页")
                _font(cap_run, 10.5)
                pic = doc.add_paragraph()
                pic.alignment = WD_ALIGN_PARAGRAPH.CENTER
                pic.add_run().add_picture(buf, width=Cm(15.5))
                inserted += 1
            except Exception:
                logger.exception("render qualification page %s failed", i + 1)
        return inserted
    finally:
        try:
            pdf.close()
        except Exception:
            pass


def qualification_attach_notes(
    doc: Document,
    pdf_path: Path | None,
    *,
    title: bool = True,
    leading_break: bool = True,
) -> list[str]:
    if pdf_path and pdf_path.is_file():
        pages = _append_pdf_pages(
            doc, pdf_path, title=title, leading_break=leading_break
        )
        if pages:
            return [
                f"已插入资质文件 {pages} 页扫描件",
                "资质彩页/扫描件未单独编页，装订时请按招标书对图纸、彩页的约定处理",
            ]
        return ["资质 PDF 未能渲染为图片，Word 中未插入扫描件"]
    return [
        "未找到资质 PDF。可将本次投标人的资质 PDF复制到 "
        "storage/tender-assets/weitai-qualifications.pdf 后重新生成"
    ]


_PAGE_BODY_CM = 22.0
# 与目录条目对应的章标题；投标函附录 / 支付条件也另起一页，避免挤在上一节签字盖章区。
_CHAPTER_PAGE_TITLES = {
    "目录",
    "投标函及投标函附录",
    "投标函附录",
    "支付条件",
    "分项报价表",
    "法定代表人身份证明",
    "授权委托书",
    "技术偏差表",
    "原厂生产承诺",
    "其他材料",
    "技术标实施方案",
    "投标承诺书",
}

# 签字盖章块起始：在其前插入空段，给盖章留出空间
_SIGN_BLOCK_MARKS = ("盖单位公章", "盖单位章")


def _p_has_page_br(p_el) -> bool:
    return any(br.get(qn("w:type")) == "page" for br in p_el.iter(qn("w:br")))


def _p_sectpr(p_el):
    p_pr = p_el.find(qn("w:pPr"))
    if p_pr is None:
        return None
    return p_pr.find(qn("w:sectPr"))


def _sect_starts_new_page(sect_pr) -> bool:
    if sect_pr is None:
        return False
    types = [el.get(qn("w:val")) for el in sect_pr.findall(qn("w:type"))]
    if not types:
        return True
    return types[0] not in {"continuous", "evenPage", "oddPage"}


def _set_section_type(sect_pr, value: str) -> None:
    if sect_pr is None:
        return
    for old in list(sect_pr.findall(qn("w:type"))):
        sect_pr.remove(old)
    typ = OxmlElement("w:type")
    typ.set(qn("w:val"), value)
    sect_pr.append(typ)


def _set_section_next_page(sect_pr) -> None:
    _set_section_type(sect_pr, "nextPage")


def _set_section_continuous(sect_pr) -> None:
    _set_section_type(sect_pr, "continuous")


def _normalize_section_page_numbers(doc: Document) -> None:
    """封面节原先从第 11 页起算（第五章残稿），改为全书从 1 连续编号。"""
    for i, section in enumerate(doc.sections):
        sp = section._sectPr
        for old in list(sp.findall(qn("w:pgNumType"))):
            sp.remove(old)
        if i == 0:
            pg = OxmlElement("w:pgNumType")
            pg.set(qn("w:start"), "1")
            sp.append(pg)


def _normalize_body_section_breaks(doc: Document) -> None:
    """
    节间一律 continuous：模板里附录/支付条件等默认 nextPage 会多占页，
    导致目录 PAGEREF 与正文分页对不上。真正换页改由显式分页符完成。
    """
    for child in list(doc.element.body):
        if child.tag == qn("w:sectPr"):
            _set_section_continuous(child)
            continue
        if child.tag != qn("w:p"):
            continue
        sect = _p_sectpr(child)
        if sect is not None:
            _set_section_continuous(sect)


def _insert_page_break_paragraph_before(para: Paragraph) -> None:
    """插入仅含分页符的空段。比 pageBreakBefore / 分节 nextPage 更兼容各编辑器。"""
    if _prev_is_explicit_page_break(para):
        return
    br_p = OxmlElement("w:p")
    br_r = OxmlElement("w:r")
    br = OxmlElement("w:br")
    br.set(qn("w:type"), "page")
    br_r.append(br)
    br_p.append(br_r)
    para._p.addprevious(br_p)


def _plain_sectpr(doc: Document):
    sp = deepcopy(doc.sections[0]._sectPr)
    for tag in ("w:headerReference", "w:footerReference", "w:pgNumType", "w:titlePg"):
        for el in list(sp.findall(qn(tag))):
            sp.remove(el)
    _set_section_continuous(sp)
    return sp


def _end_section_at_p(p_el, sect_pr) -> None:
    p_pr = p_el.find(qn("w:pPr"))
    if p_pr is None:
        p_pr = OxmlElement("w:pPr")
        p_el.insert(0, p_pr)
    for old in list(p_pr.findall(qn("w:sectPr"))):
        p_pr.remove(old)
    p_pr.append(deepcopy(sect_pr))


def _para_section_index(doc: Document, para: Paragraph) -> int:
    idx = 0
    target = para._p
    for child in doc.element.body:
        if child is target:
            return idx
        if child.tag == qn("w:p") and _p_sectpr(child) is not None:
            idx += 1
    return idx


def _first_body_section_index(doc: Document, brief: BidBrief | None = None) -> int:
    items = _active_outline_items(brief)
    if items:
        key = _toc_match_key(items[0].title)
        if key:
            for para in doc.paragraphs:
                if _looks_like_toc_entry(para):
                    continue
                if _toc_match_key(para.text) == key:
                    return _para_section_index(doc, para)
    for para in doc.paragraphs:
        if _compact(para.text) != "投标函及投标函附录":
            continue
        if _looks_like_toc_entry(para):
            continue
        return _para_section_index(doc, para)
    return 2


def _toc_title_and_last_entry(doc: Document) -> tuple[Paragraph | None, Paragraph | None]:
    """目录标题与最后一条 PAGEREF 条目；正文标题带「一、」也不会被误认成目录。"""
    paragraphs = doc.paragraphs
    toc_title = next(
        (para for para in paragraphs if _compact(para.text) == "目录"),
        None,
    )
    if toc_title is None:
        return None, None
    last = toc_title
    started = False
    toc_idx = next(i for i, para in enumerate(paragraphs) if para._p is toc_title._p)
    for para in paragraphs[toc_idx + 1 :]:
        if _looks_like_toc_entry(para):
            last = para
            started = True
            continue
        text = (para.text or "").strip()
        if not text:
            continue
        if any(mark in text for mark in ("备注", "应编制", "页码及内容")):
            continue
        break
    return toc_title, (last if started else None)


def _body_start_after_toc(doc: Document, last_entry: Paragraph) -> Paragraph | None:
    """目录最后一条 PAGEREF 之后的第一段正文，不猜 Heading 或「一、」标题。"""
    paragraphs = doc.paragraphs
    start = next(
        (i for i, para in enumerate(paragraphs) if para._p is last_entry._p),
        None,
    )
    if start is None:
        return None
    for para in paragraphs[start + 1 :]:
        if _looks_like_toc_entry(para):
            continue
        text = (para.text or "").strip()
        if not text:
            continue
        if any(mark in text for mark in ("备注", "应编制", "页码及内容")):
            continue
        return para
    return None


def _force_page_break_paragraph_before(para: Paragraph):
    """确保目标段前是独立分页段，并返回该段，供分节使用。"""
    _set_page_break_before(para, False)
    prev = para._p.getprevious()
    if prev is not None and prev.tag == qn("w:p") and _p_has_page_br(prev):
        text = "".join(node.text or "" for node in prev.iter(qn("w:t"))).strip()
        if not text:
            return prev
    br_p = OxmlElement("w:p")
    br_r = OxmlElement("w:r")
    br = OxmlElement("w:br")
    br.set(qn("w:type"), "page")
    br_r.append(br)
    br_p.append(br_r)
    para._p.addprevious(br_p)
    return br_p


def _isolate_toc_section(doc: Document, brief: BidBrief | None = None) -> None:
    """目录独占一页：按 PAGEREF 目录块定位正文，前后各插显式分页符。"""
    toc_title, last_entry = _toc_title_and_last_entry(doc)
    if toc_title is None:
        return
    body_start = _body_start_after_toc(doc, last_entry) if last_entry is not None else None

    # 模板常为「目\t录」，规范成「目录」便于检索与排版
    if (toc_title.text or "").replace("\t", "").strip() == "目录":
        _write_plain(toc_title, "目录", size=16, bold=True)
    prev = _force_page_break_paragraph_before(toc_title)
    _end_section_at_p(prev, _plain_sectpr(doc))
    if body_start is not None:
        prev = _force_page_break_paragraph_before(body_start)
        _end_section_at_p(prev, _plain_sectpr(doc))


def _set_page_break_before(para: Paragraph, on: bool = True) -> None:
    p_pr = para._p.get_or_add_pPr()
    for old in list(p_pr.findall(qn("w:pageBreakBefore"))):
        p_pr.remove(old)
    if on:
        el = OxmlElement("w:pageBreakBefore")
        el.set(qn("w:val"), "true")
        p_pr.append(el)


def _is_chapter_start(text: str) -> bool:
    n = _compact(text)
    if not n:
        return False
    if n == "目录":
        return True
    if n in _CHAPTER_PAGE_TITLES:
        return True
    # 「（三）支付条件」「（二）投标函附录」等带序号标题
    if n.endswith("支付条件") and "投标函及" not in n:
        return True
    if n.endswith("投标函附录") and "及" not in n:
        return True
    if "近年完成的类似项目" in n:
        return True
    # 「正在供货」是业绩子表，不单独强制另起一页
    if n.startswith("附件：企业资质") or n.startswith("附件企业资质"):
        return True
    return False


def _prev_is_explicit_page_break(para: Paragraph) -> bool:
    el = para._p.getprevious()
    while el is not None:
        if el.tag == qn("w:tbl"):
            return False
        if el.tag == qn("w:p"):
            if _p_has_page_br(el) or _has_page_break_before(el):
                return True
            if _p_sectpr(el) is not None and _sect_starts_new_page(_p_sectpr(el)):
                return True
            text = "".join(t.text or "" for t in el.iter(qn("w:t"))).strip()
            if text:
                return False
            el = el.getprevious()
            continue
        el = el.getprevious()
    return False


def _outline_chapter_keys(brief: BidBrief | None) -> set[str]:
    items = _active_outline_items(brief)
    if not items:
        return set()
    levels = [max(1, int(getattr(item, "level", 1) or 1)) for item in items]
    base = min(levels)
    return {
        _toc_match_key(item.title)
        for item, level in zip(items, levels, strict=True)
        if level <= base
    }


def _mark_chapter_page_starts(doc: Document, brief: BidBrief | None = None) -> None:
    """各章标题另起一页。优先显式分页符，已有换页则不重复。"""
    outline_keys = _outline_chapter_keys(brief)
    for para in doc.paragraphs:
        n = _compact(para.text)
        if n == "目录" or _looks_like_toc_entry(para):
            continue
        if not (para.text or "").strip():
            continue
        is_start = bool(outline_keys and _toc_match_key(para.text) in outline_keys)
        if not is_start and _is_chapter_start(para.text):
            is_start = True
        if not is_start:
            continue
        if _prev_is_explicit_page_break(para):
            _set_page_break_before(para, False)
            continue
        _insert_page_break_paragraph_before(para)
        _set_page_break_before(para, False)


def _is_signature_block_start(text: str) -> bool:
    raw = text or ""
    n = _compact(raw)
    if not any(mark in n for mark in _SIGN_BLOCK_MARKS):
        return False
    return (
        n.startswith("投标人")
        or n.startswith("投标人名称")
        or "投标人" in n[:12]
        or "投 标 人" in raw
        or "投　标　人" in raw
    )


def _count_blank_paras_before(para: Paragraph, *, limit: int = 6) -> int:
    n = 0
    el = para._p.getprevious()
    while el is not None and n < limit:
        if el.tag != qn("w:p"):
            break
        if _p_has_page_br(el) or _has_page_break_before(el):
            break
        if _p_sectpr(el) is not None:
            break
        text = "".join(t.text or "" for t in el.iter(qn("w:t"))).strip()
        if text:
            break
        n += 1
        el = el.getprevious()
    return n


def _sign_follows_letter_body(para: Paragraph) -> bool:
    """投标函/附录正文已经很长，再垫空段会把落款挤到下一页。"""
    el = para._p.getprevious()
    for _ in range(36):
        if el is None:
            break
        if el.tag == qn("w:p"):
            t = "".join(n.text or "" for n in el.iter(qn("w:t")))
            if any(
                k in t
                for k in (
                    "我方已仔细研究",
                    "我方已认真研究",
                    "承包工程",
                    "不撤销投标",
                    "投标函附录",
                    "愿意以人民币",
                )
            ):
                return True
        el = el.getprevious()
    return False


def _pad_signature_blocks(doc: Document, *, blanks: int = 3) -> None:
    """Compatibility entry: compact existing gaps, never add signature blanks."""
    from api.services.tenders.pdf_format import _compact_signature_flow
    _compact_signature_flow(doc)


def _drawing_height_cm(el) -> float:
    total = 0.0
    for child in el.iter():
        if child.tag.split("}")[-1] != "extent":
            continue
        cy = child.get("cy")
        if cy:
            total += int(cy) / 914400 * 2.54
    return total


def _para_height_cm(para: Paragraph) -> float:
    drawn = _drawing_height_cm(para._p)
    if drawn:
        return min(drawn + 0.6, _PAGE_BODY_CM)
    text = (para.text or "").strip()
    if not text:
        return 0.28
    size = 12.0
    for run in para.runs:
        if run.font.size:
            size = max(size, float(run.font.size.pt))
            break
    line_h = max(0.55, size / 72 * 2.54 * 1.55)
    lines = max(1, (len(text) + 31) // 32)
    return min(line_h * lines, _PAGE_BODY_CM)


def _table_height_cm(table: Table) -> float:
    """估算表高。不读 cell.text（长技术参数/合并格在 python-docx 里很慢）。"""
    total = 0.35
    for row in table.rows:
        tr_pr = row._tr.find(qn("w:trPr"))
        row_cm = 0.0
        if tr_pr is not None:
            tr_h = tr_pr.find(qn("w:trHeight"))
            if tr_h is not None:
                raw = tr_h.get(qn("w:val"))
                if raw:
                    row_cm = int(raw) / 1440 * 2.54
        if row_cm <= 0:
            # 按 XML 文本长度粗估，避免 Table.cell.text 展开合并单元格
            longest = 0
            for tc in row._tr.findall(qn("w:tc")):
                text_len = sum(len(t.text or "") for t in tc.iter(qn("w:t")))
                longest = max(longest, text_len)
            lines = max(1, (longest + 17) // 18)
            row_cm = min(2.2, 0.52 * lines)
        total += max(row_cm, 0.45)
    return total


def _bookmark_names(el) -> list[str]:
    names: list[str] = []
    for start in el.iter(qn("w:bookmarkStart")):
        name = start.get(qn("w:name"))
        if name and not name.startswith("_"):
            names.append(name)
    return names


def _has_page_break_before(p_el) -> bool:
    p_pr = p_el.find(qn("w:pPr"))
    if p_pr is None:
        return False
    el = p_pr.find(qn("w:pageBreakBefore"))
    if el is None:
        return False
    return el.get(qn("w:val")) not in {"0", "false"}


def _next_page_section_starts(doc: Document) -> set[int]:
    """各节起始子节点下标。第一节是封面，其后 type=nextPage 的节另起一页。"""
    body = list(doc.element.body)
    starts: set[int] = set()
    first = 0
    section_i = 0
    for i, child in enumerate(body):
        sect = _p_sectpr(child) if child.tag == qn("w:p") else child if child.tag == qn("w:sectPr") else None
        if sect is None:
            continue
        if section_i > 0 and _sect_starts_new_page(sect):
            starts.add(first)
        section_i += 1
        first = i + 1
    return starts


def _estimate_bookmark_pages(doc: Document) -> dict[str, int]:
    """按显式分页符、nextPage 分节和下一块高度估算书签所在页，写入目录缓存。"""
    page = 1
    used = 0.0
    pages: dict[str, int] = {}
    new_section_at = _next_page_section_starts(doc)

    def force_new_page(*, hard: bool = False) -> None:
        """soft：仅在本页已有内容时换页；hard：正文中的强制分页一律 +1。"""
        nonlocal page, used
        if hard:
            page += 1
            used = 0.0
            return
        if used > 0.05:
            page += 1
            used = 0.0

    def consume(cm: float) -> None:
        nonlocal page, used
        remain = max(cm, 0.0)
        while remain > 0:
            space = _PAGE_BODY_CM - used
            if space <= 0.35:
                page += 1
                used = 0.0
                space = _PAGE_BODY_CM
            take = min(remain, space)
            used += take
            remain -= take

    for index, child in enumerate(list(doc.element.body)):
        if child.tag == qn("w:sectPr"):
            continue
        if child.tag == qn("w:tbl"):
            if index in new_section_at:
                force_new_page(hard=index > 0)
            table = Table(child, doc)
            for name in _bookmark_names(child):
                pages[name] = page
            consume(_table_height_cm(table))
            continue
        if child.tag != qn("w:p"):
            continue
        para = Paragraph(child, doc)
        bare_break = _p_has_page_br(child) and not (para.text or "").strip() and _drawing_height_cm(child) <= 0
        hard_break = index in new_section_at or _has_page_break_before(child) or bare_break
        if hard_break:
            if index > 0:
                force_new_page(hard=True)
            if bare_break:
                continue
        for name in _bookmark_names(child):
            if name.startswith("toc_"):
                pages[name] = page
        if not bare_break:
            consume(_para_height_cm(para))
    return pages


def _pageref_bookmark(para: Paragraph) -> str | None:
    for el in para._p.iter(qn("w:instrText")):
        parts = (el.text or "").split()
        if len(parts) >= 2 and parts[0].upper() == "PAGEREF":
            return parts[1]
    return None


def _set_pageref_cache(para: Paragraph, number: str) -> None:
    in_result = False
    for run in para._p.iter(qn("w:r")):
        fld = run.find(qn("w:fldChar"))
        if fld is not None:
            kind = fld.get(qn("w:fldCharType"))
            if kind == "separate":
                in_result = True
            elif kind == "end":
                in_result = False
            continue
        if not in_result:
            continue
        text_el = run.find(qn("w:t"))
        if text_el is not None:
            text_el.text = number
            return


def _apply_toc_page_numbers(
    doc: Document,
    pages: dict[str, int],
    *,
    pending: str | None = None,
) -> None:
    for para in doc.paragraphs:
        bookmark = _pageref_bookmark(para)
        if not bookmark:
            continue
        if bookmark in pages:
            _set_pageref_cache(para, str(pages[bookmark]))
        elif pending is not None:
            # 估不到页码就明确标待更新，不写伪造的 1 把锚点问题盖住。
            _set_pageref_cache(para, pending)


def _relative_toc_pages(raw: dict[str, int], fmt=None) -> dict[str, int]:
    if not raw:
        return {}
    start = fmt.pageNumberStart if fmt is not None else "body"
    if start == "body":
        base = min(raw.values())
        return {name: max(1, page - base + 1) for name, page in raw.items()}
    if start == "cover":
        return {name: max(1, page) for name, page in raw.items()}
    return {name: max(1, page - 1) for name, page in raw.items()}


def _finalize_pagination(
    doc: Document, fmt=None, brief: BidBrief | None = None
) -> dict[str, int]:
    """分页：分节 continuous + 章标题显式分页；目录页码按正文书签实测估算。"""
    from api.services.tenders.material_layout import mark_repeating_headers
    mark_repeating_headers(doc)
    _normalize_section_page_numbers(doc)
    _normalize_body_section_breaks(doc)
    _isolate_toc_section(doc, brief)
    _mark_chapter_page_starts(doc, brief)
    _pad_signature_blocks(doc)
    pages = _relative_toc_pages(_estimate_bookmark_pages(doc), fmt)
    _apply_toc_page_numbers(doc, pages, pending="待更新")
    return pages


def _apply_template_format(doc: Document, brief: BidBrief, volume: str = "") -> list[str]:
    """公司模板：页脚仍按原规则。页边距和正文字体只在该卷标书写明时改。"""
    from api.services.tenders.format_rules import apply_volume_format
    from api.services.tenders.schema import VolumeFormat

    fmt = brief.documentFormat
    notes = format_brief_notes(fmt)
    if fmt.coverNeedSeal:
        notes.append("招标书要求封面加盖公章，请在打印后于封面预留处盖章")
    vol = (volume or "").strip().lower()
    spec: VolumeFormat | None = None
    if vol == "technical":
        spec = fmt.technicalFormat
    elif vol == "business":
        spec = fmt.businessFormat
    body_si = _first_body_section_index(doc, brief)
    for i, section in enumerate(doc.sections):
        if spec is not None and spec.marginSet:
            section.left_margin = Cm(spec.marginLeftCm)
            section.right_margin = Cm(spec.marginRightCm)
            section.top_margin = Cm(spec.marginTopCm)
            section.bottom_margin = Cm(spec.marginBottomCm)
        numbered, restart = section_page_flags(i, fmt, body_section=body_si)
        apply_footer_page_number(section, fmt, numbered=numbered, restart=restart)
    if spec is not None and (spec.fontSet or spec.bodySizeSet):
        font_only = VolumeFormat(
            fontSet=spec.fontSet,
            fontName=spec.fontName,
            bodySizeSet=spec.bodySizeSet,
            bodySizePt=spec.bodySizePt,
        )
        painted = apply_volume_format(doc, font_only, where="公司模板")
        notes.extend(n for n in painted if "正文" in n)
    if spec is not None and spec.marginSet:
        notes.append(
            "已按标书改公司模板页边距"
            f" 上{spec.marginTopCm:g} 下{spec.marginBottomCm:g}"
            f" 左{spec.marginLeftCm:g} 右{spec.marginRightCm:g}cm"
        )
    return notes


def build_bid_docx(
    brief: BidBrief,
    dest: Path,
    *,
    qualification_pdf: Path | None = None,
    catalog_slots: list | None = None,
    catalog_media: dict | None = None,
    catalog_facts: list | None = None,
    volume: str = "",
    defer_layout: bool = False,
) -> tuple[Path, list[str]]:
    from api.services.tenders.native_format import prefer_native_format
    from api.services.tenders.quote import validate_quote_source

    # 只有本次用户来源的代理人字段能进入文档渲染；历史值和孤立证号都清空。
    if (getattr(brief, "agentNameSource", "") or "").strip().lower() == "history":
        brief.agentName = ""
        brief.agentIdNo = ""
        brief.agentNameSource = ""
        brief.agentIdNoSource = ""
    elif not (brief.agentName or "").strip():
        brief.agentIdNo = ""
        brief.agentIdNoSource = ""
    else:
        brief.agentNameSource = "user"
        brief.agentIdNoSource = "user" if (brief.agentIdNo or "").strip() else ""
    quote_notes = validate_quote_source(brief)
    if prefer_native_format(brief):
        from api.services.tenders.assemble import assemble_bid_docx

        return assemble_bid_docx(
            brief, dest, qualification_pdf=qualification_pdf, catalog_slots=catalog_slots,
            catalog_media=catalog_media, catalog_facts=catalog_facts, volume=volume,
        )
    if brief.requirementsDriven:
        # Reuse the outline renderer's modules and shared TOC/page implementation.
        # This branch does not copy the fixed company template or the incomplete source.
        from api.services.tenders.assemble import assemble_bid_docx
        from api.services.tenders.requirement_bid import prepare_requirements_bid

        prepared = brief.model_copy(deep=True)
        notes = []
        if prepared.layoutMode != 'requirements' or not prepared.outlineItems:
            notes = prepare_requirements_bid(prepared)
        render_brief = prepared.model_copy(update={'layoutMode':'requirements'})
        path, rendered_notes = assemble_bid_docx(
            render_brief, dest,
            qualification_pdf=qualification_pdf, catalog_slots=catalog_slots,
            catalog_media=catalog_media, catalog_facts=catalog_facts, volume=volume,
        )
        from api.services.tenders.material_intents import sync_material_outcomes

        sync_material_outcomes(brief,render_brief)
        return path, list(dict.fromkeys([*notes, *rendered_notes]))
    warnings: list[str] = list(quote_notes)
    if brief.bidPriceYuan <= 0:
        warnings.append("投标总价为 0，请确认是否已填写报价")
    if brief.bidBondYuan is None:
        warnings.append("未解析到投标保证金金额，投标函保留空白")
    if not (brief.legalPersonName or "").strip():
        warnings.append("未填写法定代表人姓名，身份证明中该栏为空白")
    pct = brief.prepaidPct + brief.arrivalPct + brief.settlementPct + brief.warrantyPct
    if pct != 100:
        warnings.append(f"支付比例合计为 {pct}%，建议为 100%")

    template = find_chapter5_template()
    if template is None:
        raise FileNotFoundError(
            "未找到第五章投标文件格式模板。请将空白稿保存为 "
            "apps/api/services/tenders/templates/chapter5.docx"
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template, dest)
    doc = Document(str(dest))
    _pin_cjk_fonts(doc)
    _hide_proofing_marks(doc)
    if catalog_slots is None:
        from api.services.tenders.slots import prepare_bid_slots

        slots, local_media = prepare_bid_slots(brief)
        media = catalog_media if catalog_media is not None else local_media
    else:
        slots = catalog_slots
        media = catalog_media if catalog_media is not None else attachments_for_slots(slots)
    from api.services.tenders.slots import missing_library_notes

    warnings.extend(missing_library_notes(slots, media, volume=volume))
    inlined_ids: set[str] = set()
    quote_anchor = _fill_paragraphs(doc, brief, media=media, inlined=inlined_ids)
    if not (brief.extraNote or "").strip():
        warnings.append("其他补充说明 无依据，已留空")
    # 先清掉模板旧目录，避免填写技术标时把目录行的「其他材料」当成正文标题。
    _ensure_toc_entries(
        doc,
        include_commitment=bool(brief.includeCommitment),
        with_pages=True,
        fmt=brief.documentFormat,
        brief=brief,
    )
    warnings.extend(_fill_technical_section(doc, brief, media))
    warnings.extend(_fill_tables(doc, brief, catalog_facts))
    warnings.extend(_fill_quote_section(doc, brief, quote_anchor))
    inline_records = fill_inline_labeled_blanks(doc, brief)
    inline_filled = sum(int(item.get("filled", 0)) for item in inline_records)
    inline_missing = sorted({str(item["field"]) for item in inline_records if item.get("missing")})
    if inline_filled:
        warnings.append(f"行内标签填空已填 {inline_filled} 处")
    if inline_missing:
        warnings.append("行内标签字段缺失，保留原下划线：" + "、".join(inline_missing))
    _normalize_blank_underlines(doc)

    if brief.includeCommitment:
        append_commitment_letter(doc, brief)
        for para in reversed(doc.paragraphs):
            if _compact(para.text) == "投标承诺书":
                _bookmark_paragraph(para, "toc_commit")
                break
        warnings.append("已附「投标承诺书」（附件五），请核对后签字盖章")

    if brief.attachQualifications:
        warnings.extend(qualification_attach_notes(doc, qualification_pdf))

    from api.services.tenders.placeholders import place_unmatched_slots

    warnings.extend(
        place_unmatched_slots(
            doc,
            slots=slots,
            media=media,
            required_keys=brief.requiredSlotKeys,
            volume=volume,
            skip_keys=inlined_ids,
            requirements=list(brief.bidRequirements or []),
        )
    )
    # 正文（含承诺书、附件、占位资料）全部落位后再定稿目录，确保目录与章节同源。
    toc_skipped = _ensure_toc_entries(
        doc,
        include_commitment=bool(brief.includeCommitment),
        with_pages=True,
        fmt=brief.documentFormat,
        brief=brief,
    )
    if toc_skipped:
        warnings.append(
            "目录仅收录正文存在的章节，以下大纲条目正文缺失，未写入目录："
            + "、".join(dict.fromkeys(toc_skipped))
        )
    from api.services.tenders.business_render import finalize_business_responses

    if volume != "technical":
        warnings.extend(finalize_business_responses(
            doc, brief, **({"full_bid": True} if volume == "full" else {})
        ))
    if brief.materialDemands:
        from api.services.tenders.material_intents import enforce_material_outcomes

        warnings.extend(enforce_material_outcomes(doc,brief,media=media,slots=slots,volume=volume or 'business'))
    _finalize_pagination(doc, brief.documentFormat, brief)
    for i, section in enumerate(doc.sections):
        _ensure_header(section, brief.projectName, cover=(i == 0))
    warnings.extend(_apply_template_format(doc, brief, volume))
    _set_bookmark_outline(doc, {
        "toc_letter": 1, "toc_letter_app": 2, "toc_pay": 1, "toc_quote": 1,
        "toc_legal": 1, "toc_auth": 1, "toc_dev": 1, "toc_factory": 1,
        "toc_other": 1, "toc_other_draw": 2, "toc_other_text": 2,
        "toc_commit": 1, "toc_perf": 1,
    })
    from api.services.tenders.section_materials import validate_material_uniqueness
    validate_material_uniqueness(doc)
    clean_visible_prompts(doc)
    normalize_signoff_block(doc.paragraphs)
    normalize_table_layout(doc)
    normalize_document_colors(doc)
    from api.services.tenders.technical_toc import finalize
    warnings.extend(finalize(doc, brief, facts=catalog_facts, slots=slots, media=media, layout=not defer_layout))
    doc.save(str(dest))
    warnings.append(
        "封面与目录不编页码，从正文首页起编。目录条目已预填页码；"
        "若与正文不符，可在 Word/WPS 中右键目录域「更新域」。"
        "目录独占一页；投标函附录/支付条件等签字节另起一页，签章前已留白。"
    )
    return dest, warnings


def build_bid_format_skeleton(brief: BidBrief, dest: Path) -> Path:
    """Preview the generated outline's layout without responses, prices or private facts."""
    from api.services.tenders.assemble import (
        _add_next_section,
        _chapter_break,
        _display_title,
        _fill_toc_pages,
        _write_cover,
        _write_heading,
        _write_toc,
    )
    from api.services.tenders.requirement_bid import volume_items
    from api.services.tenders.schema import PlaceholderItem

    preview = brief.model_copy(deep=True)
    preview.bidderName = '【待填写】'
    preview.legalPersonName = ''
    preview.agentName = ''
    preview.bidDate = ''
    preview.techPlanNote = ''
    preview.outlineItems = [item.model_copy(update={
        'body': '', 'draftBody': '', 'inviteExcerpt': '',
    }) for item in preview.outlineItems]
    volume = (preview.generateVolume
              if preview.generateVolume in {'business', 'technical'} else 'full')
    items = volume_items(preview, volume)
    doc = Document()
    fmt = preview.documentFormat
    apply_section_page(doc.sections[0], fmt)
    _pin_cjk_fonts(doc)
    _write_cover(doc, preview, fmt, volume=volume)
    _add_next_section(doc, fmt)
    bookmarks = _subset_bookmarks(doc, items)
    _write_toc(doc, items, preview, fmt, bookmarks)
    body_section = 1
    if fmt.pageNumberStart == 'body':
        _add_next_section(doc, fmt)
        body_section = 2
    for index, (item, bookmark) in enumerate(zip(items, bookmarks, strict=True)):
        if index or fmt.pageNumberStart != 'body':
            _chapter_break(doc)
        _write_heading(doc, _display_title(item), fmt, bookmark)
        if item.id.startswith('req-group-'):
            continue
        doc.add_paragraph('【待生成】本章仅展示格式和落位，正文及资料将在正式生成时填写。')
        if item.kind == 'scan':
            draw_placeholder_box(doc, PlaceholderItem(key=item.id, title=item.title,
                                                      hint='证明材料待正式生成时匹配'))
    for index, section in enumerate(doc.sections):
        numbered, restart = section_page_flags(index, fmt, body_section=body_section)
        _ensure_header(section, preview.projectName, cover=(index == 0))
        apply_footer_page_number(section, fmt, numbered=numbered, restart=restart)
    _set_bookmark_outline(doc, {bm: item.level for bm, item in zip(bookmarks, items, strict=True)})
    _fill_toc_pages(doc, fmt)
    dest.parent.mkdir(parents=True, exist_ok=True)
    normalize_document_colors(doc)
    doc.save(dest)
    return dest
