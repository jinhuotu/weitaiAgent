"""大纲组卷的已知类型模块：从 BidBrief 填空，不写承诺条款、不用 LLM。"""

from __future__ import annotations

import re

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from docx.text.paragraph import Paragraph

from api.services.tenders.categories import chapter_draft_text
from api.services.tenders.document import (
    _LETTER_SIGN_RIGHT_KW,
    _SIGN_OFF_KW,
    _add_bottom_sign_spacer,
    _apply_fixed_table_widths,
    _auth_text,
    _commercial_dev_rows,
    _embed_tech_drawings,
    _forbid_row_split,
    _glue_sign_off,
    _deviation_rows,
    _distribute_twips,
    _fill_quote_section,
    _form_run,
    _insert_table_after,
    _no_underline,
    _ordered_perf_lines,
    _perf_amount_text,
    _rewrite_date_line,
    _rewrite_labeled_underline,
    _set_tbl_borders,
    _set_word_wrap,
    _usable_width_twips,
    _write_cell,
    _write_factory_commitment,
    _ymd,
)
from api.services.tenders.money import rmb_figure, rmb_pair, rmb_uppercase
from api.services.tenders.outline import bid_is_works, compact_title, is_seal_register, letter_role
from api.services.tenders.placeholders import (
    _insert_slot_media,
    _set_row_height,
    draw_placeholder_box,
    id_slot,
    inline_id_scans,
)
from api.services.tenders.schema import BidBrief, OutlineItem, PerformanceLine, PlaceholderItem
from api.services.tenders.tables import resolve_dev_layout, resolve_quote_layout, width_ratios

_SONG = "宋体"

MODULE_KINDS = frozenset(
    {
        "letter",
        "legal_id",
        "auth",
        "quote",
        "biz_dev",
        "tech_dev",
        "performance",
        "factory",
        "tech_plan",
        "tech_chapter",
        "company",
    }
)
COPY_KINDS = frozenset({"commitment_copy", "company"})


def render_module(
    doc: Document,
    brief: BidBrief,
    item: OutlineItem,
    media: dict | None = None,
    inlined: set[str] | None = None,
) -> list[str]:
    kind = (item.kind or "").strip()
    handler = _HANDLERS.get(kind)
    if handler is None:
        return [f"「{item.title}」无对应填空模块"]
    if kind in {"legal_id", "auth"}:
        return handler(doc, brief, item, media=media, inlined=inlined)
    if kind in {"tech_plan", "tech_chapter"}:
        return handler(doc, brief, item, media=media)
    if kind == "performance":
        return handler(doc, brief, item, media=media)
    return handler(doc, brief, item)


