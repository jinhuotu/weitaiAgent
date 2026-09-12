"""从邀请书抽出排版条款，并写入 Word 页边距 / 页码 / 目录编号。"""

from __future__ import annotations

import re

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

from api.services.tenders.schema import DocumentFormat

_CN_DIGITS = "一二三四五六七八九"

_CN_PT = {
    "初号": 42.0,
    "小初": 36.0,
    "一号": 26.0,
    "小一": 24.0,
    "二号": 22.0,
    "小二": 18.0,
    "三号": 16.0,
    "小四": 12.0,
    "四号": 14.0,
    "小三": 15.0,
    "五号": 10.5,
    "小五": 9.0,
    "六号": 7.5,
}

_PT_NUM = re.compile(r"(\d+(?:\.\d+)?)\s*(?:磅|pt|PT)")
_CN_SIZE = re.compile(r"(小初|初号|小一|一号|小二|二号|小三|三号|小四|四号|小五|五号|小六|六号)")
_TENDER_NO = re.compile(
    r"(?:招标编号|项目编号|采购编号|招标文件编号)\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9\-_/]{2,31})"
)
_ALL_MARGIN = re.compile(
    r"页边距[^。\n]{0,24}(?:均为|全部为|都为)\s*(\d+(?:\.\d+)?)\s*(cm|CM|厘米|mm|MM|毫米)"
)
_NAMED_MARGIN = re.compile(
    r"(上|下|左|右)(?:边距|页边距)?\s*(?:为|:|：)?\s*(\d+(?:\.\d+)?)\s*(cm|CM|厘米|mm|MM|毫米)"
)
_QUAD_MARGIN = re.compile(
    r"(?:上|上边距)[^。\n]{0,8}?(\d+(?:\.\d+)?)\s*(?:cm|厘米|mm|毫米)"
    r"[^。\n]{0,12}?(?:下|下边距)[^。\n]{0,8}?(\d+(?:\.\d+)?)\s*(?:cm|厘米|mm|毫米)"
    r"[^。\n]{0,12}?(?:左|左边距)[^。\n]{0,8}?(\d+(?:\.\d+)?)\s*(?:cm|厘米|mm|毫米)"
    r"[^。\n]{0,12}?(?:右|右边距)[^。\n]{0,8}?(\d+(?:\.\d+)?)\s*(?:cm|厘米|mm|毫米)",
    re.I,
)


def extract_tender_no(text: str) -> str:
    match = _TENDER_NO.search(text or "")
    if not match:
        return ""
    return match.group(1).strip().rstrip("。；;，,")


def extract_document_format(text: str) -> DocumentFormat:
    raw = text or ""
    fmt = DocumentFormat()
    notes: list[str] = []
    blob = re.sub(r"\s+", "", raw)

    _apply_margins(fmt, raw, notes)
    _apply_fonts(fmt, raw, blob, notes)
    _apply_cover(fmt, blob, notes)
    _apply_toc(fmt, blob, notes)
    _apply_page_numbers(fmt, blob, notes)

    no = extract_tender_no(raw)
    if no and fmt.coverShowTenderNo:
        notes.append(f"招标编号 {no}")
    fmt.notes = notes
    fmt.specified = bool(notes)
    return fmt


def cn_ordinal(n: int) -> str:
    """目录序号：十一、十二…，与固定模板「一、二、三」同一套汉字。"""
    if n <= 0:
        return str(n)
    if n < 10:
        return _CN_DIGITS[n - 1]
    if n == 10:
        return "十"
    if n < 20:
        return "十" + _CN_DIGITS[n - 11]
    if n < 100:
        tens, ones = divmod(n, 10)
        head = _CN_DIGITS[tens - 1] + "十"
        return head if ones == 0 else head + _CN_DIGITS[ones - 1]
    return str(n)


def toc_item_label(index: int, scheme: str) -> str:
    n = index + 1
    cn = cn_ordinal(n)
    if scheme == "arabic":
        return f"{n}."
    if scheme == "paren":
        return f"({n})"
    if scheme == "attach":
        return f"附件{cn}"
    return f"{cn}、"


