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
    PlaceholderItem,
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


def test_fill_copy_blanks_empty_zhi_and_bidder_not_sign() -> None:
    raw = (
        "投标承诺书致：\n"
        "我公司现做出如下承诺：\n"
        "投标人： （盖章）\n"
        "法定代表人或委托代理人：（签字并盖章）\n"
        "日期： 年月日"
    )
    filled = fill_copy_blanks(
        raw,
        bidder="河南伟泰光电科技有限公司",
        project="厂内充电桩采购",
        tenderer="二连浩特市联源热电有限公司",
        legal="郭志伟",
        bid_date="2026-09-15",
    )
    assert "投标承诺书\n致：二连浩特市联源热电有限公司" in filled
    assert "投标人：河南伟泰光电科技有限公司（盖章）" in filled
    assert "2026年09月15日" in filled
    assert "郭志伟" not in filled
    assert "签字并盖章" in filled


def test_fill_copy_blanks_zhi_not_body_and_bare_company() -> None:
    mashed = fill_copy_blanks(
        "致：我公司现做出如下承诺：\n1、保证投标文件内容无任何虚假。",
        bidder="河南伟泰光电科技有限公司",
        project="厂内充电桩采购",
        tenderer="二连浩特市联源热电有限公司",
        legal="郭志伟",
        bid_date="2026-09-16",
    )
    assert mashed.splitlines()[0] == "致：二连浩特市联源热电有限公司"
    assert "我公司现做出如下承诺：" in mashed
    glued = fill_copy_blanks(
        "致：河南鑫宇光科技股份有限公司我方确认收到贵方提供的招标文件，并重申以下几点：",
        bidder="河南伟泰光电科技有限公司",
        project="MOM生产运营管理平台",
        tenderer="河南鑫宇光科技股份有限公司",
        legal="",
        bid_date="2026-09-23",
    )
    lines = [ln for ln in glued.splitlines() if ln.strip()]
    assert lines[0] == "致：河南鑫宇光科技股份有限公司"
    assert lines[1].startswith("我方确认收到")
    assert "我方确认" not in lines[0]
    bare = fill_copy_blanks(
        "有限公司：\n我方已全面阅读和研究了招标文件。",
        bidder="河南伟泰光电科技有限公司",
        project="厂内充电桩采购",
        tenderer="二连浩特市联源热电有限公司",
        legal="郭志伟",
        bid_date="2026-09-16",
    )
    assert bare.startswith("二连浩特市联源热电有限公司：")


def test_fill_copy_blanks_unfolds_mashed_sign_lines() -> None:
    filled = fill_copy_blanks(
        "投标人（章）：法定代表人或授权代表（签字）：\n"
        "联系人：联系电话：2026年09月16日",
        bidder="河南伟泰光电科技有限公司",
        project="厂内充电桩采购",
        tenderer="二连浩特市联源热电有限公司",
        legal="郭志伟",
        bid_date="2026-09-16",
        phone="17630567052",
        contact="郭志伟",
    )
    lines = [ln.strip() for ln in filled.splitlines() if ln.strip()]
    assert any(ln.startswith("投标人：") and "河南伟泰光电科技有限公司" in ln for ln in lines)
    assert any("法定代表人或授权代表" in ln and "郭志伟" not in ln for ln in lines)
    assert any(ln.startswith("联系人：") and "郭志伟" in ln for ln in lines)
    assert any("17630567052" in ln and "联系人" not in ln for ln in lines)
    assert any(ln.startswith("日期：") or "2026年09月16日" in ln for ln in lines)
    assert "：：" not in filled


def test_fill_copy_blanks_covers_gai_gongzhang() -> None:
    filled = fill_copy_blanks(
        "投标人(盖公章)：\n授权代表(签名)：\n日期： 年 月 日\n",
        bidder="河南伟泰光电科技有限公司",
        project="厂内充电桩采购",
        tenderer="河南鑫宇光科技股份有限公司",
        legal="郭志伟",
        bid_date="2026-09-23",
    )
    assert "河南伟泰光电科技有限公司" in filled
    assert "盖公章" in filled.replace(" ", "")
    assert "郭志伟" not in filled.split("授权代表")[-1].split("日期")[0]
    assert "2026年09月23日" in filled.replace(" ", "")


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
    assert choose_layout_mode("第四部分投标文件格式", classic) == "outline"
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
    toc_letter = next(t for t in paras if t.startswith("一、投标函"))
    assert any("法定代表人身份证明" in t for t in paras)
    assert any("授权委托书" in t for t in paras)
    assert paras.index(toc_letter) < next(
        i for i, t in enumerate(paras) if "法定代表人身份证明" in t or "授权委托书" in t
    )
    assert paras.index("法定代表人身份证明") < paras.index("授权委托书")


def test_assemble_keeps_one_biz_dev_table(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    brief = default_brief()
    brief.layoutMode = "outline"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(id="o01", title="商务偏离表", kind="biz_dev", source="generate"),
        OutlineItem(id="o02", title="商务偏离表招标项目", kind="biz_dev", source="generate"),
        OutlineItem(id="o03", title="技术规格偏离表及建议招标项目", kind="tech_dev", source="generate"),
    ]
    dest = tmp_path / "bizdev.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    doc = Document(str(dest))
    paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    assert paras.count("商务偏离表") == 1
    assert "商务偏离表招标项目" not in paras
    assert "技术规格偏离表及建议" in paras
    assert "技术规格偏离表及建议招标项目" not in paras
    biz_tables = [
        t
        for t in doc.tables
        if any("交货期" in (c.text or "") for row in t.rows for c in row.cells)
    ]
    assert len(biz_tables) == 1


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
    assert not body_p.paragraph_format.first_line_indent
    texts = [p.text.strip() for p in paras]
    title_i = next(i for i, t in enumerate(texts) if compact_title(t) == "投标函")
    bidder_i = next(
        i for i, t in enumerate(texts) if i > title_i and compact_title(t).startswith("投标人")
    )
    legal_i = next(i for i, t in enumerate(texts) if i > title_i and "签字" in t)
    contact_i = next(i for i, t in enumerate(texts) if i > title_i and compact_title(t).startswith("联系人"))
    phone_i = next(i for i, t in enumerate(texts) if i > title_i and "17630567052" in t)
    assert bidder_i != legal_i
    assert contact_i != phone_i
    assert "河南伟泰光电科技有限公司" in texts[bidder_i]
    assert "郭志伟" not in texts[legal_i]
    assert "：：" not in texts[phone_i]
    assert "w:u" in paras[bidder_i]._p.xml
    assert "w:u" in paras[legal_i]._p.xml


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
    assert "我方参加" in text
    assert "响应总报价" not in text
    assert not any(clause in text for clause in _CLAUSES)
    assert any("复制" in w for w in warnings)


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
    render_module(doc, brief, OutlineItem(id="t1", title="实施方案", kind="tech_plan"), media={})
    text = _docx_text(doc)
    assert "河南伟泰光电科技有限公司" in text
    assert "承担原厂责任" in text
    assert "某市分公司" in text
    assert "按采购需求实施" in text
    assert "图纸" in text
    assert "在此粘贴扫描件" in text


