"""组卷大纲抽取（合成正文，不依赖某份真实招标书）。"""

from __future__ import annotations

from api.services.tenders.categories import is_volume_label, item_volume
from api.services.tenders.outline import (
    bid_item_title,
    classify_kind,
    compact_title,
    dedupe_outline_items,
    extract_outline,
    ensure_biz_essentials,
    is_noise_title,
    is_outline_junk,
    is_seal_register,
    items_for_volume,
    source_for_kind,
    split_mashed_zhi_line,
    unfold_form_sign_lines,
    unmash_invitation_text,
)
from api.services.tenders.schema import BidBrief, OutlineItem


def test_classify_kind_rules() -> None:
    assert classify_kind("投标函") == "letter"
    assert classify_kind("响应函") == "letter"
    assert classify_kind("法定代表人/负责人身份证明") == "legal_id"
    assert classify_kind("法定代表人授权委托书") == "auth"
    assert classify_kind("分项报价表") == "quote"
    assert classify_kind("报价清单") == "quote"
    assert classify_kind("投标报价单") == "quote"
    assert classify_kind("投标保证金") == "scan"
    assert classify_kind("资质证明资料") == "scan"
    assert classify_kind("业绩证明资料") == "performance"
    assert classify_kind("印鉴预留备案表") == "company"
    assert classify_kind("印件备案表（附件七）") == "company"
    assert is_seal_register(title="印鉴预留备案表")
    assert is_seal_register(title="印件备案表（附件七）")
    assert classify_kind("商务条款偏离表") == "biz_dev"
    assert classify_kind("技术规范书偏离表") == "tech_dev"
    assert classify_kind("知识产权不侵权承诺函") == "commitment_copy"
    assert classify_kind("投标承诺书") == "commitment_copy"
    assert classify_kind("企业业绩") == "performance"
    assert classify_kind("原厂生产承诺") == "factory"
    assert classify_kind("技术标（实施方案）") == "tech_plan"
    assert classify_kind("响应方案") == "tech_plan"
    assert classify_kind("技术标准和要求") != "tech_plan"
    assert classify_kind("合同条款响应书") == "commitment_copy"
    assert classify_kind("商务和技术偏差表") == "tech_dev"
    assert classify_kind("分项报价表说明") == "unknown"
    assert classify_kind("分项报价表单位：人民币元序号") == "unknown"
    assert classify_kind("商务标") == "unknown"
    assert is_volume_label("商务标")
    assert is_volume_label("技术标")
    assert item_volume(kind="quote", title="分项报价表") == "business"
    assert item_volume(kind="tech_plan", title="实施方案") == "technical"
    assert classify_kind("营业执照") == "scan"
    assert classify_kind("企业资质") == "scan"
    assert classify_kind("企业资质文件") == "scan"
    assert classify_kind("直接采购文件税务信息表") == "company"
    assert classify_kind("报价单模板") == "quote"
    assert classify_kind("招标产品功能需求清单及说明") == "unknown"
    assert classify_kind("无单位和法定代表人的印鉴") == "unknown"
    assert is_outline_junk(title="无单位和法定代表人的印鉴")
    assert is_noise_title("无单位和法定代表人的印鉴")
    assert not is_outline_junk(title="印鉴预留备案表")
    assert source_for_kind("letter") == "generate"
    assert source_for_kind("unknown") == "copy"
    assert source_for_kind("commitment_copy") == "copy"
    assert source_for_kind("letter", skipped=True) == "skip"


def test_extract_auth_need_and_default_skip() -> None:
    from api.services.tenders.outline import apply_auth_outline, extract_auth_need, extract_outline

    optional = """
第四部分 投标文件格式
法定代表人身份证明
法定代表人授权委托书
投标承诺书
"""
    _ch, items = extract_outline(optional)
    assert extract_auth_need(optional, items) == "optional"
    skipped = apply_auth_outline(items, has_agent=False, auth_need="optional")
    auth = next(item for item in skipped if item.kind == "auth")
    assert auth.skipped
    kept = apply_auth_outline(items, has_agent=True, auth_need="optional")
    assert not next(item for item in kept if item.kind == "auth").skipped

    required_text = "投标人必须提供法定代表人授权委托书，未提供按废标处理。"
    assert extract_auth_need(required_text, items) == "required"
    forced = apply_auth_outline(items, has_agent=False, auth_need="required")
    assert not next(item for item in forced if item.kind == "auth").skipped

    personally = "法定代表人亲自投标的，可不提供授权委托书。"
    assert extract_auth_need(personally, items) == "optional"

    submit = "投标文件须由委托代理人递交，开标时授权代表应出席。"
    assert extract_auth_need(submit, items) == "required"


