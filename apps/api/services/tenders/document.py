"""克隆第五章空白 Word，按表单填空。LLM 只抽邀请书字段，不直接写 docx。"""

from __future__ import annotations

import io
import logging
import re
import shutil
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor, Twips
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

from api.services.tenders.assets import find_chapter5_template
from api.services.tenders.money import rmb_lowercase, rmb_uppercase
from api.services.tenders.quote import (
    SECOND_ROUND_YUAN,
    QuoteSheet,
    apply_traffic_note,
    is_template_sheet,
    money_text,
    qty_text,
    resolve_quote_sheet,
    scale_quote,
)
from api.services.tenders.commitment import append_commitment_letter
from api.services.tenders.placeholders import (
    append_placeholder_section,
    collect_slots,
    fill_perf_placeholders,
)
from api.services.tenders.slots import attachments_for_slots
from api.services.tenders.schema import BidBrief

logger = logging.getLogger("api.tenders")

_SONG = "宋体"

_TOC_ITEMS: tuple[tuple[str, str], ...] = (
    ("投标函及投标函附录", "2"),
    ("分项报价表", "4"),
    ("法定代表人身份证明", "5"),
    ("授权委托书", "6"),
    ("技术偏差表", "7"),
    ("企业业绩", "8"),
    ("原厂生产承诺", "10"),
    ("其他材料", "11"),
    ("投标承诺书", "12"),
)
_CN_NUM = "一二三四五六七八九"


def _compact(text: str) -> str:
    return re.sub(r"[\s/]+", "", text or "")


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


def _run_underlined(run) -> bool:
    rpr = run._element.find(qn("w:rPr"))
    return rpr is not None and rpr.find(qn("w:u")) is not None


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
    """签字空位等未填下划线：去掉加粗，避免比其它横线更粗。"""
    paras: list[Paragraph] = list(doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                paras.extend(cell.paragraphs)
    for para in paras:
        for run in _blank_runs(para):
            rpr = run._element.find(qn("w:rPr"))
            if rpr is None:
                continue
            _clear_bold(rpr)


def _put_on_line(run, text: str, *, para: Paragraph | None = None) -> None:
    """把文字写进原有下划线空白：跟相邻正文字体字号一致，不加粗、不放大。"""
    r = run._element
    ref = _neighbor_rpr(para, run) if para is not None else r.find(qn("w:rPr"))
    new_rpr = _clone_fill_rpr(ref, underline=True)
    old_rpr = r.find(qn("w:rPr"))
    if old_rpr is not None:
        r.remove(old_rpr)
    r.insert(0, new_rpr)
    for child in list(r):
        if child.tag in (qn("w:t"), qn("w:tab"), qn("w:br"), qn("w:cr")):
            r.remove(child)
    t = OxmlElement("w:t")
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = f" {(text or '').strip() or '　'} "
    r.append(t)
    r.append(OxmlElement("w:tab"))


def _fill_blanks(para: Paragraph, *values: str) -> int:
    blanks = _blank_runs(para)
    filled = 0
    for run, val in zip(blanks, values):
        if not (val or "").strip():
            continue
        _put_on_line(run, val, para=para)
        filled += 1
    return filled


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


def _write_cell(
    cell: _Cell,
    text: str,
    *,
    size: float = 10.5,
    bold: bool = False,
    underline: bool = True,
    center: bool = False,
    bottom_line: bool = False,
) -> None:
    """单元格填空：默认下划线、不加粗；表头等需加粗时显式传 bold=True。"""
    cell.text = ""
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    para = cell.paragraphs[0]
    _strip_para_bottom(para)
    para.paragraph_format.space_before = Pt(2)
    para.paragraph_format.space_after = Pt(2)
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER if center else WD_ALIGN_PARAGRAPH.LEFT
    lines = (text or "").split("\n")
    for i, line in enumerate(lines):
        if i:
            para.add_run().add_break()
        run = para.add_run(line)
        # 填空内容不加粗；仅表头等显式 bold=True 时加粗。字号不大于正文常用值。
        _font(run, size, bold=bool(bold))
        run.underline = bool(underline)
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


def _ymd(iso: str) -> tuple[str, str, str]:
    raw = (iso or "").strip()
    parts = raw.replace("/", "-").split("-")
    if len(parts) >= 3:
        return parts[0], parts[1].lstrip("0") or "1", parts[2].lstrip("0") or "1"
    return "　　", "　", "　"


def _date_line(iso: str) -> str:
    year, month, day = _ymd(iso)
    return f"{year}    年    {month}    月    {day}    日"


def _set_dot_tab(paragraph: Paragraph, pos_cm: float) -> None:
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


def _write_toc_line(para: Paragraph, index: int, title: str, page: str) -> None:
    """标题 …… 页码。去掉段落下划线，否则 WPS 会把点线画成实线。"""
    _strip_toc_numbering(para)
    _set_dot_tab(para, _toc_tab_pos_cm(para))
    _clear_runs(para)
    title_run = para.add_run(f"{_CN_NUM[index]}、{title}")
    _font(title_run, 10.5)
    _no_underline(title_run)
    tab_run = para.add_run()
    _set_dot_leader_font(tab_run)
    _no_underline(tab_run)
    tab_run.add_tab()
    page_run = para.add_run(page)
    _font(page_run, 10.5)
    _no_underline(page_run)


def _ensure_header(section, project_name: str) -> None:
    header = section.header
    header.is_linked_to_previous = False
    para = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
    if _compact(para.text):
        return
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title = (project_name or "").strip() or "投标文件"
    if len(title) > 28:
        title = title[:28] + "…"
    _clear_runs(para)
    run = para.add_run(f"{title}    投标文件")
    _font(run, 10.5)
    p_pr = para._p.get_or_add_pPr()
    p_bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "4")
    bottom.set(qn("w:color"), "4F4F4F")
    p_bdr.append(bottom)
    p_pr.append(p_bdr)


