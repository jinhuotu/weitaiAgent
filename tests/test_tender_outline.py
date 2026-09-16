"""组卷大纲抽取（合成正文，不依赖某份真实招标书）。"""

from __future__ import annotations

from api.services.tenders.categories import is_volume_label, item_volume
from api.services.tenders.outline import (
    classify_kind,
    compact_title,
    dedupe_outline_items,
    extract_outline,
    is_seal_register,
    items_for_volume,
    source_for_kind,
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
    assert classify_kind("商务标") == "unknown"
    assert is_volume_label("商务标")
    assert is_volume_label("技术标")
    assert item_volume(kind="quote", title="分项报价表") == "business"
    assert item_volume(kind="tech_plan", title="实施方案") == "technical"
    assert classify_kind("营业执照") == "scan"
    assert classify_kind("企业资质") == "scan"
    assert classify_kind("企业资质文件") == "scan"
    assert classify_kind("直接采购文件税务信息表") == "company"
    assert classify_kind("无意义标题甲乙丙") == "unknown"
    assert source_for_kind("letter") == "generate"
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
    assert kinds["报价表"] == "quote"
    assert kinds["报价清单"] == "quote"
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