def test_extract_outline_drops_unknown_titles() -> None:
    text = """
第四部分 投标文件格式
投标函
竞标书
法定代表人身份证明
备品配件及专用工具表（选填）
业绩证明资料
"""
    _chapter, items = extract_outline(text)
    titles = [item.title for item in items]
    assert "投标函" in titles
    assert "法定代表人身份证明" in titles
    assert "业绩证明资料" in titles
    assert "竞标书" not in titles
    assert not any(item.kind == "unknown" for item in items)


def test_extract_classic_invitation_format() -> None:
    text = """
第一章 投标邀请
第二章 投标人须知
第四部分 投标文件格式
附件一：投标函
附件二：法定代表人身份证明
附件三：授权委托书
附件四：分项报价表
附件五：投标承诺书
第五部分 评标办法
"""
    chapter, items = extract_outline(text)
    assert "投标文件格式" in chapter
    titles = [item.title for item in items]
    assert titles == ["投标函", "法定代表人身份证明", "授权委托书", "分项报价表", "投标承诺书"]
    kinds = [item.kind for item in items]
    assert kinds == ["letter", "legal_id", "auth", "quote", "commitment_copy"]
    assert items[0].source == "generate"
    assert items[-1].source == "copy"
    assert items[0].id == "o01"


def test_extract_response_file_format_chapter() -> None:
    text = """
第一章 响应邀请书
第二章 供应商须知
第五章 响应文件格式
响应函
法定代表人/负责人身份证明
法定代表人/负责人授权委托书
承诺函
商务条款偏离表
技术规范书偏离表
知识产权不侵权承诺函
电子印章办理承诺函
报价表
报价清单
第六章 合同条款
"""
    chapter, items = extract_outline(text)
    assert chapter.startswith("第五章")
    assert "响应文件格式" in chapter
    kinds = {item.title: item.kind for item in items}
    assert kinds["响应函"] == "letter"
    assert kinds["法定代表人/负责人身份证明"] == "legal_id"
    assert kinds["法定代表人/负责人授权委托书"] == "auth"
    assert kinds["承诺函"] == "commitment_copy"
    assert kinds["商务条款偏离表"] == "biz_dev"
    assert kinds["技术规范书偏离表"] == "tech_dev"
    assert kinds["知识产权不侵权承诺函"] == "commitment_copy"
    quotes = [item for item in items if item.kind == "quote"]
    assert len(quotes) == 1
    assert quotes[0].title in {"报价表", "报价清单", "报价单"}
    assert "合同条款" not in kinds


def test_extract_from_pipe_table_and_dedupe() -> None:
    text = """
投标文件格式
附件一 | 投标函 |
附件一 | 投标函 |
附件二 | 法定代表人身份证明 |
"""
    _chapter, items = extract_outline(text)
    assert [item.title for item in items] == ["投标函", "法定代表人身份证明"]


def test_extract_empty_when_no_format_section() -> None:
    text = "本项目交货期30天。投标有效期90天。质量要求合格。"
    chapter, items = extract_outline(text)
    assert chapter == ""
    assert items == []


def test_drops_template_fields_and_eligibility_clauses() -> None:
    text = """
第四部分 投标文件格式
附件一：投标函
附件二：竞标书
附件三：法定代表人身份证明
附件四：法定代表人授权委托书
附件五：投标承诺书
附件六：投标保证金
附件七：印鉴预留备案表
印件备案表（附件七）
附件八：投标报价单
附件九：商务偏离表
1.招标编号：ELHT-CL-2026-03-
2.招标单位：某市热电有限公司
3.投标人资格要求
（1）具有独立承担民事责任的能力；
（2）具有固定的经营场所；
第五部分 评标办法
"""
    _chapter, items = extract_outline(text)
    titles = [item.title for item in items]
    assert titles == [
        "投标函",
        "法定代表人身份证明",
        "法定代表人授权委托书",
        "投标承诺书",
        "投标保证金",
        "印鉴预留备案表",
        "投标报价单",
        "商务偏离表",
    ]
    joined = " ".join(titles)
    assert "招标编号" not in joined
    assert "招标单位" not in joined
    assert "资格要求" not in joined
    assert "民事责任" not in joined
    assert "经营场所" not in joined
    assert "印件备案表" not in titles