def _letter(doc: Document, brief: BidBrief, item: OutlineItem) -> list[str]:
    if letter_role(item.title) == "appendix":
        return _letter_appendix(doc, brief)
    price_cn = rmb_uppercase(brief.bidPriceYuan)
    price_en = rmb_figure(brief.bidPriceYuan)
    tenderer = (brief.tenderer or "").strip() or "　　　　"
    project = (brief.projectName or "").strip() or "（项目名称）"
    extra = (brief.extraNote or "").strip() or "无"
    quality = (brief.quality or "").strip() or "合格"
    days = str(int(brief.deliveryDays))
    valid = str(int(brief.bidValidityDays))
    response = "响应" in (item.title or "")
    file_word = "采购文件" if response else "招标文件"
    price_lab = "响应总报价" if response else "投标总价"
    period_lab = "交货期" if response else "计划工期"
    tight = dict(space_after=2, line_spacing=1.08)
    _para_parts(doc, [("致：", False), (tenderer, True)], **tight)
    _para_parts(
        doc,
        [
            ("1. 我方已仔细研究了", False),
            (project, True),
            (f"{file_word}（含补遗书无）的全部内容，愿意以人民币（大写）：", False),
            (price_cn, True),
            (f"（小写：￥{price_en}元）的{price_lab}；{period_lab}：", False),
            (f"{days}日历天", True),
            ("；按合同约定实施和完成承包工程，修补工程中的任何缺陷，工程质量达到：", False)
            if not response
            else ("；按合同约定完成，质量要求：", False),
            (quality, True),
            ("。", False),
        ],
        indent=True,
        **tight,
    )
    _para(doc, "2. 我方承诺在投标有效期内不修改、撤销投标文件。", indent=True, **tight)
    if brief.bidBondYuan is not None:
        _para_parts(
            doc,
            [
                ("3. 随同本投标函提交投标保证金一份，金额人民币（大写）：", False),
                (rmb_uppercase(brief.bidBondYuan), True),
                (f"（小写￥{rmb_figure(brief.bidBondYuan)}元）。", False),
            ],
            indent=True,
            **tight,
        )
    else:
        _para(
            doc,
            "3. 随同本投标函提交投标保证金一份，金额人民币（大写）：　　　　元（小写￥　　　　元）。",
            indent=True,
            **tight,
        )
    _para(doc, "4. 如我方中标：", indent=True, **tight)
    _para(doc, "（1）我方承诺在收到中标通知书后，在中标通知书规定期限内与你方签订合同。", indent=True, **tight)
    _para(doc, "（2）随同本投标函递交的投标函附录属于合同文件的组成部分。", indent=True, **tight)
    _para(doc, "（3）我方承诺按照招标文件规定递交履约担保。", indent=True, **tight)
    _para(doc, "（4）我方承诺在合同约定期限内完成并移交全部合同工程。", indent=True, **tight)
    _para(
        doc,
        "5. 我方在此声明，所递交投标文件及全部资料完整、真实、准确，不存在投标人须知规定禁止投标情形。",
        indent=True,
        **tight,
    )
    _para_parts(
        doc,
        [
            ("6. 投标有效期：自投标截止之日起", False),
            (f"{valid}日历天", True),
            ("。", False),
        ],
        indent=True,
        **tight,
    )
    last = _para_parts(doc, [("7. 补充说明：", False), (extra, True), ("。", False)], indent=True, **tight)
    last.paragraph_format.keep_with_next = True
    _letter_sign_block(doc, brief)
    return []


def _letter_appendix(doc: Document, brief: BidBrief) -> list[str]:
    from api.services.tenders.outline import _clean_pm_name

    price_cn = rmb_uppercase(brief.bidPriceYuan)
    price_en = rmb_figure(brief.bidPriceYuan)
    compact = dict(line_spacing=1.15, space_before=2, space_after=1, line_em=12, keep=True)
    _id_field(doc, "项目名称：", (brief.projectName or "").strip(), **compact)
    _id_field(doc, "投标人：", (brief.bidderName or "").strip(), **compact)
    _id_field(doc, "投标范围：", (brief.bidContent or "").strip(), **compact)
    _id_field(doc, "投标总报价（大写）：", f"{price_cn}（小写：￥{price_en}元）", **compact)
    _id_field(
        doc,
        "安全生产措施费：",
        rmb_pair(brief.safetyFeeYuan) if brief.safetyFeeYuan is not None else "",
        **compact,
    )
    _id_field(
        doc,
        "暂列金额：",
        rmb_pair(brief.provisionalYuan) if brief.provisionalYuan is not None else "",
        **compact,
    )
    _id_field(
        doc,
        "专业工程暂估价：",
        rmb_pair(brief.primeCostYuan) if brief.primeCostYuan is not None else "",
        **compact,
    )
    pm = _clean_pm_name(brief.pmName or "")
    cert = (brief.pmCertNo or "").strip()
    if cert in {"/", "／", "无"}:
        cert = ""
    grade = (brief.pmGrade or "").strip()
    if grade in {"", "/", "／"}:
        grade = "无"
    _id_field(doc, "项目经理：", pm, **compact)
    _id_field(doc, "注册证号：", cert, **compact)
    valid = str(int(brief.bidValidityDays)) if brief.bidValidityDays else ""
    _id_field(doc, "投标有效期：", f"自投标截止之日起{valid}日历天" if valid else "", **compact)
    _id_field(doc, "缺陷责任期：", (brief.defectPeriod or "").strip(), **compact)
    _id_field(doc, "履约担保金额：", (brief.perfBond or "").strip(), **compact)
    _id_field(doc, "专业等级：", grade, **compact)
    _letter_sign_block(doc, brief)
    return []


