"""组卷大纲抽取（合成正文，不依赖某份真实招标书）。"""

from __future__ import annotations

from api.services.tenders.outline import classify_kind, extract_outline, source_for_kind


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
    assert classify_kind("商务条款偏离表") == "biz_dev"
    assert classify_kind("技术规范书偏离表") == "tech_dev"
    assert classify_kind("知识产权不侵权承诺函") == "commitment_copy"
    assert classify_kind("投标承诺书") == "commitment_copy"
    assert classify_kind("企业业绩") == "performance"
    assert classify_kind("原厂生产承诺") == "factory"
    assert classify_kind("技术标（实施方案）") == "tech_plan"
    assert classify_kind("营业执照") == "scan"
    assert classify_kind("直接采购文件税务信息表") == "company"
    assert classify_kind("无意义标题甲乙丙") == "unknown"
    assert source_for_kind("letter") == "generate"
    assert source_for_kind("commitment_copy") == "copy"
    assert source_for_kind("letter", skipped=True) == "skip"


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