def test_extract_drops_invite_bond_rules_and_invalid_seal_clause() -> None:
    text = """
第四部分 投标文件格式
投标承诺函
二、投标保证金：2 万元
三、投标保证金 - 公司户汇款账号
九、评标结束后 10 个工作日内退还投标保证金
5.1 无单位和法定代表人（或法定委托代理人）的印鉴
法定代表人身份证明
报价单模板
第五部分 评标办法
"""
    _chapter, items = extract_outline(text)
    titles = [item.title for item in items]
    blob = compact_title("".join(titles))
    assert "2万元" not in blob and "2万" not in blob
    assert "账号" not in blob
    assert "评标结束" not in blob
    assert "退还" not in blob
    assert "无单位" not in blob
    assert any("承诺函" in t for t in titles)
    assert any("身份证明" in t for t in titles)
    assert classify_kind("投标保证金") == "scan"
    assert classify_kind("投标保证金：2 万元") == "unknown"
    assert is_outline_junk(title="投标保证金 - 公司户汇款账号")
    assert is_outline_junk(title="评标结束后10个工作日内退还投标保证金")
    assert is_outline_junk(title="无单位和法定代表人（或法定委托代理人）的印鉴")


def test_dedupe_keeps_one_seal_register() -> None:
    from api.services.tenders.schema import OutlineItem

    items = [
        OutlineItem(id="o01", title="印件备案表（附件七）", kind="company", source="copy"),
        OutlineItem(id="o02", title="印鉴预留备案表", kind="company", source="copy"),
        OutlineItem(id="o03", title="投标函", kind="letter", source="generate"),
    ]
    out = dedupe_outline_items(items)
    titles = [item.title for item in out]
    assert titles.count("印鉴预留备案表") == 1
    assert "印件备案表（附件七）" not in titles
    assert "投标函" in titles
    joined = " ".join(titles)
    assert "招标编号" not in joined
    assert "招标单位" not in joined
    assert "资格要求" not in joined
    assert "民事责任" not in joined
    assert "经营场所" not in joined


def test_dedupe_keeps_one_quote_form() -> None:
    items = [
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
        OutlineItem(
            id="o03",
            title="报价单",
            kind="quote",
            source="generate",
        ),
        OutlineItem(
            id="o04",
            title="报价单模板",
            kind="quote",
            source="copy",
            body="序号 | 项目内容 | 金额(元) | 备注\n1 | MOM平台总费用 |  |\n",
        ),
    ]
    out = [item for item in items if not is_outline_junk(item)]
    out = dedupe_outline_items(out)
    quotes = [item for item in out if item.kind == "quote"]
    assert len(quotes) == 1
    assert quotes[0].title in {"报价单", "报价单模板", "投标报价单"}
    assert "MOM平台总费用" in (quotes[0].body or "")
    assert not any("按照软件总体报价" in (item.title or "") for item in out)


def test_extract_drops_mashed_biz_dev_form_header() -> None:
    text = """
第四部分 投标文件格式
附件八：投标报价单
附件九：商务偏离表
1、商务偏离表招标项目：
序号 招标文件条目号
2、技术规格偏离表及建议招标项目：
序号 货物名称
"""
    _chapter, items = extract_outline(text)
    titles = [item.title for item in items]
    assert titles.count("商务偏离表") == 1
    assert "商务偏离表招标项目" not in titles
    assert "技术规格偏离表及建议" in titles
    assert "技术规格偏离表及建议招标项目" not in titles
    kinds = [item.kind for item in items]
    assert kinds.count("biz_dev") == 1
    assert kinds.count("tech_dev") == 1


def test_drops_contact_and_binding_instructions() -> None:
    text = """
投标文件格式
监督人：赵某13304781234
商务标：同步提供扫描版、可编辑版电子标书。
投标函（附件一）；
竞标书（附件二）；
法定代表人身份证明（附件三）；
法定代表人授权委托书（附件四）；
投标承诺书（附件五）；
招投标保证金/银行保函（附件六）；
印件备案表（附件七）；
商务、技术偏离及备品配件专用工具表（附件九）；
资质证明资料（附件十）
业绩证明资料（附合同原件或复印件）；
"""
    _chapter, items = extract_outline(text)
    titles = [item.title for item in items]
    joined = " ".join(titles)
    assert "监督人" not in joined
    assert "扫描版" not in joined
    assert "电子标书" not in joined
    assert "投标函（附件一）" in titles
    assert "竞标书（附件二）" not in titles
    assert "招投标保证金/银行保函（附件六）" in titles
    assert "资质证明资料（附件十）" in titles