_ID_FIELD_KW: dict = {
    "align": WD_ALIGN_PARAGRAPH.LEFT,
    "left_indent_cm": 1.5,
    "right_indent_cm": 0.4,
    "line_spacing": 1.75,
    "space_before": 6,
    "space_after": 4,
    "line_em": 20,
    "nowrap": True,
}


def _legal_id(
    doc: Document,
    brief: BidBrief,
    item: OutlineItem,
    media: dict | None = None,
    inlined: set[str] | None = None,
) -> list[str]:
    del item
    _id_field(doc, "投标人名称：", (brief.bidderName or "").strip())
    _id_field(doc, "单位性质：", (brief.bidderNature or "").strip())
    _id_field(
        doc,
        "地址：",
        (brief.bidderAddress or "").strip(),
        nowrap=False,
        line_spacing=2.0,
        space_before=10,
        space_after=10,
        line_em=28,
    )
    founded = (brief.foundedDate or "").strip()
    fy, fm, fd = _ymd(founded) if founded else ("", "", "")
    if fy and not fy.startswith("　") and any(ch.isdigit() for ch in fy):
        date_para = doc.add_paragraph()
        _rewrite_date_line(
            date_para,
            fy,
            fm,
            fd,
            **{**_ID_FIELD_KW, "label": "成立时间：", "line_em": 0},
        )
    else:
        _id_field(doc, "成立时间：", _founded_text(founded))
    _id_field(doc, "经营期限：", (brief.businessTerm or "").strip())
    _id_person_line(doc, brief)
    legal = (brief.legalPersonName or "").strip() or "　　　"
    bidder = (brief.bidderName or "").strip()
    rel = doc.add_paragraph()
    pf = rel.paragraph_format
    pf.first_line_indent = Cm(0.74)
    pf.line_spacing = 1.75
    pf.space_before = Pt(10)
    pf.space_after = Pt(8)
    _set_word_wrap(rel, enabled=False)
    _form_run(rel, "上述", underline=False)
    _form_run(rel, f" {legal} ", underline=True)
    _form_run(rel, "系", underline=False)
    _form_run(rel, f" {bidder} ", underline=True)
    _form_run(rel, "的法定代表人。", underline=False)
    _para(doc, "特此证明。")
    if not (brief.agentName or "").strip():
        _para(doc, "本次由法定代表人亲自投标，不附授权委托书。")
    attach = _para(doc, "附：法定代表人身份证明复印件或扫描件。")
    attach.paragraph_format.keep_with_next = True
    notes: list[str] = []
    n = inline_id_scans(
        doc,
        (media or {}).get("id_legal") if media else None,
        empty_slot=id_slot("id_legal"),
    )
    if inlined is not None:
        inlined.add("id_legal")
    if n:
        notes.append("法人身份证已写入身份证明页")
    _id_sign_block(doc, brief)
    return notes


def _id_field(doc: Document, label: str, value: str, **overrides) -> None:
    keep = bool(overrides.pop("keep", False))
    para = doc.add_paragraph()
    _rewrite_labeled_underline(para, [(label, value, "")], **{**_ID_FIELD_KW, **overrides})
    if keep:
        para.paragraph_format.keep_with_next = True


