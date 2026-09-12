"""二期：大纲组卷复制招标书原文；三期：已知类型用模块填写 BidBrief。"""

from __future__ import annotations

from docx.oxml.ns import qn

from api.services.tenders.outline import (
    choose_layout_mode,
    compact_title,
    extract_outline,
    fill_copy_blanks,
    looks_like_form_template,
)
from api.services.tenders.schema import (
    BidBrief,
    DeviationLine,
    OutlineItem,
    PerformanceLine,
    QuoteLineIn,
    default_brief,
)


def _docx_text(doc) -> str:
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
    return "\n".join(parts)


def test_ymd_pads_single_digit_month_day() -> None:
    from api.services.tenders.document import _two_digit_md, _ymd

    assert _two_digit_md("9") == "09"
    assert _two_digit_md("09") == "09"
    assert _two_digit_md("11") == "11"
    assert _ymd("2026-9-1") == ("2026", "09", "01")
    assert _ymd("2026-09-11") == ("2026", "09", "11")


def test_align_sign_label_spreads_short_words() -> None:
    from api.services.tenders.document import _align_sign_label

    assert _align_sign_label("投标人：") == "投　标　人："
    assert _align_sign_label("日期") == "日　　　期："
    assert _align_sign_label("法定代表人：") == "法定代表人："


def test_attach_template_body_not_toc_line() -> None:
    text = """
第五章 响应文件格式
响应函
知识产权不侵权承诺函
报价表
附件 响应函
致：（招标人名称）
我方参加（项目名称）的采购。
知识产权不侵权承诺函
我方承诺不侵犯任何第三方知识产权。
投标人名称：________
年  月  日
报价表
系统名称及报价
第六章 合同条款
"""
    chapter, items = extract_outline(text)
    assert "响应文件格式" in chapter
    by_title = {item.title: item for item in items}
    letter = by_title["响应函"]
    commit = by_title["知识产权不侵权承诺函"]
    assert "我方参加" in letter.body
    assert "不侵犯任何第三方知识产权" in commit.body
    assert "系统名称及报价" not in commit.body


def test_fill_copy_blanks_only_placeholders() -> None:
    raw = "致：（招标人名称）\n我方承诺不侵犯任何第三方知识产权。\n投标人名称：________\n年  月  日"
    filled = fill_copy_blanks(
        raw,
        bidder="河南伟泰光电科技有限公司",
        project="数智制造项目",
        tenderer="某市分公司",
        legal="郭志伟",
        bid_date="2026-09-08",
    )
    assert "某市分公司" in filled
    assert "河南伟泰光电科技有限公司" in filled
    assert "2026年09月08日" in filled
    assert "不侵犯任何第三方知识产权" in filled
    assert "（招标人名称）" not in filled


def test_fill_copy_blanks_leaves_sign_name_empty() -> None:
    raw = "法定代表人（签字）：________\n授权代表签字：________"
    filled = fill_copy_blanks(
        raw,
        bidder="河南伟泰光电科技有限公司",
        project="数智制造项目",
        tenderer="某市分公司",
        legal="郭志伟",
        bid_date="2026-09-08",
    )
    assert "郭志伟" not in filled
    assert "________" in filled


def test_fill_copy_blanks_underscores_and_days() -> None:
    raw = (
        "________________有限公司：\n"
        "我方已仔细阅读和研究了________________招标文件。\n"
        "中标通知起_______天内交货。自开标之日起_______天内有效。\n"
        "联系人：________    联系电话：________\n"
        "年  月  日"
    )
    filled = fill_copy_blanks(
        raw,
        bidder="河南伟泰光电科技有限公司",
        project="厂内充电桩采购",
        tenderer="某热电有限公司",
        legal="郭志伟",
        bid_date="2026-09-09",
        phone="17630567052",
        contact="郭志伟",
        delivery_days=30,
        validity_days=60,
    )
    assert filled.startswith("某热电有限公司：")
    assert "阅读和研究了厂内充电桩采购招标文件" in filled
    assert "通知起30天" in filled
    assert "开标之日起60天" in filled
    assert "17630567052" in filled
    assert "2026年09月09日" in filled