def test_tech_plan_embeds_uploaded_drawings(tmp_path) -> None:
    from PIL import Image
    from docx import Document

    from api.services.tenders.modules import render_module
    from api.services.tenders.placeholders import TECH_DRAWING_KEY

    png = tmp_path / "layout.png"
    Image.new("RGB", (160, 90), (30, 90, 160)).save(png, "PNG")
    extra = tmp_path / "layout2.png"
    Image.new("RGB", (140, 80), (80, 40, 20)).save(extra, "PNG")
    third = tmp_path / "layout3.png"
    Image.new("RGB", (100, 70), (20, 80, 40)).save(third, "PNG")
    brief = default_brief()
    brief.techPlanNote = "按采购需求实施，不编造桩数。"
    doc = Document()
    notes = render_module(
        doc,
        brief,
        OutlineItem(id="t1", title="实施方案", kind="tech_plan"),
        media={TECH_DRAWING_KEY: [png, extra, third]},
    )
    blob = doc.element.xml
    assert blob.count("a:blip") >= 3
    assert "在此粘贴扫描件" not in _docx_text(doc)
    assert any("图纸写入" in n for n in notes)


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
    biz_blob = "".join(c.text or "" for row in biz.rows for c in row.cells)
    assert "支持单点登录" not in biz_blob
    assert "交货" in biz_blob or "质保" in biz_blob


def test_generate_bid_respects_layout_mode(tmp_path, monkeypatch) -> None:
    from api.services.tenders import generate as generate_mod
    from api.services.tenders.generate import generate_bid

    called = {"outline": 0, "vols": []}

    def fake_assemble(brief, dest, **kwargs):
        called["outline"] += 1
        called["vols"].append(kwargs.get("volume"))
        dest.write_bytes(b"PK\x03\x04")
        return dest, ["outline-path"]

    monkeypatch.setattr(generate_mod, "tenders_output_dir", lambda: tmp_path)
    monkeypatch.setattr("api.services.tenders.assemble.assemble_bid_docx", fake_assemble)

    outline_brief = BidBrief(
        layoutMode="outline",
        generateVolume="business",
        outlineItems=[OutlineItem(id="o01", title="响应函", kind="letter", body="致：甲方")],
        includePlaceholders=False,
        attachQualifications=False,
    )
    out = generate_bid(outline_brief)
    assert called["outline"] == 1
    assert called["vols"] == ["business"]
    assert out.get("docxFile")
    assert not out.get("techDocxFile")
    assert "商务标.docx" in out["downloadName"]

    tech_brief = outline_brief.model_copy(update={"generateVolume": "technical"})
    tech_out = generate_bid(tech_brief)
    assert called["outline"] == 2
    assert called["vols"][-1] == "technical"
    assert tech_out.get("techDocxFile")
    assert not tech_out.get("docxFile")
    assert "技术标.docx" in (tech_out.get("techDownloadName") or "")

    classic = BidBrief(
        layoutMode="chapter5",
        generateVolume="business",
        includePlaceholders=False,
        attachQualifications=False,
    )
    generate_bid(classic)
    assert called["outline"] == 3
    assert called["vols"][-1] == "business"


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
    instr = "".join(
        t.text or "" for el in toc_letter._p.iter(qn("w:instrText")) for t in [el]
    )
    assert "PAGEREF" in instr
    assert any("业绩证明资料" in p.text for p in doc.paragraphs)
    assert any("2.1 法定代表人身份证明" in p.text or p.text.startswith("2.1") for p in doc.paragraphs)
    assert any("资格审查资料" in p.text for p in doc.paragraphs)

    name_line = next(
        p
        for p in doc.paragraphs
        if "投标人名称" in (p.text or "") and "伟泰" in (p.text or "")
    )
    assert any(_run_underlined(run) for run in name_line.runs if "伟泰" in (run.text or ""))

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


def test_paragraphs_keep_title_and_zhi_apart() -> None:
    from api.services.tenders.assemble import _paragraphs_from_body

    lines = _paragraphs_from_body(
        "投标承诺书\n致：\n我公司现做出如下承诺：\n[第13页]\n二连浩特市联源热电有限公司"
    )
    assert "投标承诺书" in lines
    assert any(ln.startswith("致") for ln in lines)
    assert not any("投标承诺书致" in compact_title(ln) for ln in lines)
    assert not any("第13页" in ln for ln in lines)
    assert "二连浩特市联源热电有限公司" not in lines


def test_assemble_commitment_copy_keeps_form_layout(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    brief = default_brief()
    brief.layoutMode = "outline"
    brief.tenderer = "二连浩特市联源热电有限公司"
    brief.bidDate = "2026-09-15"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(
            id="o01",
            title="投标承诺书",
            kind="commitment_copy",
            source="copy",
            body=(
                "投标承诺书致：\n"
                "我公司现做出如下承诺：\n"
                "1、保证投标文件内容无任何虚假。\n"
                "投标人： （盖章）\n"
                "法定代表人或委托代理人： （签字并盖章）\n"
                "日期： 年月日\n"
                "[第13页]\n"
                "二连浩特市联源热电有限公司\n"
            ),
        ),
    ]
    dest = tmp_path / "commitment.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    doc = Document(str(dest))
    texts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    blob = "\n".join(texts)
    titles = [t for t in texts if compact_title(t) == "投标承诺书"]
    assert titles
    zhi = next(t for t in texts if compact_title(t).startswith("致"))
    assert "二连浩特市联源热电有限公司" in zhi
    assert "我公司" not in zhi
    assert "投标承诺书致" not in compact_title(zhi)
    bidder = next(t for t in texts if "盖章" in t and "签字" not in t)
    assert "河南伟泰光电科技有限公司" in bidder
    sign = next(t for t in texts if "签字并盖章" in t)
    assert "郭志伟" not in sign
    assert "2026年09月15日" in blob or ("2026" in blob and "09" in blob and "15" in blob)
    assert "第13页" not in blob
    assert not any(compact_title(t) == "二连浩特市联源热电有限公司" for t in texts)
    zhi_para = next(p for p in doc.paragraphs if compact_title(p.text).startswith("致"))
    sign_para = next(p for p in doc.paragraphs if "签字并盖章" in p.text)
    bidder_para = next(p for p in doc.paragraphs if "盖章" in p.text and "签字" not in p.text)
    assert "w:u" in zhi_para._p.xml
    assert "w:u" in sign_para._p.xml
    assert "w:u" in bidder_para._p.xml