def _id_person_line(doc: Document, brief: BidBrief) -> None:
    para = doc.add_paragraph()
    pf = para.paragraph_format
    pf.left_indent = Cm(1.5)
    pf.right_indent = Cm(0.4)
    pf.line_spacing = 1.75
    pf.space_before = Pt(8)
    pf.space_after = Pt(8)
    pf.first_line_indent = Cm(0)
    _set_word_wrap(para, enabled=False)
    parts = (
        ("姓名：", (brief.legalPersonName or "").strip()),
        ("  性别：", (brief.legalPersonGender or "").strip()),
        ("  年龄：", (brief.legalPersonAge or "").strip()),
        ("  职务：", (brief.legalPersonTitle or "").strip()),
    )
    for lab, val in parts:
        _form_run(para, lab, underline=False)
        _form_run(para, f" {val or '　　'} ", underline=True)


def _auth(
    doc: Document,
    brief: BidBrief,
    item: OutlineItem,
    media: dict | None = None,
    inlined: set[str] | None = None,
) -> list[str]:
    del item
    notes: list[str] = []
    _para(doc, _auth_text(brief), indent=True).paragraph_format.keep_with_next = True
    has_agent = bool((brief.agentName or "").strip())
    if not has_agent:
        notes.append("授权委托书未填写委托代理人，已按法定代表人亲自签署处理")
        _auth_sign_block(doc, brief)
        return notes
    n = inline_id_scans(
        doc,
        (media or {}).get("id_agent") if media else None,
        empty_slot=id_slot("id_agent"),
    )
    if inlined is not None:
        inlined.add("id_agent")
    if n:
        notes.append("委托人身份证已写入授权委托书")
    _auth_sign_block(doc, brief)
    return notes


def _quote(doc: Document, brief: BidBrief, item: OutlineItem) -> list[str]:
    layout = resolve_quote_layout(brief, item)
    charger = any("技术参数" in (h or "") or (h or "").strip() == "设备" for h in layout.titles)
    if charger:
        anchor = _para(doc, "备注：详见下表。工程量与招标清单一致，综合单价按投标总价折算。")
    else:
        anchor = _para(doc, "")
    notes = _fill_quote_section(doc, brief, anchor, headers=layout.titles)
    titles = " / ".join(layout.titles)
    if "设备" not in layout.titles and "技术参数要求" not in layout.titles:
        notes.append(f"报价表表头已按招标书生成：{titles}")
    return notes


def _biz_dev(doc: Document, brief: BidBrief, item: OutlineItem) -> list[str]:
    _dev_table(doc, brief, item)
    return []


def _tech_dev(doc: Document, brief: BidBrief, item: OutlineItem) -> list[str]:
    _dev_table(doc, brief, item)
    return []


_PERF_PAGE_CAPS = ("合同首页", "合同金额页", "签字盖章页")


def _perf_files_for(line: PerformanceLine, media: dict | None) -> list:
    from api.services.tenders.performance import line_name_key

    if not isinstance(media, dict):
        return []
    key = f"perf:{line_name_key(line.projectName)}"
    files = media.get(key) or []
    return [p for p in files if getattr(p, "is_file", lambda: False)()]


def _write_perf_scans(doc: Document, line: PerformanceLine, media: dict | None) -> list[str]:
    files = _perf_files_for(line, media)
    name = (line.projectName or "").strip() or "类似业绩"
    _para(doc, f"{name} 合同扫描件（首页 / 金额页 / 签字页）", bold=True)
    inserted = 0
    if files:
        inserted = _insert_slot_media(doc, files[:3], max_pages=3)
    notes: list[str] = []
    missing = max(0, 3 - inserted)
    for i in range(3 - missing, 3):
        cap = _PERF_PAGE_CAPS[i]
        draw_placeholder_box(
            doc,
            PlaceholderItem(key="perf", title=f"{name} {cap}", hint="装订时附原件"),
        )
        notes.append(f"{name}缺{cap}")
    return notes