def test_looks_like_form_template() -> None:
    short = "致：（招标人名称）\n我方参加（项目名称）。"
    assert looks_like_form_template(short) is False
    form = (
        "附件一：\n"
        "投 标 函\n"
        "________________有限公司：\n"
        "我方已仔细阅读和研究了________________招标文件，决定参加投标。\n"
        "投标人（章）：          法定代表人或授权代表（签字）：\n"
        "年  月  日\n"
    )
    assert looks_like_form_template(form) is True
    mashed = (
        "第四部分投标文件格式附件一：投标函________________有限公司："
        "我方已仔细阅读和研究了________________招标文件，决定参加投标。"
        "投标人（章）：年  月  日"
    )
    assert looks_like_form_template(mashed) is True


def test_choose_layout_mode_response_vs_classic() -> None:
    _chapter, telecom = extract_outline(
        """
第五章 响应文件格式
响应函
承诺函
商务条款偏离表
技术规范书偏离表
知识产权不侵权承诺函
第六章 合同
"""
    )
    assert choose_layout_mode("第五章响应文件格式", telecom) == "outline"
    _c2, classic = extract_outline(
        """
第四部分 投标文件格式
附件一：投标函
附件二：法定代表人身份证明
附件三：授权委托书
附件四：分项报价表
附件五：投标承诺书
第五部分 评标办法
"""
    )
    assert choose_layout_mode("第四部分投标文件格式", classic) == "chapter5"
    _c3, long_pack = extract_outline(
        """
第四部分 投标文件格式
投标函
竞标书
法定代表人身份证明
法定代表人授权委托书
投标承诺书
投标保证金
印鉴预留备案表
投标报价单
商务偏离表
第五部分 评标办法
"""
    )
    assert choose_layout_mode("第四部分投标文件格式", long_pack) == "outline"


def test_assemble_follows_outline_item_order(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    brief = default_brief()
    brief.layoutMode = "outline"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(id="o01", title="授权委托书", kind="auth", source="generate"),
        OutlineItem(id="o02", title="法定代表人身份证明", kind="legal_id", source="generate"),
    ]
    dest = tmp_path / "order.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    paras = [p.text.strip() for p in Document(str(dest)).paragraphs if p.text.strip()]
    toc_auth = next(t for t in paras if t.startswith("一、授权委托书"))
    toc_legal = next(t for t in paras if t.startswith("二、法定代表人身份证明"))
    assert paras.index(toc_auth) < paras.index(toc_legal)
    assert paras.index("授权委托书") < paras.index("法定代表人身份证明")


def test_assemble_letter_keeps_invitation_form_layout(tmp_path) -> None:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    from api.services.tenders.assemble import assemble_bid_docx

    body = (
        "附件一：\n"
        "投 标 函\n"
        "________________有限公司：\n"
        "我方已仔细阅读和研究了________________招标文件，决定参加投标。\n"
        "中标通知起_______天内交货。自开标之日起_______天内有效。\n"
        "投标人（章）：          法定代表人或授权代表（签字）：\n"
        "联系人：________    联系电话：________\n"
        "年  月  日\n"
    )
    brief = default_brief()
    brief.layoutMode = "outline"
    brief.projectName = "厂内新能源充电桩采购项目"
    brief.tenderer = "某热电有限公司"
    brief.bidDate = "2026-09-09"
    brief.deliveryDays = 30
    brief.bidValidityDays = 60
    brief.bidderPhone = "17630567052"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(
            id="o01",
            title="投标函（附件一）",
            kind="letter",
            source="generate",
            body=body,
        )
    ]
    dest = tmp_path / "letter-form.docx"
    path, warnings = assemble_bid_docx(brief, dest, qualification_pdf=None)
    doc = Document(str(path))
    text = _docx_text(doc)
    assert "响应总报价" not in text
    assert "附件一" in text
    assert "投 标 函" in text
    assert "某热电有限公司" in text
    assert "厂内新能源充电桩采购项目" in text
    assert "通知起30天" in text
    assert "开标之日起60天" in text
    assert "17630567052" in text
    assert any("复制" in w for w in warnings)
    paras = [p for p in doc.paragraphs if p.text.strip()]
    title = next(p for p in paras if compact_title(p.text) == "投标函")
    assert title.alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert title.runs and title.runs[0].bold and title.runs[0].font.size >= Pt(16)
    salute = next(p for p in paras if p.text.strip().startswith("某热电有限公司"))
    assert salute.alignment == WD_ALIGN_PARAGRAPH.LEFT
    assert not salute.paragraph_format.first_line_indent
    body_p = next(p for p in paras if "阅读和研究了" in p.text)
    assert body_p.paragraph_format.first_line_indent and body_p.paragraph_format.first_line_indent > 0
    assert any("投标人（章）" in (c.text or "") for t in doc.tables for c in t.rows[0].cells)