def test_drops_running_headers_form_labels_and_letter_body() -> None:
    text = """
第四部分 投标文件格式
投标函（附件一）；
竞标书（附件二）；
法定代表人身份证明（附件三）；
法定代表人授权委托书（附件四）；
投标承诺书（附件五）；
招投标保证金/银行保函（附件六）；
商务、技术偏离及备品配件专用工具表（附件九）；
资质证明资料（附件十）
业绩证明资料（附合同原件或复印件）；
备品配件及专用工具表（选填）
纸质版标书一式三份（一正二副）
某市热电有限公司第四部分投标文件格式附件一
投标函有限公司
某市热电有限公司附件二
投标代理人（公章）
某市热电有限公司附件三
法定代表人身份证明投标人名称
系（投标人名称）的法定代表人
特此证明
年月日法人身份证复印件粘贴处
某市热电有限公司附件四
法定代表人授权委托书公司
授权期间
代理人身份证复印件粘贴处单位名称（章）
某市热电有限公司附件五
我公司现做出如下承诺
保证投标文件的报价不存在低于本项目成本的恶意报价行为
某市热电有限公司附件六
某市热电有限公司附件九
"""
    _chapter, items = extract_outline(text)
    titles = [item.title for item in items]
    joined = " ".join(titles)
    assert "一式三份" not in joined
    assert "某市热电有限公司附件" not in joined
    assert "投标文件格式附件" not in joined
    assert "投标函有限公司" not in titles
    assert "公章" not in joined
    assert "投标人名称" not in joined
    assert "特此证明" not in titles
    assert "粘贴处" not in joined
    assert "授权委托书公司" not in joined
    assert "授权期间" not in titles
    assert "如下承诺" not in joined
    assert "恶意报价" not in joined
    assert "投标函（附件一）" in titles
    assert "竞标书（附件二）" not in titles
    assert "商务、技术偏离及备品配件专用工具表（附件九）" in titles
    assert "资质证明资料（附件十）" in titles
    assert "备品配件及专用工具表（选填）" not in titles
    assert "投标承诺书（附件五）" in titles


def test_attach_form_body_when_title_has_attach_paren() -> None:
    text = """
第四部分 投标文件格式
投标函（附件一）
竞标书（附件二）
法定代表人授权委托书（附件四）
附件一：
投 标 函
________________有限公司：
我方已仔细阅读和研究了________________招标文件，决定参加本次投标。
投标人（章）：          法定代表人或授权代表（签字）：
年  月  日
附件四：
法定代表人授权委托书
本授权委托书声明：下列被授权人代表我方签署投标文件。
投标人（章）：
年  月  日
第五部分 评标办法
"""
    chapter, items = extract_outline(text)
    assert "投标文件格式" in chapter
    by_title = {item.title: item for item in items}
    letter = by_title["投标函（附件一）"]
    auth = by_title["法定代表人授权委托书（附件四）"]
    assert "阅读和研究了" in letter.body
    assert "投 标 函" in letter.body or "投标函" in letter.body.replace(" ", "")
    assert "本授权委托书声明" in auth.body
    assert "阅读和研究了" not in auth.body


def test_outline_fields_on_default_brief() -> None:
    from api.services.tenders.schema import default_brief

    brief = default_brief()
    assert brief.layoutMode == "chapter5"
    assert brief.outlineItems == []
    assert brief.outlineChapter == ""


def test_item_level_from_invitation_prefix() -> None:
    from api.services.tenders.outline import item_level

    assert item_level("一、响应函") == 1
    assert item_level("1.1 响应函") == 2
    assert item_level("1.2.1 分项报价表") == 3
    assert item_level("第一章 技术方案总述") == 2