def _performance(
    doc: Document,
    brief: BidBrief,
    item: OutlineItem,
    media: dict | None = None,
) -> list[str]:
    del item
    lines = _ordered_perf_lines(brief)
    if not lines:
        return []
    notes: list[str] = []
    done = [line for line in lines if not line.ongoing]
    doing = [line for line in lines if line.ongoing]
    if done:
        _para(doc, "近年完成的类似项目", bold=True)
        for line in done:
            _perf_table(doc, line)
            notes.extend(_write_perf_scans(doc, line, media))
    if doing:
        _para(doc, "正在供货和新承接的项目", bold=True)
        for line in doing:
            _perf_table(doc, line)
            notes.extend(_write_perf_scans(doc, line, media))
    return notes


def _factory(doc: Document, brief: BidBrief, item: OutlineItem) -> list[str]:
    del item
    para = doc.add_paragraph()
    _write_factory_commitment(para, brief)
    _id_sign_block(doc, brief, unit="承诺单位（盖章）：")
    return []


def _company(doc: Document, brief: BidBrief, item: OutlineItem) -> list[str]:
    if is_seal_register(item):
        return _seal_register(doc, brief)
    n = compact_title(item.title)
    if "拟分包" in n:
        _para(doc, "【本项目不进行分包】")
        return []
    if "项目经理" in n and "承诺" in n:
        _para(
            doc,
            "我方拟派项目经理现阶段无在建工程施工项目。项目经理姓名以项目管理机构表为准。",
            indent=True,
        )
        _id_sign_block(doc, brief)
        return []
    _kv_table(
        doc,
        [
            ("投标人名称", (brief.bidderName or "").strip()),
            ("地址", (brief.bidderAddress or "").strip()),
            ("联系电话", (brief.bidderPhone or "").strip()),
            ("备注", ""),
        ],
    )
    return []


def _seal_register(doc: Document, brief: BidBrief) -> list[str]:
    name = (brief.bidderName or "").strip()
    phone = (brief.bidderPhone or "").strip()
    year, month, day = _ymd(brief.bidDate)
    date = f"{year}年{month}月{day}日" if year and not year.startswith("　") else ""
    anchor = doc.add_paragraph()
    table = _insert_table_after(doc, anchor, rows=10, cols=6)
    _set_tbl_borders(table)
    table.autofit = False
    _merge(table, 0, 0, 5)
    _merge(table, 1, 0, 5)
    _merge(table, 2, 0, 2)
    _merge(table, 2, 3, 5)
    _merge(table, 3, 0, 2)
    _merge(table, 3, 3, 5)
    _merge(table, 4, 0, 5)
    _merge(table, 5, 0, 5)
    _merge(table, 6, 0, 1)
    _merge(table, 6, 2, 3)
    _merge(table, 6, 4, 5)
    _merge(table, 7, 0, 1)
    _merge(table, 7, 2, 3)
    _merge(table, 7, 4, 5)
    _merge(table, 8, 0, 2)
    _merge(table, 8, 3, 5)
    _merge(table, 9, 0, 2)
    _merge(table, 9, 3, 5)
    _write_cell(table.cell(0, 0), f"{name}公司证件、印章备案表", size=14, bold=True, underline=False, center=True)
    _write_cell(table.cell(1, 0), f"日期：{date}", size=11, bold=False, underline=False, center=True)
    _write_cell(table.cell(2, 0), f"公司名称：{name}", size=10.5, bold=False, underline=False)
    _write_cell(table.cell(2, 3), f"公司电话：{phone}", size=10.5, bold=False, underline=False)
    _write_cell(table.cell(3, 0), "对公账户：", size=10.5, bold=False, underline=False)
    _write_cell(table.cell(3, 3), "行号：", size=10.5, bold=False, underline=False)
    _write_cell(table.cell(4, 0), "账号：", size=10.5, bold=False, underline=False)
    _write_cell(table.cell(5, 0), "", size=10.5, bold=False, underline=False, center=True)
    _set_row_height(table.rows[5], 0.7)
    _write_cell(table.cell(6, 0), "公章", size=11, bold=False, underline=False, center=True)
    _write_cell(table.cell(6, 2), "财务章", size=11, bold=False, underline=False, center=True)
    _write_cell(table.cell(6, 4), "合同章", size=11, bold=False, underline=False, center=True)
    for col in (0, 2, 4):
        _write_cell(table.cell(7, col), "印鉴备案", size=10.5, bold=False, underline=False, center=True)
    # 公章直径约 4.2cm，格子加高方便盖章；上面空行压矮，整表仍优先一页
    _set_row_height(table.rows[7], 5.8, exact=True)
    _write_cell(table.cell(8, 0), "法人签字样本：", size=10.5, bold=False, underline=False)
    _write_cell(table.cell(8, 3), "", size=10.5, bold=False, underline=False)
    _write_cell(table.cell(9, 0), "财务负责人签字样本：", size=10.5, bold=False, underline=False)
    _write_cell(table.cell(9, 3), "财务负责人电话：", size=10.5, bold=False, underline=False)
    _set_row_height(table.rows[8], 1.4)
    _set_row_height(table.rows[9], 1.4)
    note = _para(doc, "备注：本表中三处备案印鉴为红色章。")
    col_twips = _distribute_twips(_usable_width_twips(doc), (1, 1, 1, 1, 1, 1))
    _apply_fixed_table_widths(table, col_twips)
    _forbid_row_split(table)
    if note is not None:
        note.paragraph_format.keep_together = True
    return ["印鉴预留备案表已按招标书格子填写公司名称和电话，印章请盖原件"]