def test_assemble_letter_without_body_still_writes_module(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    brief = default_brief()
    brief.layoutMode = "outline"
    brief.tenderer = "某热电有限公司"
    brief.bidPriceYuan = 518800
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(id="o01", title="投标函（附件一）", kind="letter", source="generate"),
        OutlineItem(id="o02", title="法定代表人授权委托书（附件四）", kind="auth", source="generate"),
    ]
    dest = tmp_path / "module-fallback.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    text = _docx_text(Document(str(dest)))
    assert "某热电有限公司" in text
    assert "518,800.00" in text or "伍拾壹万" in text or "投标总报价" in text or "响应总报价" in text
    assert "授权" in text or "委托" in text


def test_assemble_mashed_invitation_letter_not_dropped(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    body = (
        "第四部分投标文件格式附件一：投 标 函"
        "________________有限公司："
        "我方已仔细阅读和研究了________________招标文件，决定参加本次投标。"
        "中标通知起_______天内交货。自开标之日起_______天内有效。"
        "投标人（章）：          法定代表人或授权代表（签字）："
        "年  月  日"
    )
    brief = default_brief()
    brief.layoutMode = "outline"
    brief.projectName = "厂内新能源充电桩采购项目"
    brief.tenderer = "某热电有限公司"
    brief.bidDate = "2026-09-09"
    brief.deliveryDays = 30
    brief.bidValidityDays = 60
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(
            id="o01",
            title="投标函（附件一）",
            kind="letter",
            source="generate",
            body=body,
        )
    ]
    dest = tmp_path / "mashed-letter.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    text = _docx_text(Document(str(dest)))
    assert "阅读和研究了" in text
    assert "厂内新能源充电桩采购项目" in text
    assert "招标书格式章未提供本页空白稿" not in text