def test_build_outline_toc_tree_groups_legal_and_qual() -> None:
    from api.services.tenders.format_rules import flatten_toc_tree
    from api.services.tenders.outline import build_outline_toc_tree
    from api.services.tenders.schema import OutlineItem

    items = [
        OutlineItem(id="o01", title="响应函", kind="letter"),
        OutlineItem(id="o02", title="响应函附录", kind="letter"),
        OutlineItem(id="o03", title="分项报价表", kind="quote"),
        OutlineItem(id="o04", title="法定代表人身份证明", kind="legal_id"),
        OutlineItem(id="o05", title="授权委托书", kind="auth"),
        OutlineItem(id="o06", title="营业执照", kind="scan"),
        OutlineItem(id="o07", title="代理授权书", kind="scan"),
        OutlineItem(id="o08", title="技术标（实施方案）", kind="tech_plan"),
    ]
    bms = [f"toc_{it.id}" for it in items]
    rows = flatten_toc_tree(build_outline_toc_tree(items, bms))
    labels = [f"{lab}{title}" for lab, title, _lv, _bm in rows]
    assert labels[0] == "一、响应函及响应函附录"
    assert "1.1 响应函" in labels
    assert "1.2.1 分项报价表" in labels
    assert "二、法定代表人身份证明及授权委托书" in labels
    assert "三、资格审查资料" in labels
    assert any(t.startswith("四、技术标") for t in labels)
    assert any("文字描述" in t for t in labels)


def test_nested_invitation_keeps_levels() -> None:
    from api.services.tenders.outline import extract_outline

    text = """
第五章 响应文件格式
一、响应函及响应函附录
1.1 响应函
1.2 响应函附录
1.2.1 分项报价表
二、法定代表人身份证明
"""
    _chapter, items = extract_outline(text)
    by = {item.title: item.level for item in items}
    assert by["响应函及响应函附录"] == 1
    assert by["响应函"] == 2
    assert by["响应函附录"] == 2
    assert by["分项报价表"] == 3
    assert by["法定代表人身份证明"] == 1


def test_attach_bodies_from_last_format_chapter_not_toc() -> None:
    text = """
目录
第四部分投标文件格式...................................................................7
附件五投标承诺书：.................................................................11
附件七：印鉴预留备案表.............................................................13
[第3页]
第一部分投标邀请
二连浩特市联源热电有限公司现对项目招标。
5、投标承诺书（附件五）；
7、印件备案表（附件七）；
[第8页]
第四部分投标文件格式附件一：
投标函有限公司：
我方已全面阅读和研究了招标文件。
[第12页]
附件五：
投标承诺书致：
我公司现做出如下承诺：
1、保证投标文件内容无任何虚假。若评标过程中发现虚假，同意作无效投标文件处理。
10、保证在施工期间因甲方资金暂时不到位的情况下，不发生停工。
投标人： （盖章）
日期： 年月日
[第13页]
二连浩特市联源热电有限公司
[第15页]
附件八：印鉴预留备案公司证件、印章备案表日期：
公司名称： 公司电话：
公章财务章合同章
备注：本表中三处备案印鉴为红色章。
"""
    chapter, items = extract_outline(text)
    assert "投标文件格式" in chapter
    by = {item.title: item for item in items}
    commit = by["投标承诺书"]
    assert "保证投标文件内容无任何虚假" in commit.body
    assert "现对项目招标" not in commit.body
    assert "印鉴预留备案" not in commit.body
    blob = "\n".join(ln.strip() for ln in commit.body.splitlines() if ln.strip())
    assert "投标承诺书" in blob
    assert any(ln.strip().startswith("致") for ln in commit.body.splitlines())
    assert not any("投标承诺书致" in compact_title(ln) for ln in commit.body.splitlines())
    assert "[第13页]" not in commit.body
    assert "[第15页]" not in commit.body
    assert "二连浩特市联源热电有限公司" not in commit.body
    seal = by["印鉴预留备案表"]
    assert "三处备案印鉴为红色章" in seal.body or "印鉴备案" in seal.body or "公章" in seal.body


def test_extract_fu_yi_commitment_keeps_invitation_clauses() -> None:
    text = """
第五章 投标文件格式
附一：投标承诺函模板
投标承诺函
致：河南鑫宇光科技股份有限公司
1、我方已详细研究了招标文件的所有内容，包括修正文（如果有）和所有已提供的参考资料以及有关附件，并完全明白，我方放弃在此方面提出含糊意见或误解的一切权利。
2、我方承诺投标文件夹中的一切资料、数据是真实的，并承担由此引起的一切后果和相应法律责任。
3、我方明白并同意若我方在投标有效期之内撤回投标，则投标保证金将被贵方没收。
4、我方理解贵方不一定接受最低标价或任何贵方可能收到的投标。
5、我方如果中标，将保证履行招标文件以及招标文件修改书中的全部责任和义务。
投标人(盖公章)：
日期： 年 月 日
第六章 合同条款
"""
    _chapter, items = extract_outline(text)
    commit = next(item for item in items if item.kind == "commitment_copy")
    assert "详细研究了招标文件" in commit.body
    assert "资料、数据是真实的" in commit.body
    assert "保证投标文件无虚假内容" not in commit.body
    assert "不采取停工、上访" not in commit.body