def _merge(table, row: int, c1: int, c2: int) -> None:
    table.cell(row, c1).merge(table.cell(row, c2))


_WORKS_PLAN = (
    "主要施工方案与技术措施",
    "质量管理体系与措施",
    "安全管理体系与措施",
    "文明施工、环境保护体系与施工现场治安措施",
    "工期保证措施",
    "拟投入资源配备计划",
    "施工进度计划与网络图",
    "施工总平面布置",
    "建造方式的创新应用实施方案",
    "新工艺、新技术、新设备、新材料及BIM应用",
    "施工现场信息化监控与数据管理",
    "风险管理措施",
    "招标文件规定的其他内容",
)


def _scrub_anon(text: str, brief: BidBrief) -> str:
    out = text or ""
    for name in (brief.bidderName, brief.legalPersonName, brief.agentName):
        n = (name or "").strip()
        if len(n) >= 2:
            out = out.replace(n, "")
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def _tech_plan(
    doc: Document, brief: BidBrief, item: OutlineItem, media: dict | None = None
) -> list[str]:
    body = _scrub_anon(chapter_draft_text(item, brief), brief)
    notes: list[str] = []
    if bid_is_works(brief) or "施工组织" in compact_title(item.title):
        _para(doc, "本部分按技术暗标编制，不出现投标人名称、徽标和人员姓名。", indent=True)
    for block in _split_blocks(body):
        _para(doc, block, indent=True)
    if _chapter_wants_drawings(item):
        title = _para(doc, "图纸", bold=True)
        notes.extend(_embed_tech_drawings(doc, title, media))
    if "【待响应】" in body or "【待补充】" in body:
        notes.insert(0, f"{item.title}尚未补全，已按待响应/待补充写入")
    return notes


def _chapter_wants_drawings(item: OutlineItem) -> bool:
    n = compact_title(item.title)
    return any(k in n for k in ("图纸", "方案", "实施", "布置", "组织设计"))


_HANDLERS = {
    "letter": _letter,
    "legal_id": _legal_id,
    "auth": _auth,
    "quote": _quote,
    "biz_dev": _biz_dev,
    "tech_dev": _tech_dev,
    "performance": _performance,
    "factory": _factory,
    "company": _company,
    "tech_plan": _tech_plan,
    "tech_chapter": _tech_plan,
}