def test_coalesce_broken_legal_id_lines() -> None:
    from api.services.tenders.assemble import _paragraphs_from_body

    lines = _paragraphs_from_body(
        "成立时\n间：\n________\n年\n________\n月\n日经营期限：长期\n姓名：郭志伟"
    )
    blob = "｜".join(lines)
    assert "成立时间：" in blob
    assert "经营期限：长期" in blob
    assert all(ln != "成立时" and ln != "间：" for ln in lines)


def test_assemble_legal_id_copy_stays_on_form(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    brief = default_brief()
    brief.layoutMode = "outline"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(
            id="o02",
            title="法定代表人身份证明",
            kind="legal_id",
            source="copy",
            body=(
                "三、法定代表人身份证明\n投标人名称：________\n成立时\n间：\n年\n月\n"
                "日经营期限：长期\n姓名： 性别： 年龄： 职务：\n系（投标人名称）的法定代表人。"
            ),
        ),
    ]
    dest = tmp_path / "legal-id.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    doc = Document(str(dest))
    texts = [p.text.strip() for p in doc.paragraphs]
    nonempty = [t for t in texts if t]
    assert "特此证明。" in nonempty
    assert all(t != "成立时" and t != "间：" for t in nonempty)
    rel = next(t for t in nonempty if "法定代表人" in t and "系" in t)
    assert "郭志伟" in rel
    assert "河南伟泰光电科技有限公司" in rel
    person = next(t for t in nonempty if "姓名" in t and "职务" in t)
    assert "郭志伟" in person
    assert "执行董事" in person
    assert any(p.text.startswith("一、投标函") for p in doc.paragraphs)
    toc = next(
        p
        for p in doc.paragraphs
        if "法定代表人身份证明" in (p.text or "")
        and (p.text.startswith("二、") or p.text.startswith("2.1") or p.text.startswith("三、"))
    )
    instr = "".join(t.text or "" for el in toc._p.iter(qn("w:instrText")) for t in [el])
    assert "PAGEREF" in instr
    assert 'w:leader="dot"' in toc._p.xml


def test_assemble_inlines_id_cards_not_in_appendix(tmp_path) -> None:
    from PIL import Image
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    front = tmp_path / "id-front.png"
    back = tmp_path / "id-back.png"
    Image.new("RGB", (220, 350), (190, 170, 150)).save(front)
    Image.new("RGB", (220, 350), (150, 170, 190)).save(back)
    agent_f = tmp_path / "ag-front.png"
    agent_b = tmp_path / "ag-back.png"
    Image.new("RGB", (220, 350), (120, 160, 140)).save(agent_f)
    Image.new("RGB", (220, 350), (140, 120, 160)).save(agent_b)

    brief = default_brief()
    brief.layoutMode = "outline"
    brief.includePlaceholders = True
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.agentName = "李四"
    brief.outlineItems = [
        OutlineItem(id="o01", title="法定代表人身份证明", kind="legal_id", source="generate"),
        OutlineItem(id="o02", title="授权委托书", kind="auth", source="generate"),
    ]
    dest = tmp_path / "ids.docx"
    assemble_bid_docx(
        brief,
        dest,
        qualification_pdf=None,
        catalog_slots=[
            PlaceholderItem(key="id_legal", title="法定代表人身份证正反面"),
            PlaceholderItem(key="id_agent", title="授权代理人身份证正反面"),
            PlaceholderItem(key="bond", title="投标保证金缴存回单"),
        ],
        catalog_media={
            "id_legal": [front, back],
            "id_agent": [agent_f, agent_b],
        },
    )
    doc = Document(str(dest))
    texts = [p.text or "" for p in doc.paragraphs]
    assert not any("（一）法定代表人身份证正反面" in t for t in texts)
    assert not any("授权代理人身份证" in t and t.startswith("（") for t in texts)
    assert not any("附件：资料库扫描件" in t for t in texts)
    assert not any("投标保证金缴存回单" in t for t in texts)
    legal_i = next(i for i, t in enumerate(texts) if t.strip() == "法定代表人身份证明")
    auth_i = next(i for i, t in enumerate(texts) if t.strip() == "授权委托书")
    legal_sign = next(
        i
        for i, t in enumerate(texts[legal_i:auth_i], start=legal_i)
        if "投标人" in "".join(t.split()) and "盖单位公章" in t
    )
    auth_sign = next(
        i
        for i, t in enumerate(texts[auth_i:], start=auth_i)
        if "投标人" in "".join(t.split()) and "盖单位公章" in t
    )

    def _blip_between(a: int, b: int) -> bool:
        el = doc.paragraphs[a]._element.getnext()
        stop = doc.paragraphs[b]._element
        while el is not None and el is not stop:
            if el.findall(".//" + qn("a:blip")) or el.findall(".//" + qn("pic:blipFill")):
                return True
            el = el.getnext()
        return False

    assert _blip_between(legal_i, legal_sign)
    assert _blip_between(auth_i, auth_sign)
    id_para = next(
        p
        for p in doc.paragraphs[legal_i:legal_sign]
        if p._p.findall(".//" + qn("wp:extent"))
    )
    assert id_para.paragraph_format.keep_with_next is True
    assert doc.paragraphs[legal_sign].paragraph_format.keep_with_next is True
    assert doc.paragraphs[auth_sign].paragraph_format.keep_with_next is True


def test_estimate_counts_id_scan_height(tmp_path) -> None:
    from PIL import Image
    from docx import Document

    from api.services.tenders.document import _estimate_cm_on_current_page
    from api.services.tenders.placeholders import id_slot, inline_id_scans

    front = tmp_path / "f.png"
    back = tmp_path / "b.png"
    Image.new("RGB", (220, 350), (180, 160, 140)).save(front)
    Image.new("RGB", (220, 350), (140, 160, 180)).save(back)
    doc = Document()
    doc.add_paragraph("法定代表人身份证明")
    before = _estimate_cm_on_current_page(doc)
    inline_id_scans(doc, [front, back], empty_slot=id_slot("id_legal"))
    after = _estimate_cm_on_current_page(doc)
    assert after - before >= 4.5