def test_items_for_volume_splits_and_drops_labels() -> None:
    brief = BidBrief(
        layoutMode="outline",
        outlineItems=[
            OutlineItem(id="a", title="商务标", kind="unknown"),
            OutlineItem(id="b", title="投标函", kind="letter"),
            OutlineItem(id="c", title="分项报价表", kind="quote"),
            OutlineItem(id="d", title="技术标", kind="tech_plan"),
            OutlineItem(id="e", title="技术偏差表", kind="tech_dev"),
            OutlineItem(id="f", title="实施方案", kind="tech_plan"),
        ],
    )
    biz = items_for_volume(brief, "business")
    tech = items_for_volume(brief, "technical")
    biz_kinds = [item.kind for item in biz]
    tech_kinds = [item.kind for item in tech]
    assert "letter" in biz_kinds
    assert "quote" in biz_kinds
    assert "tech_plan" not in biz_kinds
    assert "tech_dev" not in biz_kinds
    assert "quote" not in tech_kinds
    assert "letter" not in tech_kinds
    assert "tech_dev" in tech_kinds
    assert "tech_plan" in tech_kinds
    assert not any(is_volume_label(item.title) for item in biz + tech)


def test_extract_keeps_requirement_list_and_quote_template() -> None:
    text = """
第五章 投标文件格式
附一：投标承诺函模板
投标承诺函
致：河南鑫宇光科技股份有限公司
1、我方已详细研究了招标文件的所有内容。
2、我方承诺投标文件夹中的一切资料、数据是真实的。
投标人(盖公章)：
日期： 年 月 日
附二：招标产品功能需求清单及说明
一、MOM生产运营管理平台：
设备数采 1.多类型数据采集；2数据传输与存储功能
TPM系统 1.设备档案管理
附三：报价单模板
项目总报价：
序号 项目内容 金额(元) 备注
1 MOM平台总费用
2 总实施费用
3 年度服务费
4 合计
（1）数采报价
序号 项目内容 金额(元) 备注
1 软件费用
2 实施费用
3 合计(1+2)
（6）实施费用分项明细
第六章 合同条款
"""
    chapter, items = extract_outline(text)
    assert "投标文件格式" in chapter
    by = {item.title: item for item in items}
    assert "招标产品功能需求清单及说明" in by
    assert by["招标产品功能需求清单及说明"].kind == "unknown"
    assert by["招标产品功能需求清单及说明"].source == "copy"
    assert "设备数采" in (by["招标产品功能需求清单及说明"].body or "")
    assert "印鉴预留备案表" not in by
    quote = next(item for item in items if item.kind == "quote")
    assert "报价单" in quote.title
    assert quote.source == "copy"
    assert "MOM平台总费用" in (quote.body or "")
    assert "软件费用" in (quote.body or "")
    commit = next(item for item in items if item.kind == "commitment_copy")
    assert "详细研究了招标文件" in (commit.body or "")
    assert "MOM平台总费用" not in (commit.body or "")
    assert "设备数采" not in (commit.body or "")


def test_items_for_volume_outline_fills_biz_essentials() -> None:
    brief = BidBrief(
        layoutMode="outline",
        outlineItems=[
            OutlineItem(id="a", title="投标承诺函", kind="commitment_copy"),
            OutlineItem(id="b", title="招标产品功能需求清单及说明", kind="unknown"),
            OutlineItem(id="c", title="报价单模板", kind="quote"),
        ],
    )
    biz = items_for_volume(brief, "business")
    kinds = [item.kind for item in biz]
    titles = [item.title for item in biz]
    assert "letter" in kinds
    assert biz[0].kind == "letter"
    assert "legal_id" in kinds
    assert "biz_dev" in kinds
    assert "scan" in kinds
    assert any("营业执照" in t for t in titles)
    assert any("保证金" in t for t in titles)
    assert any("无违法" in t for t in titles)
    assert "quote" in kinds
    assert "commitment_copy" in kinds
    assert "unknown" in kinds
    assert "印鉴预留备案表" not in titles
    tech = items_for_volume(brief, "technical")
    assert not any(item.kind == "tech_dev" for item in tech)
    assert not any(item.kind == "tech_plan" for item in tech)