def toc_page_cache(index: int, fmt: DocumentFormat) -> str:
    """打开 Word 前的占位页码。封面不编时目录为第 1 页，正文条目从第 2 页起。"""
    if fmt.pageNumberStart == "body":
        return str(index + 1)
    if fmt.pageNumberStart == "cover":
        return str(index + 3)
    return str(index + 2)


def section_page_flags(
    section_index: int, fmt: DocumentFormat, *, body_section: int
) -> tuple[bool, bool]:
    """返回 (是否编页码, 是否从 1 重新起编)。"""
    if fmt.pageNumberPos == "none":
        return False, False
    if fmt.pageNumberStart == "cover":
        return True, section_index == 0
    if fmt.pageNumberStart == "body":
        return section_index >= body_section, section_index == body_section
    return section_index >= 1, section_index == 1


def apply_section_page(section, fmt: DocumentFormat) -> None:
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.left_margin = Cm(fmt.marginLeftCm)
    section.right_margin = Cm(fmt.marginRightCm)
    section.top_margin = Cm(fmt.marginTopCm)
    section.bottom_margin = Cm(fmt.marginBottomCm)
    section.header_distance = Cm(1.75)
    section.footer_distance = Cm(1.5)


def apply_footer_page_number(
    section,
    fmt: DocumentFormat,
    *,
    numbered: bool,
    restart: bool,
) -> None:
    footer = section.footer
    footer.is_linked_to_previous = False
    para = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    _clear_runs(para)
    para.paragraph_format.space_before = Pt(0)
    para.paragraph_format.space_after = Pt(0)
    _set_pg_num_type(section, start=1 if restart and numbered else None)
    if not numbered or fmt.pageNumberPos == "none":
        if getattr(section, "different_first_page_header_footer", False):
            first = section.first_page_footer
            first.is_linked_to_previous = False
            first_para = first.paragraphs[0] if first.paragraphs else first.add_paragraph()
            _clear_runs(first_para)
        return
    para.alignment = (
        WD_ALIGN_PARAGRAPH.RIGHT
        if fmt.pageNumberPos == "bottom-right"
        else WD_ALIGN_PARAGRAPH.CENTER
    )
    _add_page_field(para, font=fmt.fontName, size_pt=10.5)


def format_brief_notes(fmt: DocumentFormat) -> list[str]:
    if fmt.specified and fmt.notes:
        return ["已按招标书抽出排版要求：" + "；".join(fmt.notes[:8])]
    return [
        "招标书未写页边距/字体/页码等排版条款，"
        "生成用默认 A4、宋体，封面不编页码、从目录起页底居中编码"
    ]


def _to_cm(value: str, unit: str) -> float:
    num = float(value)
    key = (unit or "cm").lower()
    if key in {"mm", "毫米"}:
        num = num / 10.0
    return max(1.0, min(5.0, round(num, 2)))


def _apply_margins(fmt: DocumentFormat, raw: str, notes: list[str]) -> None:
    all_m = _ALL_MARGIN.search(raw)
    if all_m:
        cm = _to_cm(all_m.group(1), all_m.group(2))
        fmt.marginTopCm = fmt.marginBottomCm = fmt.marginLeftCm = fmt.marginRightCm = cm
        notes.append(f"页边距均为 {cm:g}cm")
        return
    quad = _QUAD_MARGIN.search(raw)
    if quad:
        fmt.marginTopCm = _to_cm(quad.group(1), "cm")
        fmt.marginBottomCm = _to_cm(quad.group(2), "cm")
        fmt.marginLeftCm = _to_cm(quad.group(3), "cm")
        fmt.marginRightCm = _to_cm(quad.group(4), "cm")
        notes.append(
            f"页边距 上{fmt.marginTopCm:g} 下{fmt.marginBottomCm:g} "
            f"左{fmt.marginLeftCm:g} 右{fmt.marginRightCm:g}cm"
        )
        return
    found = False
    mapping = {
        "上": "marginTopCm",
        "下": "marginBottomCm",
        "左": "marginLeftCm",
        "右": "marginRightCm",
    }
    for match in _NAMED_MARGIN.finditer(raw):
        setattr(fmt, mapping[match.group(1)], _to_cm(match.group(2), match.group(3)))
        found = True
    if found:
        notes.append(
            f"页边距 上{fmt.marginTopCm:g} 下{fmt.marginBottomCm:g} "
            f"左{fmt.marginLeftCm:g} 右{fmt.marginRightCm:g}cm"
        )