def _deviation_rows(brief: BidBrief) -> list[tuple[str, str, str, str]]:
    ac = (
        "7kW交流汽车充电桩：单相220V、50Hz、7kW、一体式单枪、IP≥IP55、噪声≤60dB、"
        "待机≤5W、工作温度-40℃至65℃、枪线≥5米、短路/急停/粘连/漏电保护、壁挂或立柱安装（不含立柱）、"
        "国家3C认证，含供货安装接线调试（不含上端电源电缆）。"
    )
    dc = (
        "30kW直流汽车充电桩：三相380V、50Hz、30kW、一体式单枪、IP≥IP55、噪声≤80dB、"
        "待机≤5W、工作温度-40℃至65℃、枪线≥5米、相应安全保护、壁挂或立柱安装（不含立柱）、"
        "国家3C认证，含供货安装调试（不含上端电源电缆）。"
    )
    billing = (
        "计费系统：微信/支付宝扫码支付；免费对接用户对公收费账户，质保期内免收流量费；"
        f"{brief.trafficFeeNote}；提现服务费≤1%；与设备同质保并免费升级维护；"
        "具备第三方智能化集成平台接口；计费系统随设备自带。"
    )
    slow = "慢充标准配套立柱（含供货安装调试，安装费含在综合单价内）"
    fast = "快充标准配套立柱（含供货安装调试，安装费含在综合单价内）"
    return [
        ("1", ac, ac, "无偏差"),
        ("2", dc, dc, "无偏差"),
        ("3", slow, "我司所投慢充立柱为慢充标准配套立柱，含供货、安装、调试，安装费已含在综合单价内。", "无偏差"),
        ("4", fast, "我司所投快充立柱为快充标准配套立柱，含供货、安装、调试，安装费已含在综合单价内。", "无偏差"),
        ("5", billing, billing, "无偏差"),
    ]


def _factory_addressee(brief: BidBrief) -> str:
    """致送对象：优先招标人，空则用项目名称（投标项目名称）。"""
    return (brief.tenderer or "").strip() or (brief.projectName or "").strip()