def test_assemble_seal_register_fills_company_table(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    brief = default_brief()
    brief.layoutMode = "outline"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(id="o01", title="印鉴预留备案表", kind="company", source="copy", body=""),
    ]
    dest = tmp_path / "seal.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    doc = Document(str(dest))
    blob = _docx_text(doc)
    assert "公司证件、印章备案表" in blob
    assert "河南伟泰光电科技有限公司" in blob
    assert "公章" in blob
    assert "财务章" in blob
    assert "合同章" in blob
    assert "印鉴备案" in blob
    assert "三处备案印鉴为红色章" in blob
    stamp = None
    for table in doc.tables:
        for row in table.rows:
            if any("印鉴备案" in (c.text or "") for c in row.cells):
                stamp = row
                break
        if stamp is not None:
            break
    assert stamp is not None
    tr_pr = stamp._tr.find(qn("w:trPr"))
    el = None if tr_pr is None else tr_pr.find(qn("w:trHeight"))
    assert el is not None
    assert el.get(qn("w:hRule")) == "exact"
    assert int(el.get(qn("w:val")) or 0) >= 3000


def test_assemble_drops_duplicate_seal_register_page(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    brief = default_brief()
    brief.layoutMode = "outline"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(id="o01", title="印鉴预留备案表", kind="company", source="copy", body=""),
        OutlineItem(id="o02", title="印件备案表（附件七）", kind="company", source="copy", body=""),
    ]
    dest = tmp_path / "seal-dup.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    doc = Document(str(dest))
    blob = _docx_text(doc)
    assert "印件备案表" not in blob
    assert "印鉴预留备案表" in blob
    assert "公司证件、印章备案表" in blob


def test_collapse_consecutive_page_breaks_drops_empty_page() -> None:
    from docx import Document

    from api.services.tenders.assemble import _collapse_extra_page_breaks

    doc = Document()
    doc.add_paragraph("上页")
    doc.add_page_break()
    doc.add_paragraph("")
    doc.add_page_break()
    doc.add_paragraph("下页")
    _collapse_extra_page_breaks(doc)
    n = 0
    for p in doc.paragraphs:
        if (p.text or "").strip():
            continue
        if any(br.get(qn("w:type")) == "page" for br in p._element.iter(qn("w:br"))):
            n += 1
    assert n == 0
    tail = next(p for p in doc.paragraphs if (p.text or "").strip() == "下页")
    assert tail.paragraph_format.page_break_before is True


def test_chapter_break_clears_keep_next_on_last_line() -> None:
    from docx import Document

    from api.services.tenders.assemble import _chapter_break

    doc = Document()
    p = doc.add_paragraph("落款")
    p.paragraph_format.keep_with_next = True
    _chapter_break(doc)
    assert not p.paragraph_format.keep_with_next


def test_assemble_does_not_leave_empty_page_break_paras(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx
    from api.services.tenders.document import _paragraph_has_page_break

    brief = default_brief()
    brief.layoutMode = "outline"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(id="o01", title="投标函", kind="letter", source="generate", body=""),
        OutlineItem(id="o02", title="投标承诺书", kind="commitment_copy", source="copy", body=""),
        OutlineItem(id="o03", title="印鉴预留备案表", kind="company", source="copy", body=""),
    ]
    dest = tmp_path / "pages.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    doc = Document(str(dest))
    empty_breaks = 0
    for p in doc.paragraphs:
        if (p.text or "").strip():
            continue
        if _paragraph_has_page_break(p._p) and not p._p.findall(".//" + qn("w:drawing")):
            empty_breaks += 1
    assert empty_breaks == 0
    titles = [p.text.strip() for p in doc.paragraphs if (p.text or "").strip()]
    assert "投标函" in titles
    assert "投标承诺书" in titles


