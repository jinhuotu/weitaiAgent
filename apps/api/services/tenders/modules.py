"""大纲组卷的已知类型模块：从 BidBrief 填空，不写承诺条款、不用 LLM。"""

from __future__ import annotations

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from docx.text.paragraph import Paragraph

from api.services.tenders.categories import tech_plan_body
from api.services.tenders.document import (
    _LETTER_ADDR_KW,
    _LETTER_CONTACT_KW,
    _LETTER_FIELD_KW,
    _SIGN_OFF_KW,
    _add_bottom_sign_spacer,
    _apply_fixed_table_widths,
    _auth_text,
    _commercial_dev_rows,
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
from api.services.tenders.money import rmb_lowercase, rmb_uppercase
from api.services.tenders.outline import is_seal_register
from api.services.tenders.placeholders import (
    TECH_DRAWING_SLOT,
    _set_row_height,
    draw_placeholder_box,
    id_slot,
    inline_id_scans,
)
from api.services.tenders.schema import BidBrief, OutlineItem, PerformanceLine
from api.services.tenders.tables import resolve_dev_layout, resolve_quote_layout, width_ratios

_SONG = "宋体"

MODULE_KINDS = frozenset(
    {"letter", "legal_id", "auth", "quote", "biz_dev", "tech_dev", "performance", "factory", "tech_plan", "company"}
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
    return handler(doc, brief, item)


def _letter(doc: Document, brief: BidBrief, item: OutlineItem) -> list[str]:
    response = "响应" in (item.title or "")
    file_word = "采购文件" if response else "招标文件"
    act = "响应" if response else "投标"
    price_cn = rmb_uppercase(brief.bidPriceYuan)
    price_en = rmb_lowercase(brief.bidPriceYuan)
    tenderer = (brief.tenderer or "").strip() or "　　　　"
    project = (brief.projectName or "").strip() or "（项目名称）"
    extra = (brief.extraNote or "").strip() or "（无）"
    quality = (brief.quality or "").strip() or "合格"
    _para_parts(doc, [("致：", False), (tenderer, True)])
    _para_parts(
        doc,
        [
            ("我方已认真研究了", False),
            (project, True),
            (f"{file_word}的全部内容，愿意以人民币{act}总报价（大写）", False),
            (price_cn, True),
            ("（小写）", False),
            (price_en, True),
            (f"元参加{act}。", False),
        ],
        indent=True,
    )
    _para_parts(
        doc,
        [
            ("交货期：", False),
            (f"{brief.deliveryDays}天内", True),
            ("。质量要求：", False),
            (quality, True),
            (f"。{act}有效期：", False),
            (f"{brief.bidValidityDays}日历天", True),
            (f"（从{act}截止之日算起）。", False),
        ],
        indent=True,
    )
    _para_parts(doc, [("其他补充说明：", False), (extra, True)], indent=True)
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
    para = doc.add_paragraph()
    _rewrite_labeled_underline(para, [(label, value, "")], **{**_ID_FIELD_KW, **overrides})


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
    anchor = _para(doc, "备注：详见下表。工程量与招标清单一致，综合单价按投标总价折算。")
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


def _performance(doc: Document, brief: BidBrief, item: OutlineItem) -> list[str]:
    del item
    lines = _ordered_perf_lines(brief)
    if not lines:
        _para(doc, "【待补】请在页面填写类似业绩后再生成。")
        return ["类似业绩为空，业绩页仅保留标题说明"]
    done = [line for line in lines if not line.ongoing]
    doing = [line for line in lines if line.ongoing]
    if done:
        _para(doc, "近年完成的类似项目", bold=True)
        for line in done:
            _perf_table(doc, line)
    if doing:
        _para(doc, "正在供货和新承接的项目", bold=True)
        for line in doing:
            _perf_table(doc, line)
    return []


def _factory(doc: Document, brief: BidBrief, item: OutlineItem) -> list[str]:
    del item
    para = doc.add_paragraph()
    _write_factory_commitment(para, brief)
    _id_sign_block(doc, brief, unit="承诺单位（盖章）：")
    return []


def _company(doc: Document, brief: BidBrief, item: OutlineItem) -> list[str]:
    if is_seal_register(item):
        return _seal_register(doc, brief)
    _para(doc, "【待补】请按招标书本页空白稿填写。")
    return [f"「{item.title}」招标书格式页未抽到正文"]


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
    _set_row_height(table.rows[5], 2.4)
    _write_cell(table.cell(6, 0), "公章", size=11, bold=False, underline=False, center=True)
    _write_cell(table.cell(6, 2), "财务章", size=11, bold=False, underline=False, center=True)
    _write_cell(table.cell(6, 4), "合同章", size=11, bold=False, underline=False, center=True)
    for col in (0, 2, 4):
        _write_cell(table.cell(7, col), "印鉴备案", size=10.5, bold=False, underline=False, center=True)
    _set_row_height(table.rows[7], 3.6)
    _write_cell(table.cell(8, 0), "法人签字样本：", size=10.5, bold=False, underline=False)
    _write_cell(table.cell(8, 3), "", size=10.5, bold=False, underline=False)
    _write_cell(table.cell(9, 0), "财务负责人签字样本：", size=10.5, bold=False, underline=False)
    _write_cell(table.cell(9, 3), "财务负责人电话：", size=10.5, bold=False, underline=False)
    _set_row_height(table.rows[8], 1.5)
    _set_row_height(table.rows[9], 1.5)
    _para(doc, "备注：本表中三处备案印鉴为红色章。")
    col_twips = _distribute_twips(_usable_width_twips(doc), (1, 1, 1, 1, 1, 1))
    _apply_fixed_table_widths(table, col_twips)
    return ["印鉴预留备案表已按招标书格子填写公司名称和电话，印章请盖原件"]


def _merge(table, row: int, c1: int, c2: int) -> None:
    table.cell(row, c1).merge(table.cell(row, c2))


def _tech_plan(doc: Document, brief: BidBrief, item: OutlineItem) -> list[str]:
    del item
    body = tech_plan_body(brief)
    for block in _split_blocks(body):
        _para(doc, block, indent=True)
    _para(doc, "图纸", bold=True)
    draw_placeholder_box(doc, TECH_DRAWING_SLOT)
    notes: list[str] = []
    if "【待响应】" in body:
        notes.append("实施方案文字未写，已插入待响应说明")
    notes.append("实施方案图纸用虚线框占位，装订时另附原件")
    return notes


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
    spacer = _add_bottom_sign_spacer(doc, sign_cm=7.4)
    kw = {**_LETTER_CONTACT_KW, "nowrap": True}
    agent = (brief.agentName or "").strip()
    lines = [
        _add_sign_line(
            doc,
            [("投　标　人：", (brief.bidderName or "").strip(), "（盖单位公章）")],
            kw,
            space_before=4,
        ),
        _add_sign_line(
            doc,
            [("法定代表人或其委托代理人：", agent, "（签字或盖章）")],
            kw,
        ),
        _add_sign_line(
            doc,
            [("地　址：", (brief.bidderAddress or "").strip(), "")],
            _LETTER_ADDR_KW,
        ),
    ]
    website = (brief.bidderWebsite or "").strip()
    if website:
        lines.append(_add_sign_line(doc, [("网　　址：", website, "")], _LETTER_FIELD_KW))
    lines.append(_add_sign_line(doc, [("电　　话：", (brief.bidderPhone or "").strip(), "")], _LETTER_FIELD_KW))
    fax = (brief.bidderFax or "").strip()
    if fax:
        lines.append(_add_sign_line(doc, [("传　　真：", fax, "")], _LETTER_FIELD_KW))
    post = (brief.bidderPostcode or "").strip()
    if post:
        lines.append(_add_sign_line(doc, [("邮政编码：", post, "")], _LETTER_FIELD_KW))
    year, month, day = _ymd(brief.bidDate)
    date_para = doc.add_paragraph()
    _rewrite_date_line(date_para, year, month, day, **{**_LETTER_FIELD_KW, "label": "日　　期："})
    lines.append(date_para)
    _glue_sign_off(doc, spacer, lines)


def _auth_sign_block(doc: Document, brief: BidBrief) -> None:
    spacer = _add_bottom_sign_spacer(doc, sign_cm=6.6)
    kw = _SIGN_OFF_KW
    agent = (brief.agentName or "").strip() or "（不委托）"
    lines = [
        _add_sign_line(
            doc,
            [("投标人：", (brief.bidderName or "").strip(), "（盖单位公章）")],
            kw,
            space_before=4,
        ),
        _add_sign_line(
            doc,
            [("法定代表人：", (brief.legalPersonName or "").strip(), "（签字）")],
            kw,
        ),
        _add_sign_line(
            doc,
            [("身份证号码：", (brief.legalPersonIdNo or "").strip(), "")],
            kw,
        ),
        _add_sign_line(
            doc,
            [("委托代理人：", agent, "（签字）")],
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
            [("法定代表人：", (brief.legalPersonName or "").strip(), "（签字）")],
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
) -> Paragraph:
    return _para_parts(doc, [(text, False)], size=size, bold=bold, indent=indent)


def _para_parts(
    doc: Document,
    parts: list[tuple[str, bool]],
    *,
    size: float = 12,
    bold: bool = False,
    indent: bool = False,
) -> Paragraph:
    para = doc.add_paragraph()
    pf = para.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(6)
    pf.line_spacing = 1.5
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