def _is_factory_body(n: str) -> bool:
    if not n:
        return False
    if any(mark in n for mark in ("见模", "充电设备生产厂商", "承担原厂责任", "联源热电")):
        return True
    if n.endswith("：") and any(k in n for k in ("公司", "局", "中心", "院")):
        return True
    return False


def _add_factory_run(para: Paragraph, text: str, *, underline: bool) -> None:
    if not text:
        return
    run = para.add_run(text)
    _style_fill_run(run, para, underline=underline)


def _write_factory_commitment(para: Paragraph, brief: BidBrief) -> None:
    """原厂承诺正文：致送单位随招标人/项目名称变化，仅填空处下划线。"""
    addressee = _factory_addressee(brief) or "　　　　"
    project = (brief.projectName or "").strip()
    align = para.alignment
    _clear_runs(para)
    _add_factory_run(para, addressee, underline=True)
    _add_factory_run(para, "：我单位 ", underline=False)
    _add_factory_run(para, brief.bidderName, underline=True)
    _add_factory_run(
        para,
        " 作为充电设备生产厂商，承诺具备加工生产条件，"
        "在人员、设备、资金等方面具备相应的供货能力，对",
        underline=False,
    )
    if project:
        _add_factory_run(para, f"「{project}」", underline=True)
    else:
        _add_factory_run(para, "本次", underline=False)
    _add_factory_run(para, "投标产品的质量、供货期、质保期及售后服务承担原厂责任。本项目供货期 ", underline=False)
    _add_factory_run(para, str(brief.deliveryDays), underline=True)
    _add_factory_run(para, " 日历天内，质保期 ", underline=False)
    _add_factory_run(para, str(brief.warrantyYears), underline=True)
    _add_factory_run(para, " 年。特此承诺。", underline=False)
    if align is not None:
        para.alignment = align


def _auth_text(brief: BidBrief) -> str:
    legal = (brief.legalPersonName or "").strip() or "　　　"
    agent = (brief.agentName or "").strip()
    if not agent:
        return (
            "本次投标文件由法定代表人直接签署、递交，不另行委托代理人。"
            "如需委托，请填写委托代理人姓名后重新生成。"
        )
    until = (brief.agentAuthUntil or "").strip() or "至本项目投标有效期届满"
    return (
        f"本人 {legal} （姓名）系 {brief.bidderName} （投标人名称）的法定代表人，"
        f"现委托 {agent} （姓名）为我方代理人。代理人根据授权，以我方名义签署、澄清、说明、"
        f"补正、递交、撤回、修改 {brief.projectName} 投标文件、签订合同和处理有关事宜，"
        f"其法律后果由我方承担。委托期限：{until}。代理人无转委托权。"
    )