def test_quote_summary_rows_stay_compact(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    brief = default_brief()
    brief.layoutMode = "outline"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.bidPriceYuan = 518800
    brief.quoteLines = [
        QuoteLineIn(
            seq="1",
            name="交流充电桩",
            spec="（5）具备接入第三方智能化集成平台接口功能；\n（6）计量系统随设备自带；",
            unit="台",
            qty=10,
            unitPrice=51880,
            amount=518800,
        ),
    ]
    brief.outlineItems = [
        OutlineItem(id="o01", title="投标报价单", kind="quote", source="generate", body=""),
    ]
    dest = tmp_path / "quote.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    doc = Document(str(dest))
    quote_tbl = None
    for table in doc.tables:
        blob = "\n".join(cell.text for row in table.rows for cell in row.cells)
        if "不含税合计" in blob and "含税合计" in blob:
            quote_tbl = table
            break
    assert quote_tbl is not None
    for row in quote_tbl.rows[-3:]:
        blob = " ".join(cell.text for cell in row.cells)
        assert any(k in blob for k in ("不含税合计", "税率", "含税合计"))
        merged = row.cells[1]._tc
        paras = [child for child in merged if child.tag == qn("w:p")]
        assert len(paras) <= 2
        tr_pr = row._tr.find(qn("w:trPr"))
        th = None if tr_pr is None else tr_pr.find(qn("w:trHeight"))
        assert th is not None
        assert int(th.get(qn("w:val")) or 0) <= 400


def test_assemble_commitment_without_body_does_not_write_stock_clauses(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx
    from api.services.tenders.commitment import _CLAUSES

    brief = default_brief()
    brief.layoutMode = "outline"
    brief.tenderer = "二连浩特市联源热电有限公司"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(id="o01", title="投标承诺书", kind="commitment_copy", source="copy", body=""),
    ]
    dest = tmp_path / "commit.docx"
    path, warnings = assemble_bid_docx(brief, dest, qualification_pdf=None)
    doc = Document(str(path))
    blob = _docx_text(doc)
    assert "投标承诺书" in blob
    assert "我公司现做出如下承诺" not in blob
    assert not any(clause in blob for clause in _CLAUSES)
    assert any("未抽到招标书原文" in w for w in warnings)


def _qual_pdf(path):
    from PIL import Image

    Image.new("RGB", (320, 450), (230, 230, 230)).save(path, "PDF")
    return path


def test_assemble_warns_when_qual_pdf_missing(tmp_path) -> None:
    from api.services.tenders.assemble import assemble_bid_docx

    brief = default_brief()
    brief.layoutMode = "outline"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = True
    brief.outlineItems = [
        OutlineItem(id="o01", title="投标函", kind="letter", source="generate"),
    ]
    _, warnings = assemble_bid_docx(brief, tmp_path / "noqual.docx", qualification_pdf=None)
    assert any("未找到资质 PDF" in w for w in warnings)


def test_assemble_inserts_qual_pdf_into_outline_chapter(tmp_path) -> None:
    from docx import Document
    from docx.oxml.ns import qn

    from api.services.tenders.assemble import assemble_bid_docx

    pdf = _qual_pdf(tmp_path / "企业资质.pdf")
    brief = default_brief()
    brief.layoutMode = "outline"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = True
    brief.outlineItems = [
        OutlineItem(id="o01", title="投标函", kind="letter", source="generate"),
        OutlineItem(id="o02", title="企业资质", kind="scan", source="copy"),
    ]
    path, warnings = assemble_bid_docx(brief, tmp_path / "qual.docx", qualification_pdf=pdf)
    doc = Document(str(path))
    blob = _docx_text(doc)
    assert "企业资质" in blob
    assert "资质文件 第 1 页" in blob
    assert "附件：企业资质文件扫描件" not in blob
    assert any("已插入资质文件" in w for w in warnings)
    xml = doc.element.body.xml
    assert "a:blip" in xml or "pic:blipFill" in xml
    heading = next(p for p in doc.paragraphs if (p.text or "").strip() == "企业资质")
    found = False
    el = heading._element.getnext()
    while el is not None:
        if el.findall(".//" + qn("a:blip")) or el.findall(".//" + qn("pic:blipFill")):
            found = True
            break
        el = el.getnext()
    assert found


def test_find_qualification_pdf_looks_in_slots(tmp_path, monkeypatch) -> None:
    from api.services.tenders import assets as assets_mod

    monkeypatch.setattr(assets_mod, "tender_assets_dir", lambda: tmp_path)
    slot = tmp_path / "slots" / "license"
    slot.mkdir(parents=True)
    pdf = _qual_pdf(slot / "伟泰企业资质扫描件.pdf")
    found = assets_mod.find_qualification_pdf()
    assert found == pdf


def _volume_brief() -> BidBrief:
    brief = default_brief()
    brief.layoutMode = "outline"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(id="o01", title="商务标", kind="unknown", source="skip"),
        OutlineItem(id="o02", title="投标函", kind="letter", source="generate"),
        OutlineItem(id="o03", title="分项报价表", kind="quote", source="generate"),
        OutlineItem(id="o04", title="技术标", kind="tech_plan", source="generate"),
        OutlineItem(id="o05", title="技术偏差表", kind="tech_dev", source="generate"),
        OutlineItem(id="o06", title="技术标（实施方案）", kind="tech_plan", source="generate"),
    ]
    return brief