def test_assemble_copies_commitment_not_charger_clauses(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx
    from api.services.tenders.commitment import _CLAUSES

    brief = default_brief()
    brief.layoutMode = "outline"
    brief.projectName = "数智制造管理系统"
    brief.tenderer = "某市分公司"
    brief.bidDate = "2026-09-08"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineChapter = "第五章响应文件格式"
    brief.outlineItems = [
        OutlineItem(
            id="o01",
            title="响应函",
            kind="letter",
            source="generate",
            body="致：（招标人名称）\n我方参加（项目名称）。",
        ),
        OutlineItem(
            id="o02",
            title="知识产权不侵权承诺函",
            kind="commitment_copy",
            source="copy",
            body="我方承诺不侵犯任何第三方知识产权。\n投标人名称：________\n年  月  日",
        ),
        OutlineItem(id="o03", title="报价表", kind="quote", source="generate", skipped=True),
    ]
    dest = tmp_path / "outline.docx"
    path, warnings = assemble_bid_docx(brief, dest, qualification_pdf=None)
    assert path.is_file()
    doc = Document(str(path))
    text = _docx_text(doc)
    assert "目录" in text
    assert "响应函" in text
    assert "知识产权不侵权承诺函" in text
    assert "某市分公司" in text
    assert "数智制造管理系统" in text
    assert "不侵犯任何第三方知识产权" in text
    assert "河南伟泰光电科技有限公司" in text
    assert "2026年09月08日" in text
    assert "报价表" not in text
    assert "我方参加" not in text
    assert "响应总报价" in text
    assert not any(clause in text for clause in _CLAUSES)
    assert any("复制" in w for w in warnings)
    assert any("模块填写" in w for w in warnings)


def test_assemble_modules_fill_brief_not_invitation_body(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx
    from api.services.tenders.commitment import _CLAUSES

    brief = default_brief()
    brief.layoutMode = "outline"
    brief.projectName = "数智制造管理系统"
    brief.tenderer = "某市分公司"
    brief.bidDate = "2026-09-08"
    brief.bidPriceYuan = 113000
    brief.deliveryDays = 45
    brief.bidValidityDays = 120
    brief.agentName = "李四"
    brief.agentIdNo = "410000199001011234"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.quoteLines = [
        QuoteLineIn(seq="1", name="子系统模块A", spec="按采购需求", unit="套", qty=1, unitPrice=100000, amount=100000)
    ]
    brief.deviationLines = [
        DeviationLine(seq="1", requirement="支持单点登录", response="支持单点登录", deviation="无偏差")
    ]
    brief.performanceLines = [
        PerformanceLine(
            projectName="某园区信息化项目",
            client="某园区管委会",
            amountYuan=200000,
            summary="已竣工",
        )
    ]
    brief.outlineItems = [
        OutlineItem(id="o01", title="响应函", kind="letter", source="generate"),
        OutlineItem(id="o02", title="法定代表人身份证明", kind="legal_id", source="generate"),
        OutlineItem(
            id="o03",
            title="授权委托书",
            kind="auth",
            source="generate",
        ),
        OutlineItem(id="o04", title="报价表", kind="quote", source="generate"),
        OutlineItem(id="o05", title="技术规范书偏离表", kind="tech_dev", source="generate"),
        OutlineItem(
            id="o06",
            title="知识产权不侵权承诺函",
            kind="commitment_copy",
            source="copy",
            body="我方承诺不侵犯任何第三方知识产权。",
        ),
        OutlineItem(id="o07", title="类似项目业绩", kind="performance", source="generate"),
        OutlineItem(id="o08", title="分项报价表", kind="quote", source="generate", skipped=True),
    ]
    dest = tmp_path / "modules.docx"
    path, warnings = assemble_bid_docx(brief, dest, qualification_pdf=None)
    assert path.is_file()
    doc = Document(str(path))
    text = _docx_text(doc)
    assert "致：某市分公司" in text
    assert "响应总报价" in text
    assert "120日历天" in text
    assert "410521199802104053" in text
    assert "李四" in text
    assert "子系统模块A" in text
    quote_header = next(
        [c.text for c in t.rows[0].cells]
        for t in doc.tables
        if any("单价" in (c.text or "") and "合价" in "".join(x.text or "" for x in t.rows[0].cells) for c in t.rows[0].cells)
    )
    assert "设备" not in quote_header
    assert "技术参数要求" not in quote_header
    assert "名称" in quote_header
    assert "支持单点登录" in text
    assert "无偏差" in text
    assert "某园区信息化项目" in text
    assert "不侵犯任何第三方知识产权" in text
    assert "分项报价表" not in text
    assert not any(clause in text for clause in _CLAUSES)
    assert any("模块填写" in w for w in warnings)
    assert any("复制" in w for w in warnings)


def test_assemble_letter_copy_override_keeps_invitation_body(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    brief = default_brief()
    brief.layoutMode = "outline"
    brief.projectName = "数智制造管理系统"
    brief.tenderer = "某市分公司"
    brief.bidDate = "2026-09-08"
    brief.includePlaceholders = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(
            id="o01",
            title="响应函",
            kind="letter",
            source="copy",
            body="致：（招标人名称）\n我方参加（项目名称）的采购。",
        )
    ]
    dest = tmp_path / "copy-letter.docx"
    path, _warnings = assemble_bid_docx(brief, dest, qualification_pdf=None)
    text = _docx_text(Document(str(path)))
    assert "我方参加某市分公司" not in text
    assert "我方参加数智制造管理系统的采购" in text
    assert "响应总报价" not in text


def test_render_factory_and_tech_plan_modules() -> None:
    from docx import Document

    from api.services.tenders.modules import render_module

    brief = default_brief()
    brief.projectName = "数智制造管理系统"
    brief.tenderer = "某市分公司"
    brief.deliveryDays = 30
    brief.techPlanNote = "按采购需求实施，不编造桩数。"
    doc = Document()
    render_module(doc, brief, OutlineItem(id="f1", title="原厂生产承诺", kind="factory"))
    render_module(doc, brief, OutlineItem(id="t1", title="实施方案", kind="tech_plan"))
    text = _docx_text(doc)
    assert "河南伟泰光电科技有限公司" in text
    assert "承担原厂责任" in text
    assert "某市分公司" in text
    assert "按采购需求实施" in text
    assert "图纸" in text
    assert "在此粘贴扫描件" in text


def test_assemble_quote_and_dev_follow_invitation_headers(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    brief = default_brief()
    brief.layoutMode = "outline"
    brief.projectName = "数智制造管理系统"
    brief.tenderer = "某市分公司"
    brief.includePlaceholders = False
    brief.attachQualifications = False
    brief.quoteLines = [
        QuoteLineIn(
            seq="1",
            name="单点登录",
            unit="",
            qty=0,
            unitPrice=80000,
            amount=80000,
            groups=["制造管理系统", "用户中心"],
        )
    ]
    brief.deviationLines = [
        DeviationLine(seq="1", requirement="支持单点登录", response="支持单点登录", deviation="无偏差")
    ]
    brief.outlineItems = [
        OutlineItem(
            id="q1",
            title="报价表",
            kind="quote",
            source="generate",
            body="序号 | 系统名称 | 子系统 | 功能模块 | 单价（元） | 合价（元）\n |  |  |  |  | ",
        ),
        OutlineItem(
            id="d1",
            title="技术规范书偏离表",
            kind="tech_dev",
            source="generate",
            body="序号 | 招标文件规定 | 投标响应 | 偏离情况\n1 |  |  | ",
        ),
        OutlineItem(
            id="d2",
            title="商务条款偏离表",
            kind="biz_dev",
            source="generate",
            body="序号 | 商务条款 | 响应情况 | 偏差说明\n1 |  |  | ",
        ),
    ]
    path, _warnings = assemble_bid_docx(brief, tmp_path / "headers.docx", qualification_pdf=None)
    doc = Document(str(path))
    quote = next(t for t in doc.tables if any("功能模块" in (c.text or "") for c in t.rows[0].cells))
    quote_heads = [c.text for c in quote.rows[0].cells]
    assert quote_heads == ["序号", "系统名称", "子系统", "功能模块", "单价（元）", "合价（元）"]
    assert "设备" not in quote_heads
    assert quote.rows[1].cells[1].text == "制造管理系统"
    assert quote.rows[1].cells[2].text == "用户中心"
    assert quote.rows[1].cells[3].text == "单点登录"
    tech = next(t for t in doc.tables if any("招标文件规定" in (c.text or "") for c in t.rows[0].cells))
    assert [c.text for c in tech.rows[0].cells] == ["序号", "招标文件规定", "投标响应", "偏离情况"]
    assert "支持单点登录" in tech.rows[1].cells[1].text
    biz = next(t for t in doc.tables if any("商务条款" in (c.text or "") for c in t.rows[0].cells))
    assert [c.text for c in biz.rows[0].cells] == ["序号", "商务条款", "响应情况", "偏差说明"]


def test_generate_bid_respects_layout_mode(tmp_path, monkeypatch) -> None:
    from api.services.tenders import generate as generate_mod
    from api.services.tenders.generate import generate_bid

    called = {"outline": 0, "chapter5": 0}

    def fake_assemble(brief, dest, **_kwargs):
        called["outline"] += 1
        dest.write_bytes(b"PK\x03\x04")
        return dest, ["outline-path"]

    def fake_chapter5(brief, dest, **_kwargs):
        called["chapter5"] += 1
        dest.write_bytes(b"PK\x03\x04")
        return dest, ["chapter5-path"]

    monkeypatch.setattr(generate_mod, "build_bid_docx", fake_chapter5)
    monkeypatch.setattr("api.services.tenders.assemble.assemble_bid_docx", fake_assemble)

    outline_brief = BidBrief(
        layoutMode="outline",
        outlineItems=[OutlineItem(id="o01", title="响应函", kind="letter", body="致：甲方")],
        includePlaceholders=False,
        attachQualifications=False,
    )
    out = generate_bid(outline_brief)
    assert called["outline"] == 1
    assert called["chapter5"] == 0
    assert "outline-path" in out["warnings"]

    classic = BidBrief(layoutMode="chapter5", includePlaceholders=False, attachQualifications=False)
    generate_bid(classic)
    assert called["chapter5"] == 1


def _run_underlined(run) -> bool:
    rpr = run._element.find(qn("w:rPr"))
    if rpr is None:
        return bool(run.underline)
    for uel in rpr.findall(qn("w:u")):
        val = (uel.get(qn("w:val")) or "").lower()
        if val not in {"", "none", "nil"}:
            return True
    return False


def test_assemble_outline_toc_and_signoff_match_fixed(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    brief = default_brief()
    brief.layoutMode = "outline"
    brief.tenderer = "某热电有限公司"
    brief.bidPriceYuan = 518800
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(id="o01", title="投标函", kind="letter", source="generate"),
        OutlineItem(id="o02", title="法定代表人身份证明", kind="legal_id", source="generate"),
        OutlineItem(id="o03", title="授权委托书", kind="auth", source="generate"),
        *[
            OutlineItem(
                id=f"p{i:02d}",
                title=f"占位材料{i}",
                kind="scan",
                source="copy",
                body="（装订时附原件）",
            )
            for i in range(4, 11)
        ],
        OutlineItem(id="o11", title="业绩证明资料", kind="performance", source="generate"),
    ]
    dest = tmp_path / "layout.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    doc = Document(str(dest))
    texts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    assert not any(t.startswith("按招标书「") for t in texts)
    toc_letter = next(p for p in doc.paragraphs if p.text.startswith("一、投标函"))
    assert not any(_run_underlined(run) for run in toc_letter.runs if "投标函" in (run.text or ""))
    fonts = [
        el.get(qn("w:ascii"))
        for run in toc_letter.runs
        for el in run._element.iter(qn("w:rFonts"))
    ]
    assert "Times New Roman" in fonts
    instr = "".join(
        t.text or "" for el in toc_letter._p.iter(qn("w:instrText")) for t in [el]
    )
    assert "PAGEREF" in instr
    assert any(p.text.startswith("十一、业绩证明资料") for p in doc.paragraphs)

    name_cell = next(
        row.cells[1]
        for table in doc.tables
        for row in table.rows
        if "投标人名称" in (row.cells[0].text or "")
    )
    assert not any(_run_underlined(run) for para in name_cell.paragraphs for run in para.runs)

    auth_sign = next(
        p
        for p in doc.paragraphs
        if p.text.startswith("投") and "盖单位公章" in p.text and "河南伟泰" in p.text and "投标人" in "".join(p.text.split())
    )
    indent = float(auth_sign.paragraph_format.left_indent.cm) if auth_sign.paragraph_format.left_indent else 0
    assert indent >= 3.0
    assert any(_run_underlined(run) for run in auth_sign.runs if "河南伟泰" in (run.text or ""))
    letter_sign = next(p for p in doc.paragraphs if p.text.startswith("投　标　人："))
    letter_indent = (
        float(letter_sign.paragraph_format.left_indent.cm) if letter_sign.paragraph_format.left_indent else 0
    )
    assert letter_indent >= 3.0
    assert any(_run_underlined(run) for run in letter_sign.runs if "河南伟泰" in (run.text or ""))
    spacers = []
    for para in doc.paragraphs:
        p_pr = para._p.find(qn("w:pPr"))
        if p_pr is None:
            continue
        sp = p_pr.find(qn("w:spacing"))
        if sp is None or (sp.get(qn("w:lineRule")) or "") != "exact":
            continue
        if "".join((para.text or "").replace("\u200b", "").split()):
            continue
        spacers.append(int(sp.get(qn("w:line")) or "0"))
    assert spacers
    assert max(spacers) >= int(1.4 * 567)


def test_assemble_auth_sign_single_line_aligned(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx
    from api.services.tenders.document import _em_len

    brief = default_brief()
    brief.layoutMode = "outline"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.agentName = ""
    brief.bidDate = "2026-9-1"
    brief.outlineItems = [
        OutlineItem(id="o01", title="法定代表人授权委托书", kind="auth", source="generate"),
    ]
    dest = tmp_path / "auth-sign.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    doc = Document(str(dest))

    def compact(text: str) -> str:
        return "".join((text or "").split())

    title_i = next(
        i
        for i, p in enumerate(doc.paragraphs)
        if (p.text or "").strip() == "法定代表人授权委托书"
    )
    block = doc.paragraphs[title_i:]
    bidder = next(p for p in block if compact(p.text).startswith("投标人") and "盖单位公章" in p.text)
    legal = next(p for p in block if compact(p.text).startswith("法定代表人：") and "签字" in p.text)
    id_lines = [p for p in block if compact(p.text).startswith("身份证号码：")]
    agent = next(p for p in block if compact(p.text).startswith("委托代理人："))
    date = next(p for p in block if compact(p.text).startswith("日期："))
    assert len(id_lines) == 2
    for para in (bidder, legal, id_lines[0], agent, id_lines[1], date):
        assert para._p.find(".//" + qn("w:br")) is None
        wrap = para._p.find(".//" + qn("w:wordWrap"))
        assert wrap is not None and wrap.get(qn("w:val")) == "off"
        assert _em_len(para.text) <= 30
        head = (para.text or "").split("：", 1)[0]
        assert len(head) == 5
    assert bidder.text.split("：", 1)[0] == "投　标　人"
    assert date.text.split("：", 1)[0] == "日　　　期"
    assert "09" in date.text and "01" in date.text
    assert "河南伟泰光电科技有限公司" in bidder.text
    assert "（盖单位公章）" in bidder.text
    assert "郭志伟" not in legal.text and "（签字）" in legal.text
    assert "410521199802104053" in id_lines[0].text
    assert "郭志伟" not in agent.text and "（不委托）" not in agent.text and "（签字）" in agent.text

    empty_boxes = []
    saw_title = False
    for child in doc.element.body:
        text = "".join(t.text or "" for t in child.iter(qn("w:t"))).replace("\u200b", "").strip()
        if child.tag == qn("w:p") and text == "法定代表人授权委托书":
            saw_title = True
        if not saw_title:
            continue
        if child.tag == qn("w:tbl"):
            rows = child.findall(qn("w:tr"))
            cell_text = "".join(t.text or "" for t in child.iter(qn("w:t"))).strip()
            if len(rows) == 1 and not cell_text:
                empty_boxes.append(child)
    assert not empty_boxes