def test_ensure_biz_essentials_keeps_invitation_letter() -> None:
    items = [
        OutlineItem(id="o1", title="投标函", kind="letter", source="copy", body="致：甲方\n我方确认收到招标文件"),
        OutlineItem(id="o2", title="报价单", kind="quote", source="copy", body="序号 | 项目内容 |\n1 | MOM |"),
    ]
    out = ensure_biz_essentials(items)
    letter = next(item for item in out if item.kind == "letter")
    assert out[0].kind == "letter"
    assert "我方确认收到招标文件" in (letter.body or "")
    assert any(item.kind == "biz_dev" for item in out)
    assert any("营业执照" in item.title for item in out)
    assert any("无违法" in item.title for item in out)


def test_ensure_biz_essentials_pins_letter_first() -> None:
    items = [
        OutlineItem(id="o1", title="授权委托书", kind="auth"),
        OutlineItem(id="o2", title="投标函附录", kind="letter"),
        OutlineItem(id="o3", title="投标函", kind="letter", body="致：甲方"),
        OutlineItem(id="o4", title="报价单", kind="quote"),
    ]
    out = ensure_biz_essentials(items)
    assert out[0].kind == "letter"
    assert "附录" not in (out[0].title or "")
    assert out[1].kind == "letter"
    assert "附录" in (out[1].title or "")
    kinds = [item.kind for item in out]
    assert kinds.index("auth") < kinds.index("quote")


def test_ensure_biz_essentials_orders_business_pack() -> None:
    items = [
        OutlineItem(id="q", title="报价单", kind="quote"),
        OutlineItem(id="d", title="商务偏离表", kind="biz_dev"),
        OutlineItem(id="c", title="投标承诺函", kind="commitment_copy"),
        OutlineItem(id="p", title="类似项目业绩", kind="performance"),
        OutlineItem(id="a", title="授权委托书", kind="auth"),
        OutlineItem(id="l", title="法定代表人身份证明", kind="legal_id"),
        OutlineItem(id="s", title="营业执照", kind="scan"),
        OutlineItem(id="x", title="无单位和法定代表人的印鉴", kind="company"),
    ]
    out = ensure_biz_essentials(items)
    titles = [item.title for item in out]
    assert not any("无单位" in t and "印鉴" in t for t in titles)
    kinds = [item.kind for item in out]
    assert kinds[0] == "letter"
    assert kinds.index("commitment_copy") < kinds.index("legal_id") < kinds.index("auth")
    assert kinds.index("auth") < kinds.index("scan")
    assert kinds.index("scan") < kinds.index("performance") < kinds.index("quote") < kinds.index("biz_dev")


def test_extract_drops_disqualify_seal_clause() -> None:
    text = """
第四部分 投标文件格式
投标承诺函
无单位和法定代表人的印鉴
法定代表人身份证明
授权委托书
报价单模板
商务偏离表
第五部分 评标办法
"""
    _chapter, items = extract_outline(text)
    titles = [item.title for item in items]
    assert not any("无单位" in t and "印鉴" in t for t in titles)
    assert any("承诺函" in t for t in titles)
    assert any("身份证明" in t for t in titles)


def test_extract_skips_instruction_and_quote_debris() -> None:
    text = """
第五章 响应文件格式
供应商名称：与营业执照、资质证书一
技术标准和要求：满足第四章“技术标准及要求”规定，按照响应文件格式提供详细的技术文件
B.有效的企业营业执照、企业资质证书
二、合同条款响应书
三、商务和技术偏差表
四、响应方案
五、分项报价表
1. 分项报价表说明
2. 分项报价表单位：人民币元
序号 分项名称 单位 数量 单价（元） 总价（元） 备注
第六章 合同条款
"""
    _chapter, items = extract_outline(text)
    titles = [item.title for item in items]
    assert any("合同条款响应书" in t for t in titles)
    assert "分项报价表" in titles
    assert sum(1 for t in titles if "分项报价" in t) == 1
    assert not any("供应商名称" in t for t in titles)
    assert not any("技术标准" in t for t in titles)
    assert not any("人民币元" in t for t in titles)
    assert not any("报价表说明" in t for t in titles)
    assert not any("有效的企业营业执照" in t for t in titles)
    kinds = {item.title: item.kind for item in items}
    assert kinds.get("响应方案") == "tech_plan"
    quote = next(item for item in items if item.kind == "quote")
    assert "分项名称" in (quote.body or "")