def _fill_paragraphs(doc: Document, brief: BidBrief) -> Paragraph | None:
    price_cn = rmb_uppercase(brief.bidPriceYuan)
    price_en = rmb_lowercase(brief.bidPriceYuan)
    extra = (brief.extraNote or "").strip() or "（无）"
    year, month, day = _ymd(brief.bidDate)
    section = "cover"
    drop: list[Paragraph] = []
    quote_anchor: Paragraph | None = None
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
            if "备注" in n or "应编制" in n:
                continue
            for i, (title, page) in enumerate(_TOC_ITEMS):
                key = _compact(title)
                if n == key or n.endswith(key):
                    _write_toc_line(para, i, title, page)
                    break
            continue
        if n == "投标函及投标函附录" and heading:
            section = "letter"
            continue
        if n == "投标函附录":
            section = "appendix"
            continue
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
        if "近年完成的类似项目" in n or "正在供货和新承接" in n:
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
            elif n.startswith("投标人：") and "盖单位公章" in n:
                _remove_line_drawings(para)
                _fill_blanks(para, brief.bidderName)
            elif n == "年月日":
                _fill_blanks(para, year, month, day)
            continue

        if section == "letter":
            if "招标人名称" in n:
                _write_tenderer_line(para, brief.tenderer)
            elif "愿意以人民币投标总报价" in n or "我方已认真研究了" in n:
                _fill_blanks(
                    para,
                    brief.projectName,
                    price_cn,
                    price_en,
                    f"{brief.deliveryDays}天内",
                    brief.quality,
                )
                _scrub_hints(para, hints_project)
            elif "其他补充说明" in n:
                _remove_line_drawings(para)
                _ensure_blank_underline(para)
                _fill_blanks(para, extra)
                if not _blank_runs(para):
                    _write_para(para, extra, size=12)
            elif n.startswith("投标人") and "盖单位公章" in n:
                _remove_line_drawings(para)
                _ensure_blank_underline(para)
                _fill_blanks(para, brief.bidderName)
            elif "法定代表人或其委托代理人" in n and "地址" in n:
                _fill_blanks(para, "", (brief.bidderAddress or "").strip())
            elif n.startswith("地址"):
                _fill_blanks(para, (brief.bidderAddress or "").strip())
            elif n.startswith("网址"):
                _fill_blanks(para, (brief.bidderWebsite or "").strip())
            elif n.startswith("电话"):
                _fill_blanks(para, (brief.bidderPhone or "").strip())
            elif "传真" in n:
                _fill_blanks(
                    para,
                    (brief.bidderFax or "").strip(),
                    (brief.bidderPostcode or "").strip(),
                )
            elif n.startswith("日期") or n == "年月日":
                _fill_blanks(para, year, month, day)
            continue

        if section in {"appendix", "pay"}:
            if "盖单位公章" in n or (n.startswith("投标人") and "签字" in n):
                _remove_line_drawings(para)
                _ensure_blank_underline(para)
                if "年" in n and "月" in n:
                    _fill_blanks(para, brief.bidderName, "", year, month, day)
                else:
                    _fill_blanks(para, brief.bidderName)
            elif n == "年月日" or n.startswith("日期"):
                _fill_blanks(para, year, month, day)
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
                _fill_blanks(para, brief.bidderName)
            elif n == "年月日":
                _fill_blanks(para, year, month, day)
            continue

        if section == "auth":
            if n.startswith("本人") and "法定代表人" in n:
                agent = (brief.agentName or "").strip() or "（不委托）"
                _fill_blanks(
                    para,
                    (brief.legalPersonName or "").strip(),
                    brief.bidderName,
                    agent,
                    brief.projectName,
                )
                _scrub_hints(para, hints_project + hints_bidder + ("（姓名）",))
            elif n.startswith("委托期限"):
                until = (brief.agentAuthUntil or "").strip() or "至本项目投标有效期届满"
                _fill_blanks(para, until)
            elif n.startswith("投标人") and "法定代表人" in n:
                _fill_blanks(para, brief.bidderName, (brief.legalPersonName or "").strip())
            elif n.startswith("身份证号码") and "委托代理人" not in n:
                _fill_blanks(para, (brief.legalPersonIdNo or "").strip())
            elif "委托代理人" in n:
                _fill_blanks(
                    para,
                    (brief.agentName or "").strip(),
                    (brief.agentIdNo or "").strip(),
                )
            elif n == "年月日":
                _fill_blanks(para, year, month, day)
            continue

        if section == "factory":
            if _is_factory_body(n):
                _write_factory_commitment(para, brief)
            continue

        if section == "other":
            # 去掉模板里「本部分附企业介绍…」说明段，待补资料改用后文小标题+方框
            if n:
                drop.append(para)
            continue

    for para in drop:
        _drop_para(para)
    return quote_anchor