def test_assemble_business_volume_excludes_tech(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    path, _ = assemble_bid_docx(_volume_brief(), tmp_path / "biz.docx", volume="business")
    blob = _docx_text(Document(str(path)))
    assert "商 务 标 投 标 文 件" in blob
    assert "投标函" in blob
    assert "分项报价" in blob
    assert "实施方案" not in blob
    assert "技术偏差" not in blob
    assert "技 术 标 投 标 文 件" not in blob


def test_assemble_technical_volume_excludes_quote(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    path, _ = assemble_bid_docx(_volume_brief(), tmp_path / "tech.docx", volume="technical")
    blob = _docx_text(Document(str(path)))
    assert "技 术 标 投 标 文 件" in blob
    assert "技术偏差" in blob or "实施方案" in blob
    assert "分项报价" not in blob
    assert "投标函" not in blob
    assert "商 务 标 投 标 文 件" not in blob


def test_assemble_technical_volume_embeds_drawings(tmp_path) -> None:
    from PIL import Image
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx
    from api.services.tenders.placeholders import TECH_DRAWING_KEY

    png = tmp_path / "a.png"
    Image.new("RGB", (120, 80), (10, 80, 140)).save(png, "PNG")
    brief = _volume_brief()
    brief.techPlanNote = "按现场布置施工。"
    path, warnings = assemble_bid_docx(
        brief,
        tmp_path / "tech.docx",
        volume="technical",
        catalog_media={TECH_DRAWING_KEY: [png]},
    )
    blob = Document(str(path)).element.xml
    assert "a:blip" in blob
    assert any("图纸写入" in w for w in warnings)


def test_assemble_technical_omits_library_scan_heading(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    brief = default_brief()
    brief.layoutMode = "outline"
    brief.includePlaceholders = True
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(id="o01", title="技术偏差表", kind="tech_dev", source="generate"),
    ]
    path, _ = assemble_bid_docx(
        brief,
        tmp_path / "tech-slots.docx",
        volume="technical",
        catalog_slots=[
            PlaceholderItem(key="product", title="所投产品检测报告 / 3C / 对应功率桩型证明"),
        ],
    )
    blob = _docx_text(Document(str(path)))
    assert "附件：资料库扫描件" not in blob
    assert "所投产品检测报告" not in blob


def test_assemble_copies_invitation_commitment_not_stock_clauses(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx
    from api.services.tenders.commitment import _CLAUSES

    body = (
        "附一：投标承诺函模板\n"
        "投标承诺函\n"
        "致：（招标人名称）\n"
        "1、我方已详细研究了招标文件的所有内容。\n"
        "2、我方承诺投标文件夹中的一切资料、数据是真实的。\n"
        "3、我方明白并同意若我方在投标有效期之内撤回投标，则投标保证金将被贵方没收。\n"
        "4、我方理解贵方不一定接受最低标价或任何贵方可能收到的投标。\n"
        "5、我方如果中标，将保证履行招标文件中的全部责任和义务。\n"
        "投标人(盖公章)：\n"
        "日期： 年 月 日\n"
    )
    brief = default_brief()
    brief.layoutMode = "outline"
    brief.tenderer = "河南鑫宇光科技股份有限公司"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(
            id="o01",
            title="投标承诺函模板",
            kind="commitment_copy",
            source="copy",
            body=body,
        )
    ]
    dest = tmp_path / "commit-src.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    doc = Document(str(dest))
    blob = _docx_text(doc)
    assert "详细研究了招标文件" in blob
    assert "资料、数据是真实的" in blob
    assert "不一定接受最低标价" in blob
    assert "附一：投标承诺函模板" not in blob
    assert not any(clause in blob for clause in _CLAUSES)


def test_assemble_splits_zhi_company_from_body(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    body = (
        "投标承诺函\n"
        "致：河南鑫宇光科技股份有限公司我方确认收到贵方提供的鑫宇科技《MOM生产运营管理平台》"
        "项目招标投标所需的招标文件，并已完全明白招标文件的所有条款要求，并重申以下几点：\n"
        "1、我方已详细研究了招标文件的所有内容。\n"
        "投标人(盖公章)：\n"
        "授权代表(签名)：\n"
        "日期： 年 月 日\n"
    )
    brief = default_brief()
    brief.layoutMode = "outline"
    brief.tenderer = "河南鑫宇光科技股份有限公司"
    brief.bidDate = "2026-09-23"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(
            id="o01",
            title="投标承诺函",
            kind="commitment_copy",
            source="copy",
            body=body,
        )
    ]
    dest = tmp_path / "zhi-split.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    doc = Document(str(dest))
    paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    zhi = next(t for t in paras if t.startswith("致"))
    assert "我方确认" not in zhi
    assert "河南鑫宇光科技股份有限公司" in zhi
    body_p = next(t for t in paras if t.startswith("我方确认收到"))
    assert "招标文件" in body_p
    date_i = next(i for i, t in enumerate(paras) if compact_title(t).startswith("日期"))
    assert "2026" in paras[date_i]
    if date_i + 1 < len(paras):
        assert paras[date_i + 1] != "2026年09月23日"
    assert any("投标人" in compact_title(t) and "盖公章" in compact_title(t) for t in paras)


def test_assemble_copied_sign_lines_have_underline(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    body = (
        "投标承诺函\n"
        "致：河南鑫宇光科技股份有限公司\n"
        "1、我方已详细研究了招标文件的所有内容。\n"
        "投标人(盖公章)：\n"
        "授权代表(签名)：\n"
        "日期：\n"
        "年 月 日\n"
    )
    brief = default_brief()
    brief.layoutMode = "outline"
    brief.tenderer = "河南鑫宇光科技股份有限公司"
    brief.bidDate = "2026-09-23"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(
            id="o01",
            title="投标承诺函",
            kind="commitment_copy",
            source="copy",
            body=body,
        )
    ]
    dest = tmp_path / "commit-sign.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    doc = Document(str(dest))

    def hit(pred):
        return next(p for p in doc.paragraphs if pred(p.text or ""))

    bidder = hit(lambda t: "盖公章" in compact_title(t) and "投标人" in compact_title(t))
    agent = hit(lambda t: "授权代表" in compact_title(t) or "签名" in compact_title(t))
    date = hit(lambda t: compact_title(t).startswith("日期"))
    assert "w:u" in bidder._p.xml
    assert "河南伟泰" in bidder.text
    assert "w:u" in agent._p.xml
    assert "郭志伟" not in agent.text
    assert "w:u" in date._p.xml
    assert "2026" in date.text


def test_assemble_keeps_multiline_commitment_paragraphs(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    body = (
        "1、甲方已研究招标文件。乙方不得以含糊为由抗辩。丙方继续履行。\n"
        "2、资料真实。"
    )
    brief = default_brief()
    brief.layoutMode = "outline"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(
            id="o01",
            title="投标承诺书",
            kind="commitment_copy",
            source="copy",
            body=body,
        )
    ]
    dest = tmp_path / "commit-lines.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    paras = [p.text.strip() for p in Document(str(dest)).paragraphs if "甲方已研究" in (p.text or "")]
    assert paras
    assert "乙方不得以含糊为由抗辩" in paras[0]
    assert "丙方继续履行" in paras[0]


def test_assemble_copies_seal_register_body_instead_of_grid(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    body = (
        "印鉴预留备案\n"
        "公司名称：________  公司电话：________\n"
        "公章    财务章    合同章\n"
        "备注：本表中三处备案印鉴为红色章。"
    )
    brief = default_brief()
    brief.layoutMode = "outline"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(id="o01", title="印鉴预留备案表", kind="company", source="copy", body=body),
    ]
    dest = tmp_path / "seal-copy.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    doc = Document(str(dest))
    blob = _docx_text(doc)
    assert "三处备案印鉴为红色章" in blob
    stamp = None
    for table in doc.tables:
        for row in table.rows:
            if any((c.text or "").strip() == "印鉴备案" for c in row.cells):
                stamp = row
                break
    assert stamp is None


def test_assemble_follows_invitation_templates_not_fixed_seal_or_quote(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    req_body = (
        "附二：招标产品功能需求清单及说明\n"
        "一、MOM生产运营管理平台：\n"
        "设备数采 1.多类型数据采集；2数据传输与存储功能\n"
        "TPM系统 1.设备档案管理\n"
    )
    quote_body = (
        "附三：报价单模板\n"
        "项目总报价：\n"
        "序号 项目内容 金额(元) 备注\n"
        "1 MOM平台总费用\n"
        "2 总实施费用\n"
        "3 年度服务费\n"
        "4 合计\n"
        "（1）数采报价\n"
        "序号 项目内容 金额(元) 备注\n"
        "1 软件费用\n"
        "2 实施费用\n"
        "3 合计(1+2)\n"
        "（6）实施费用分项明细\n"
    )
    commit_body = (
        "附一：投标承诺函模板\n"
        "投标承诺函\n"
        "致：（招标人名称）\n"
        "1、我方已详细研究了招标文件的所有内容。\n"
        "投标人(盖公章)：\n"
        "日期： 年 月 日\n"
    )
    brief = default_brief()
    brief.layoutMode = "outline"
    brief.tenderer = "河南鑫宇光科技股份有限公司"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(
            id="o01",
            title="投标承诺函模板",
            kind="commitment_copy",
            source="copy",
            body=commit_body,
        ),
        OutlineItem(
            id="o02",
            title="招标产品功能需求清单及说明",
            kind="unknown",
            source="copy",
            body=req_body,
        ),
        OutlineItem(
            id="o03",
            title="报价单模板",
            kind="quote",
            source="copy",
            body=quote_body,
        ),
    ]
    dest = tmp_path / "mom-outline.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    doc = Document(str(dest))
    blob = _docx_text(doc)
    assert "详细研究了招标文件" in blob
    assert "招标产品功能需求清单" in blob
    assert "设备数采" in blob
    assert "MOM平台总费用" in blob
    assert "软件费用" in blob
    assert "实施费用分项明细" in blob
    assert "附一：投标承诺函模板" not in blob
    assert "附二：招标产品功能需求清单" not in blob
    assert "附件：资料库扫描件" not in blob
    assert "公司证件、印章备案表" not in blob
    assert "印鉴备案" not in blob
    assert "不含税综合单价" not in blob
    quote_tables = [
        t
        for t in doc.tables
        if t.rows and any("项目内容" in (c.text or "") for c in t.rows[0].cells)
    ]
    assert quote_tables
    heads = [c.text for c in quote_tables[0].rows[0].cells]
    assert "项目内容" in heads
    assert "设备" not in heads
    req_tables = [
        t
        for t in doc.tables
        if any("设备数采" in (c.text or "") for row in t.rows for c in row.cells)
    ]
    assert req_tables
    req_blob = " ".join(c.text or "" for row in req_tables[0].rows for c in row.cells)
    assert "TPM系统" in req_blob
    assert "设备档案管理" in req_blob


def test_assemble_outline_empty_volume_not_charger_pack(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    brief = default_brief()
    brief.layoutMode = "outline"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(
            id="o01",
            title="报价单模板",
            kind="quote",
            source="copy",
            body="序号 项目内容 金额(元) 备注\n1 MOM平台总费用\n",
        ),
    ]
    path, warnings = assemble_bid_docx(brief, tmp_path / "tech-empty.docx", volume="technical")
    blob = _docx_text(Document(str(path)))
    assert any("组卷大纲为空" in w for w in warnings)
    assert "公司证件、印章备案表" not in blob
    assert "印鉴备案" not in blob
    assert "不含税综合单价" not in blob


def test_assemble_skips_duplicate_license_scan_and_quote_debris(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    brief = default_brief()
    brief.layoutMode = "outline"
    brief.includePlaceholders = True
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.bidDate = "2026-09-22"
    brief.outlineItems = [
        OutlineItem(id="o01", title="供应商名称：与营业执照、资质证书一", kind="scan", source="copy"),
        OutlineItem(
            id="o02",
            title="B.有效的企业营业执照、企业资质证书",
            kind="scan",
            source="copy",
            body="B.有效的企业营业执照\nC.业绩要求；\nD.财务要求",
        ),
        OutlineItem(id="o03", title="分项报价表", kind="quote", source="generate"),
        OutlineItem(id="o04", title="分项报价表说明", kind="quote", source="generate"),
        OutlineItem(id="o05", title="分项报价表单位：人民币元序号", kind="quote", source="generate"),
        OutlineItem(
            id="o06",
            title="合同条款响应书",
            kind="commitment_copy",
            source="copy",
            body="二、合同条款响应书\n致：________\n供应商：________（全称、盖章）\n日期： 年 月 日\n四、响应方案格式自拟",
        ),
    ]
    path, _ = assemble_bid_docx(
        brief,
        tmp_path / "dedup.docx",
        catalog_slots=[PlaceholderItem(key="license", title="营业执照")],
        catalog_media={},
    )
    blob = _docx_text(Document(str(path)))
    assert "供应商名称：与营业执照" not in blob
    assert "C.业绩要求" not in blob
    assert "分项报价表说明" not in blob
    assert "人民币元序号" not in blob
    assert "2026年09月22日四、" not in blob.replace(" ", "")
    assert blob.count("分项报价表") <= 3


def test_assemble_pipe_requirement_and_focus_tables(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    req_body = (
        "一、MOM生产运营管理平台：\n"
        "MOM | 设备数采 | 1.多类型数据采集；2.数据传输与存储功能；\n"
        "MOM | TPM系统 | 1.设备档案管理；2. 设备台账与库存管理；\n"
        " | 硬件 | 单独报价，不记录在总价中。\n"
        "二、重点需求明细包括（但不限于）：\n"
        "模块 | 建设要求 | 实现目标数据采集 | 1.多类型数据采集； | 数据采集的基础，针对设备的运行状态。\n"
        "TPM系统 | 1.设备档案管理；2.设备台账与库存管理； | "
        "1.设备档案管理系统的数据基础，实现设备信息的标准化。"
        "基础信息：设备编号、名称、型号、采购日期、单价；"
        "备品备件管理：采购单价、供应商，备件出入库登记；"
        "录入核心信息：供应商、校准周期、安全库存。\n"
        "传感器信号弱、网关离线），及时发现潜在问题。\n"
    )
    brief = default_brief()
    brief.layoutMode = "outline"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(
            id="o01",
            title="招标产品功能需求清单及说明",
            kind="unknown",
            source="copy",
            body=req_body,
        )
    ]
    dest = tmp_path / "req-tables.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    doc = Document(str(dest))
    blob = _docx_text(doc)
    assert "设备数采" in blob
    assert "硬件" in blob
    assert "重点需求明细" in blob
    assert "数据采集的基础" in blob
    mom = next(
        t
        for t in doc.tables
        if any("设备数采" in (c.text or "") for row in t.rows for c in row.cells)
    )
    mom_blob = " ".join(c.text or "" for row in mom.rows for c in row.cells)
    assert "TPM系统" in mom_blob
    assert "硬件" in mom_blob
    focus = next(
        t
        for t in doc.tables
        if any((c.text or "").strip() == "模块" for row in t.rows for c in row.cells)
    )
    heads = [c.text.strip() for c in focus.rows[0].cells]
    assert "模块" in heads
    assert "建设要求" in heads
    assert "实现目标" in heads
    focus_blob = " ".join(c.text or "" for row in focus.rows for c in row.cells)
    assert "数据采集" in focus_blob
    tpm_row = next(
        row
        for row in focus.rows
        if any("TPM系统" in (c.text or "") for c in row.cells)
    )
    tpm_cells = [c.text or "" for c in tpm_row.cells]
    assert any("设备档案管理系统的数据基础" in c for c in tpm_cells)
    assert any("采购日期" in c for c in tpm_cells)
    assert any("供应商" in c for c in tpm_cells)
    assert any("网关离线" in c for c in tpm_cells)
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    assert tpm_row.cells[0].vertical_alignment == WD_CELL_VERTICAL_ALIGNMENT.CENTER
    assert tpm_row.cells[1].vertical_alignment == WD_CELL_VERTICAL_ALIGNMENT.CENTER
    assert tpm_row.cells[0].paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert tpm_row.cells[1].paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert len(tpm_row.cells[1].paragraphs) >= 2
    paras = "\n".join(p.text or "" for p in doc.paragraphs)
    assert "设备档案管理系统的数据基础" not in paras
    tbl_w = focus._tbl.tblPr.find(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tblW"
    )
    assert tbl_w is not None
    assert int(tbl_w.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}w") or "0") > 5000


def test_assemble_keeps_single_filled_quote_sheet(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    brief = default_brief()
    brief.layoutMode = "outline"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.bidPriceYuan = 897733
    brief.quoteLines = [
        QuoteLineIn(seq="1", name="7KW交流汽车充电桩", unit="台", qty=1, unitPrice=787.61, amount=787.61),
        QuoteLineIn(seq="2", name="30KW直流汽车充电桩", unit="台", qty=1, unitPrice=5823.01, amount=5823.01),
    ]
    brief.outlineItems = [
        OutlineItem(
            id="o01",
            title="本次招标方案按照软件总体报价，实施费分项报价形式；甲方按照需求分期签订实施合同",
            kind="quote",
            source="generate",
        ),
        OutlineItem(
            id="o02",
            title="投标报价表(产品、实施、开发对接、服务、硬件服务器方案分项报价)及报价说明",
            kind="quote",
            source="generate",
        ),
        OutlineItem(id="o03", title="报价单", kind="quote", source="generate"),
    ]
    dest = tmp_path / "one-quote.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    doc = Document(str(dest))
    blob = _docx_text(doc)
    quote_tables = [
        t
        for t in doc.tables
        if any("不含税合计" in (c.text or "") for row in t.rows for c in row.cells)
    ]
    assert len(quote_tables) == 1
    assert "按照软件总体报价" not in blob
    assert "硬件服务器方案" not in blob
    assert "报价单" in blob
    names = " ".join(c.text or "" for row in quote_tables[0].rows for c in row.cells)
    assert "交流汽车充电桩" in names


def test_assemble_quote_matrix_keeps_sites_and_hardware_apart(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    quote_body = (
        "项目总报价：\n"
        "序号 | 项目内容 | 金额(元) | 备注\n"
        "1 | MOM平台总费用 |  |\n"
        "（1）数采报价序号 | 项目内容 | 金额(元) | 备注\n"
        "1 | 软件费用 |  |\n"
        "2 | 实施费用 |  |\n"
        "3 | 合计(1+2) |  |\n"
        "实施费用分项明细。                                               单位：元\n"
        " | 数据采集 | TPM | WMS | MES | QMS | 合计（元） | 备注总部 |  |  |  |  |  |  |\n"
        "吉成 |  |  |  |  |  |  |\n"
        "成都 |  |  |  |  |  |  |\n"
        "郑州 |  |  |  |  |  |  |\n"
        "（7）硬件单独报价，不记录到总价。（单价）\n"
        "序号 | 项目内容 | 品牌 | 型号 | 单价（元）\n"
        "1 |  |  |  |\n"
        "2 |  |  |  |\n"
        "3 |  |  |  |\n"
    )
    brief = default_brief()
    brief.layoutMode = "outline"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(
            id="o01",
            title="报价单模板",
            kind="quote",
            source="copy",
            body=quote_body,
        )
    ]
    dest = tmp_path / "quote-matrix.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    doc = Document(str(dest))
    matrix = next(
        t
        for t in doc.tables
        if any((c.text or "").strip() == "数据采集" for row in t.rows for c in row.cells)
    )
    assert len(matrix.columns) <= 8
    widths = [int(c.width) for c in matrix.rows[0].cells]
    assert min(widths[1:]) * 4 > widths[0]
    heads = [(c.text or "").strip() for c in matrix.rows[0].cells]
    assert "数据采集" in heads
    assert heads.count("数据采集") == 1
    assert all("\n" not in (c.text or "") for c in matrix.rows[0].cells)
    assert "数据采集" in heads
    assert "TPM" in heads
    assert "WMS" in heads
    assert "MES" in heads
    assert "QMS" in heads
    assert any("合计" in h for h in heads)
    assert any(h == "备注" for h in heads)
    assert not any("总部" in h for h in heads)
    sites = " ".join((row.cells[0].text or "") for row in matrix.rows)
    assert "总部" in sites
    assert "吉成" in sites
    assert "成都" in sites
    assert "郑州" in sites
    matrix_blob = " ".join(c.text or "" for row in matrix.rows for c in row.cells)
    assert "硬件单独报价" not in matrix_blob
    assert "品牌" not in matrix_blob
    numcai = next(
        t
        for t in doc.tables
        if any((c.text or "").strip() == "软件费用" for row in t.rows for c in row.cells)
    )
    assert (numcai.rows[0].cells[0].text or "").strip() == "序号"
    assert "数采报价序号" not in (numcai.rows[0].cells[0].text or "")
    hw = next(
        t
        for t in doc.tables
        if any((c.text or "").strip() == "品牌" for row in t.rows for c in row.cells)
    )
    hw_heads = [(c.text or "").strip() for c in hw.rows[0].cells]
    assert "品牌" in hw_heads
    assert "型号" in hw_heads
    assert "吉成" not in " ".join(c.text or "" for row in hw.rows for c in row.cells)
    blob = _docx_text(doc)
    assert "（1）数采报价" in blob
    assert "硬件单独报价" in blob
    cap = next(p for p in doc.paragraphs if "实施费用分项明细" in (p.text or ""))
    assert "单位" in (cap.text or "")
    assert "元" in (cap.text or "")


def test_assemble_biz_essentials_use_title_and_box(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    brief = default_brief()
    brief.layoutMode = "outline"
    brief.includePlaceholders = False
    brief.includeCommitment = False
    brief.attachQualifications = False
    brief.outlineItems = [
        OutlineItem(
            id="o01",
            title="投标承诺函",
            kind="commitment_copy",
            source="copy",
            body="我方确认收到贵方提供的招标文件，并重申以下几点：",
        ),
        OutlineItem(
            id="o02",
            title="报价单模板",
            kind="quote",
            source="copy",
            body="序号 | 项目内容 | 金额(元) | 备注\n1 | MOM平台总费用 |  |\n",
        ),
    ]
    dest = tmp_path / "biz-essentials.docx"
    assemble_bid_docx(brief, dest, qualification_pdf=None)
    blob = _docx_text(Document(str(dest)))
    assert "投标函" in blob
    assert "营业执照" in blob
    assert "商务偏离表" in blob
    assert "无违法承诺函" in blob
    assert "投标保证金" in blob
    assert "在此粘贴扫描件" in blob
    assert "附件：资料库扫描件" not in blob
    assert "我方确认收到贵方提供的招标文件" in blob