def _dev_table(doc: Document, brief: BidBrief, item: OutlineItem) -> None:
    rows = _commercial_dev_rows(brief) if item.kind == "biz_dev" else _deviation_rows(brief)
    layout = resolve_dev_layout(brief, item)
    titles = list(layout.titles)
    roles = list(layout.roles)
    cols = len(titles)
    anchor = doc.add_paragraph()
    table = _insert_table_after(doc, anchor, rows=1 + len(rows), cols=cols)
    _set_tbl_borders(table)
    table.autofit = False
    col_twips = _distribute_twips(_usable_width_twips(doc), width_ratios(roles))
    for i, title in enumerate(titles):
        _write_cell(table.rows[0].cells[i], title, size=9, bold=True, underline=False, center=True, bottom_line=False)
    for i, data in enumerate(rows, start=1):
        mapped = {"seq": data[0], "requirement": data[1], "response": data[2], "deviation": data[3]}
        for j, role in enumerate(roles):
            val = mapped.get(role, "")
            center = role in {"seq", "deviation"}
            _write_cell(
                table.rows[i].cells[j],
                val,
                size=9,
                bold=(role == "seq"),
                underline=False,
                center=center,
                bottom_line=False,
            )
    _apply_fixed_table_widths(table, col_twips)


def _perf_table(doc: Document, line: PerformanceLine) -> None:
    amount = _perf_amount_text(float(line.amountYuan or 0))
    rows = [
        ("项目名称", (line.projectName or "").strip()),
        ("项目所在地", (line.location or "").strip()),
        ("买方名称", (line.client or "").strip()),
        ("买方联系人", (line.contact or "").strip()),
        ("合同价格", amount),
        ("规格型号", (line.spec or "").strip()),
        ("项目概况", (line.summary or "").strip()),
        ("备注", (line.note or "").strip()),
    ]
    _kv_table(doc, rows)


def _kv_table(doc: Document, rows: list[tuple[str, str]]) -> None:
    """身份证明 / 业绩表：格线即可，字下不再加横线，避免看起来像删除线。"""
    anchor = doc.add_paragraph()
    table = _insert_table_after(doc, anchor, rows=len(rows), cols=2)
    _set_tbl_borders(table)
    table.autofit = False
    col_twips = _distribute_twips(_usable_width_twips(doc), (22, 78))
    for i, (label, value) in enumerate(rows):
        _write_cell(table.rows[i].cells[0], label, size=10.5, bold=True, underline=False, center=True, bottom_line=False)
        _write_cell(table.rows[i].cells[1], value, size=10.5, bold=False, underline=False, center=False, bottom_line=False)
    _apply_fixed_table_widths(table, col_twips)


def _add_sign_line(doc: Document, rows: list[tuple[str, str, str]], kw: dict, **overrides) -> Paragraph:
    para = doc.add_paragraph()
    _rewrite_labeled_underline(para, rows, **{**kw, **overrides})
    return para


def _letter_sign_block(doc: Document, brief: BidBrief) -> None:
    spacer = _add_bottom_sign_spacer(doc, sign_cm=2.4)
    kw = _LETTER_SIGN_RIGHT_KW
    agent = (brief.agentName or "").strip()
    lines = [
        _add_sign_line(
            doc,
            [("投　标　人：", (brief.bidderName or "").strip(), "（盖单位公章）")],
            kw,
            space_before=16,
        ),
        _add_sign_line(
            doc,
            [("法定代表人或其委托代理人：", agent, "（签字或盖章）")],
            kw,
        ),
    ]
    year, month, day = _ymd(brief.bidDate)
    date_para = doc.add_paragraph()
    _rewrite_date_line(date_para, year, month, day, **{**kw, "label": "日　　期："})
    lines.append(date_para)
    _glue_sign_off(doc, spacer, lines)