def _fill_tables(doc: Document, brief: BidBrief) -> None:
    price_cn = rmb_uppercase(brief.bidPriceYuan)
    price_en = rmb_lowercase(brief.bidPriceYuan)
    for table in doc.tables:
        kind = _table_kind(table)
        if kind == "appendix":
            _fill_appendix_table(table, brief, price_cn, price_en)
        elif kind == "pay":
            _fill_payment_table(table, brief)
        elif kind == "dev":
            _fill_deviation_table(table, brief)
        elif kind == "perf":
            fill_perf_placeholders(table)


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
            tc_pr = cell._tc.get_or_add_tcPr()
            for old in tc_pr.findall(qn("w:noWrap")):
                tc_pr.remove(old)


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
    """关闭拼写/语法红波浪线，避免中文技术用语被标红。"""
    root = doc.settings.element
    for tag in ("hideSpellingErrors", "hideGrammaticalErrors"):
        el_tag = qn(f"w:{tag}")
        for old in list(root.findall(el_tag)):
            root.remove(old)
        el = OxmlElement(f"w:{tag}")
        el.set(qn("w:val"), "true")
        root.append(el)


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


def _insert_table_after(doc: Document, para: Paragraph, rows: int, cols: int) -> Table:
    table = doc.add_table(rows=rows, cols=cols)
    para._p.addnext(table._tbl)
    return table