def _pt_from_blob(blob: str) -> float | None:
    named = _CN_SIZE.search(blob)
    if named:
        return _CN_PT.get(named.group(1))
    num = _PT_NUM.search(blob)
    if num:
        return max(8.0, min(42.0, float(num.group(1))))
    return None


def _apply_fonts(fmt: DocumentFormat, raw: str, blob: str, notes: list[str]) -> None:
    fonts = "仿宋_GB2312|楷体_GB2312|宋体|仿宋|黑体|楷体|微软雅黑|Times New Roman"
    font = re.search(
        r"(?:字体|正文|全文|采用)[^。\n]{0,20}(" + fonts + r")"
        r"|(" + fonts + r")(?:、|/|和)?(?:小?[一二三四五]号|\d+(?:\.\d+)?\s*(?:磅|pt))",
        raw,
    )
    if font:
        fmt.fontName = next(g for g in font.groups() if g)
        notes.append(f"正文字体 {fmt.fontName}")
    body_ctx = re.search(r"(?:正文|全文)[^。\n]{0,24}(?:字体|字号|用)?[^。\n]{0,16}", raw)
    if body_ctx:
        pt = _pt_from_blob(body_ctx.group(0))
        if pt:
            fmt.bodySizePt = pt
            notes.append(f"正文 {pt:g} 磅")
    elif "正文" in blob and "小四" in blob:
        fmt.bodySizePt = 12.0
        notes.append("正文小四")
    head_ctx = re.search(r"(?:一级标题|章节标题|标题字号|标题用)[^。\n]{0,24}", raw)
    if head_ctx:
        pt = _pt_from_blob(head_ctx.group(0))
        if pt:
            fmt.headingSizePt = pt
            notes.append(f"标题 {pt:g} 磅")
    cover_ctx = re.search(r"封面[^。\n]{0,30}(?:标题|字体|字号)", raw)
    if cover_ctx:
        pt = _pt_from_blob(cover_ctx.group(0))
        if pt:
            fmt.coverTitleSizePt = pt
            notes.append(f"封面标题 {pt:g} 磅")


def _near_cover(blob: str, *needles: str) -> bool:
    for needle in needles:
        if re.search(rf"封面.{{0,48}}{re.escape(needle)}|{re.escape(needle)}.{{0,48}}封面", blob):
            return True
    return False


def _apply_cover(fmt: DocumentFormat, blob: str, notes: list[str]) -> None:
    if "封面" not in blob:
        return
    fmt.coverRequired = True
    extra: list[str] = []
    if _near_cover(blob, "项目全称", "项目名称"):
        fmt.coverShowProject = True
    if _near_cover(blob, "招标编号", "项目编号", "采购编号"):
        fmt.coverShowTenderNo = True
        extra.append("招标编号")
    if _near_cover(blob, "投标人全称", "投标人名称", "供应商名称"):
        fmt.coverShowBidder = True
    if _near_cover(blob, "正本", "副本"):
        fmt.coverShowCopyMark = True
        if _near_cover(blob, "副本") and not _near_cover(blob, "正本"):
            fmt.coverCopyMark = "副本"
        else:
            fmt.coverCopyMark = "正本"
        extra.append(fmt.coverCopyMark)
    if _near_cover(blob, "投标日期", "日期"):
        fmt.coverShowDate = True
    if _near_cover(blob, "加盖公章", "盖章", "公章") or "封面加盖" in blob:
        fmt.coverNeedSeal = True
        extra.append("加盖公章")
    if extra:
        notes.append("封面含" + "、".join(extra))