def _auth_sign_block(doc: Document, brief: BidBrief) -> None:
    spacer = _add_bottom_sign_spacer(doc, sign_cm=6.6)
    kw = _SIGN_OFF_KW
    lines = [
        _add_sign_line(
            doc,
            [("投标人：", (brief.bidderName or "").strip(), "（盖单位公章）")],
            kw,
            space_before=4,
        ),
        _add_sign_line(
            doc,
            [("法定代表人：", "", "（签字）")],
            kw,
        ),
        _add_sign_line(
            doc,
            [("身份证号码：", (brief.legalPersonIdNo or "").strip(), "")],
            kw,
        ),
        _add_sign_line(
            doc,
            [("委托代理人：", "", "（签字）")],
            kw,
        ),
        _add_sign_line(
            doc,
            [("身份证号码：", (brief.agentIdNo or "").strip(), "")],
            kw,
        ),
    ]
    year, month, day = _ymd(brief.bidDate)
    date_para = doc.add_paragraph()
    _rewrite_date_line(date_para, year, month, day, **kw)
    lines.append(date_para)
    _glue_sign_off(doc, spacer, lines)


def _id_sign_block(doc: Document, brief: BidBrief, *, unit: str = "投标人：") -> None:
    spacer = _add_bottom_sign_spacer(doc, sign_cm=4.4)
    kw = _SIGN_OFF_KW
    suffix = "" if unit.startswith("承诺") else "（盖单位公章）"
    lines = [
        _add_sign_line(
            doc,
            [(unit, (brief.bidderName or "").strip(), suffix)],
            kw,
            space_before=4,
        ),
        _add_sign_line(
            doc,
            [("法定代表人：", "", "（签字）")],
            kw,
        ),
    ]
    year, month, day = _ymd(brief.bidDate)
    date_para = doc.add_paragraph()
    _rewrite_date_line(date_para, year, month, day, **kw)
    lines.append(date_para)
    _glue_sign_off(doc, spacer, lines)


def _founded_text(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    year, month, day = _ymd(text)
    if year and not year.startswith("　") and any(ch.isdigit() for ch in year):
        if "年" in text and not text.replace("/", "-").count("-") >= 2:
            return text
        return f"{year}年{month}月{day}日"
    return text


def _split_blocks(text: str) -> list[str]:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []
    parts = [p.strip() for p in raw.split("\n") if p.strip()]
    return parts or [raw]


def _para(
    doc: Document,
    text: str,
    *,
    size: float = 12,
    bold: bool = False,
    indent: bool = False,
    space_after: float = 6,
    line_spacing: float = 1.5,
) -> Paragraph:
    return _para_parts(
        doc,
        [(text, False)],
        size=size,
        bold=bold,
        indent=indent,
        space_after=space_after,
        line_spacing=line_spacing,
    )


def _para_parts(
    doc: Document,
    parts: list[tuple[str, bool]],
    *,
    size: float = 12,
    bold: bool = False,
    indent: bool = False,
    space_after: float = 6,
    line_spacing: float = 1.5,
) -> Paragraph:
    para = doc.add_paragraph()
    pf = para.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(space_after)
    pf.line_spacing = line_spacing
    if indent:
        pf.first_line_indent = Cm(0.74)
    for text, under in parts:
        _run(para, text, size=size, bold=bold, underline=under)
    return para


def _run(
    para: Paragraph,
    text: str,
    *,
    size: float = 12,
    bold: bool = False,
    underline: bool = False,
) -> None:
    run = para.add_run(text or "")
    run.bold = bold
    run.font.size = Pt(size)
    run.font.name = _SONG
    run.font.color.rgb = RGBColor(0, 0, 0)
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:ascii"), _SONG)
    rfonts.set(qn("w:hAnsi"), _SONG)
    rfonts.set(qn("w:eastAsia"), _SONG)
    proof = OxmlElement("w:noProof")
    rpr.append(proof)
    if underline:
        for uel in list(rpr.findall(qn("w:u"))):
            rpr.remove(uel)
        uel = OxmlElement("w:u")
        uel.set(qn("w:val"), "single")
        rpr.append(uel)
        run.underline = True
    else:
        _no_underline(run)