def _quote_warnings(sheet: QuoteSheet, target: Decimal, *, origin: str) -> list[str]:
    notes: list[str] = []
    n = len(sheet.lines)
    if origin == "template" or is_template_sheet(sheet):
        notes.append(
            f"未解析到本次工程量清单，分项暂用内置模板《{sheet.title}》共 {n} 项。"
            "请上传招标工程量 Excel/Word，或在页面改分项后再生成"
        )
        if target > 0 and target != SECOND_ROUND_YUAN:
            notes.append(
                f"二次报价函曾报 {money_text(SECOND_ROUND_YUAN)} 元，当前投标总价为 {money_text(target)} 元，请确认使用哪一档"
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


def _fill_quote_section(doc: Document, brief: BidBrief, anchor: Paragraph | None) -> list[str]:
    sheet, origin = resolve_quote_sheet(brief)
    target = Decimal(str(brief.bidPriceYuan or 0)).quantize(Decimal("0.01"))
    filled = scale_quote(sheet, target)
    notes = _quote_warnings(sheet, target, origin=origin)
    if anchor is None:
        notes.append("未找到「分项报价表」备注段，未能插入报价明细")
        return notes

    headers = ("序号", "设备", "技术参数要求", "单位", "数量", "不含税综合单价（元）", "合价")
    extra = 3
    table = _insert_table_after(doc, anchor, rows=1 + len(filled.lines) + extra, cols=7)
    _set_tbl_borders(table)
    table.autofit = False
    col_twips = _distribute_twips(_usable_width_twips(doc), (11, 20, 58, 10, 12, 26, 23))
    for i, title in enumerate(headers):
        _write_cell(table.rows[0].cells[i], title, size=9, bold=True, underline=False, center=True, bottom_line=False)
    for i, line in enumerate(filled.lines, start=1):
        spec = apply_traffic_note(line.spec, brief.trafficFeeNote)
        values = (
            line.seq,
            line.name,
            spec,
            line.unit,
            qty_text(line.qty),
            money_text(line.unit_price),
            money_text(line.amount),
        )
        for j, val in enumerate(values):
            center = j != 2
            _write_cell(
                table.rows[i].cells[j],
                val,
                size=8 if j == 2 else 9,
                bold=False,
                underline=False,
                center=center,
                bottom_line=False,
            )
    summaries = (
        ("不含税合计", money_text(filled.total_ex_tax)),
        ("税率", f"{(filled.tax_rate * 100).quantize(Decimal('1'))}%"),
        ("含税合计", money_text(filled.total_inc_tax)),
    )
    base = 1 + len(filled.lines)
    start_seq = len(filled.lines) + 1
    for offset, (label, value) in enumerate(summaries):
        row = table.rows[base + offset]
        _write_cell(row.cells[0], str(start_seq + offset), size=9, bold=True, underline=False, center=True, bottom_line=False)
        _write_cell(row.cells[1], label, size=9, bold=True, underline=False, center=False, bottom_line=False)
        for j in range(2, 6):
            _write_cell(row.cells[j], "", size=9, bold=False, underline=False, bottom_line=False)
        _write_cell(row.cells[6], value, size=9, bold=True, underline=False, center=True, bottom_line=False)
        row.cells[1].merge(row.cells[5])
    _apply_fixed_table_widths(table, col_twips)
    return notes


def _fill_appendix_table(table: Table, brief: BidBrief, price_cn: str, price_en: str) -> None:
    mapping = {
        "项目名称": brief.projectName,
        "投标人": brief.bidderName,
        "投标总报价": f"（大写）{price_cn}\n（小写）{price_en}元",
        "投标内容": brief.bidContent,
        "投标质量": brief.quality,
        "交货期": f"{brief.deliveryDays}日历天内",
        "投标工期": f"{brief.deliveryDays}日历天内",
        "质保期": f"基础要求{brief.warrantyYears}年",
        "投标有效期": f"{brief.bidValidityDays} 日历天（从投标截止之日算起）",
        "需要说明的问题": (brief.extraNote or "").strip() or "投标人自行填写。（可另加附页）",
    }
    for row in table.rows:
        label = _compact(row.cells[0].text)
        for key, value in mapping.items():
            if key in label:
                _write_cell(row.cells[-1], value)
                break


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
    data_rows = list(table.rows[1:])
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

    for i, row in enumerate(table.rows):
        for j, cell in enumerate(row.cells):
            _strip_cell_underline(cell)
            text = _compact(cell.text)
            horizontal = i == 0 or j in (0, 3) or _is_short_cell(text)
            _center_cell(cell, horizontal=horizontal)


def _append_pdf_pages(doc: Document, pdf_path: Path, *, max_pages: int = 40) -> int:
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

    count = min(len(pdf), max_pages)
    if count <= 0:
        return 0
    doc.add_page_break()
    para = doc.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = para.add_run("附件：企业资质文件扫描件")
    _font(run, 16, bold=True)
    inserted = 0
    for i in range(count):
        try:
            page = pdf[i]
            bitmap = page.render(scale=1.2)
            image = bitmap.to_pil().convert("RGB")
            buf = io.BytesIO()
            image.save(buf, format="JPEG", quality=70, optimize=True)
            buf.seek(0)
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


def build_bid_docx(
    brief: BidBrief,
    dest: Path,
    *,
    qualification_pdf: Path | None = None,
) -> tuple[Path, list[str]]:
    warnings: list[str] = []
    if brief.bidPriceYuan <= 0:
        warnings.append("投标总价为 0，请确认是否已填写报价")
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
    quote_anchor = _fill_paragraphs(doc, brief)
    _fill_tables(doc, brief)
    warnings.extend(_fill_quote_section(doc, brief, quote_anchor))
    _normalize_blank_underlines(doc)
    for i, section in enumerate(doc.sections):
        if i == 0:
            continue
        _ensure_header(section, brief.projectName)

    if brief.includeCommitment:
        append_commitment_letter(doc, brief)
        warnings.append("已附「投标承诺书」（附件五），请核对后签字盖章")

    if brief.includePlaceholders:
        slots = collect_slots(brief.extraPlaceholders)
        media = attachments_for_slots(slots)
        n_filled, n_boxes = append_placeholder_section(doc, slots, media)
        if n_filled:
            warnings.append(f"已将 {n_filled} 项已上传扫描件写入附件区")
        if n_boxes:
            warnings.append(f"另有 {n_boxes} 处仍为待补虚线框，可在页面补充后重新生成")

    pages = 0
    if brief.attachQualifications:
        if qualification_pdf and qualification_pdf.is_file():
            pages = _append_pdf_pages(doc, qualification_pdf)
            if pages == 0:
                warnings.append("资质 PDF 未能渲染为图片，Word 中未插入扫描件")
        else:
            warnings.append(
                "未找到资质 PDF。可将《伟泰科技资质文件最终版.pdf》复制到 "
                "storage/tender-assets/weitai-qualifications.pdf 后重新生成"
            )

    doc.save(str(dest))
    if pages:
        warnings.append(f"已插入资质文件 {pages} 页扫描件")
    return dest, warnings