def _apply_toc(fmt: DocumentFormat, blob: str, notes: list[str]) -> None:
    if "目录" not in blob:
        return
    if re.search(r"目录[^。]{0,20}(?:按附件|采用附件)|目录按附件", blob):
        fmt.tocNumbering = "attach"
        notes.append("目录按附件编号")
    elif re.search(r"目录[^。]{0,20}阿拉伯|目录[^。]{0,12}(?:用|按|采用)[0-9]", blob):
        fmt.tocNumbering = "arabic"
        notes.append("目录用1.编号")
    elif re.search(r"目录[^。]{0,16}(?:用|按|采用)[（(]1[)）]", blob):
        fmt.tocNumbering = "paren"
        notes.append("目录用(1)编号")
    toc_page_keys = ("目录页码", "页码一一对应", "不能错页", "页码对应", "目录与正文页码")
    if any(k in blob for k in toc_page_keys):
        fmt.tocNeedPageNos = True
        notes.append("目录页码与正文对应")


def _apply_page_numbers(fmt: DocumentFormat, blob: str, notes: list[str]) -> None:
    has_right = any(k in blob for k in ("右下角", "页脚右侧", "页底右侧")) or bool(
        re.search(r"页码.{0,8}右", blob)
    )
    has_center = any(k in blob for k in ("页底居中", "页脚居中", "底部居中", "页码居中"))
    if has_right and not has_center:
        fmt.pageNumberPos = "bottom-right"
        notes.append("页码右下角")
    elif has_center:
        fmt.pageNumberPos = "bottom-center"
        notes.append("页码页底居中")
    cover_unnumbered = any(k in blob for k in ("封面不编", "扉页不编"))
    start_body = any(k in blob for k in ("从正文起编", "正文起编"))
    start_toc = any(k in blob for k in ("从目录起编", "目录起编"))
    start_cover = any(k in blob for k in ("封面编页", "自封面", "从封面起编"))
    if cover_unnumbered:
        if start_body and not start_toc:
            fmt.pageNumberStart = "body"
            notes.append("封面/目录不编，从正文起编")
        else:
            fmt.pageNumberStart = "toc"
            notes.append("封面不编页码，从目录起编")
    elif start_body and not start_toc:
        fmt.pageNumberStart = "body"
        notes.append("封面/目录不编，从正文起编")
    elif start_toc:
        fmt.pageNumberStart = "toc"
        notes.append("封面不编页码，从目录起编")
    elif start_cover:
        fmt.pageNumberStart = "cover"
        notes.append("自封面连续编页")
    elif any(k in blob for k in ("连续编码", "不能跳号", "不得跳号", "不得缺页")):
        notes.append("页码连续编码")


def _set_pg_num_type(section, *, start: int | None) -> None:
    sp = section._sectPr
    for old in list(sp.findall(qn("w:pgNumType"))):
        sp.remove(old)
    if start is None:
        return
    pg = OxmlElement("w:pgNumType")
    pg.set(qn("w:start"), str(int(start)))
    sp.append(pg)


def _clear_runs(para) -> None:
    for child in list(para._p):
        if child.tag == qn("w:r"):
            para._p.remove(child)


def _add_page_field(para, *, font: str, size_pt: float) -> None:
    begin = para.add_run()
    fld = OxmlElement("w:fldChar")
    fld.set(qn("w:fldCharType"), "begin")
    begin._r.append(fld)
    _set_run_font(begin, font, size_pt)

    instr = para.add_run()
    text = OxmlElement("w:instrText")
    text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    text.text = " PAGE "
    instr._r.append(text)
    _set_run_font(instr, font, size_pt)

    sep = para.add_run()
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    sep._r.append(fld_sep)
    _set_run_font(sep, font, size_pt)

    cache = para.add_run("1")
    _set_run_font(cache, font, size_pt)

    end = para.add_run()
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    end._r.append(fld_end)
    _set_run_font(end, font, size_pt)


def _set_run_font(run, font: str, size_pt: float) -> None:
    run.font.size = Pt(size_pt)
    run.font.name = font
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:ascii"), font)
    rfonts.set(qn("w:hAnsi"), font)
    rfonts.set(qn("w:eastAsia"), font)