def test_fill_copy_blanks_does_not_glue_date_into_section() -> None:
    from api.services.tenders.outline import fill_copy_blanks

    text = "日期： 年 月 日四、响应方案供应商参见询价采购文件"
    out = fill_copy_blanks(
        text,
        bidder="河南伟泰光电科技有限公司",
        project="数智化运营管理系统",
        tenderer="甲方",
        legal="",
        bid_date="2026-09-22",
    )
    assert "2026年09月22日四、" not in out.replace(" ", "")
    assert "四、响应方案" in out


def test_bid_item_title_strips_invite_attach_and_template() -> None:
    assert bid_item_title("附一：投标承诺函模板") == "投标承诺函"
    assert bid_item_title("附二：招标产品功能需求清单及说明") == "招标产品功能需求清单及说明"
    assert bid_item_title("报价单模板") == "报价单"
    assert bid_item_title("投标函（附件一）") == "投标函（附件一）"


def test_unmash_splits_quote_caption_from_header() -> None:
    out = unmash_invitation_text("（1）数采报价序号 | 项目内容 | 金额(元) | 备注")
    assert "（1）数采报价" in out
    assert out.splitlines()[1].startswith("序号 |")


def test_split_mashed_zhi_keeps_company_only_on_salute() -> None:
    parts = split_mashed_zhi_line(
        "致：河南鑫宇光科技股份有限公司我方确认收到贵方提供的招标文件，并重申以下几点："
    )
    assert parts[0] == "致：河南鑫宇光科技股份有限公司"
    assert parts[1].startswith("我方确认收到")


def test_unfold_sign_lines_keeps_pipe_table_row() -> None:
    row = (
        "TPM系统 | 1.设备档案管理； | 基础信息：设备编号、采购日期、单价；"
        "备件采购单价、供应商，备件出入库登记；录入供应商、校准周期。"
    )
    assert unfold_form_sign_lines(row) == row


def test_extract_mashed_fu_attach_keeps_letter_and_tables() -> None:
    text = """
鑫宇科技《MOM生产运营管理平台》项目招标文件
十一、附件附一、投标承诺函模板。
附二、招标产品功能需求清单及说明。
附三、报价单模板。
河南鑫宇光科技股份有限公司
2026年3月13日附一：投标承诺函模板投标承诺函致： 河南鑫宇光科技股份有限公司我方确认收到贵方提供的招标文件，并重申以下几点：
1、我方已详细研究了招标文件的所有内容，包括修正文(如果有)和所有已提供的参考资料以及有关附件。
2、我方承诺投标文件夹中的一切资料、数据是真实的。
投标人(盖公章)：
日期：     年月日附二：招标产品功能需求清单及说明一、MOM生产运营管理平台：
MOM | 设备数采 | 1.多类型数据采集；2.数据传输与存储功能；
MOM | TPM系统 | 1.设备档案管理；2. 设备台账与库存管理；
 | 硬件 | 单独报价，不记录在总价中。
二、重点需求明细包括（但不限于）：
模块 | 建设要求 | 实现目标数据采集 | 1.多类型数据采集； | 数据采集的基础，针对设备的运行状态。
TPM系统 | 1.设备档案管理； | 设备档案管理系统的数据基础。
附三：报价单模板
项目总报价：
序号 | 项目内容 | 金额(元) | 备注
1 | MOM平台总费用 |  |
2 | 总实施费用 |  |
"""
    chapter, items = extract_outline(text)
    commit = next(item for item in items if item.kind == "commitment_copy")
    assert "详细研究了招标文件" in (commit.body or "")
    assert "资料、数据是真实的" in (commit.body or "")
    assert "MOM平台总费用" not in (commit.body or "")
    req = next(item for item in items if "功能需求" in item.title)
    assert "设备数采" in (req.body or "")
    assert "硬件" in (req.body or "")
    assert "重点需求明细" in (req.body or "")
    assert "数据采集的基础" in (req.body or "")
    quote = next(item for item in items if item.kind == "quote")
    assert "MOM平台总费用" in (quote.body or "")
    assert "设备数采" not in (quote.body or "")


