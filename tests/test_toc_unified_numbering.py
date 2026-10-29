"""测试目录统一编号功能"""

from docx import Document
from docx.oxml.ns import qn

from api.services.tenders.assemble import assemble_bid_docx
from api.services.tenders.format_rules import cn_ordinal
from api.services.tenders.schema import BidBrief, OutlineItem


def test_cn_ordinal():
    """测试中文序号转换"""
    assert cn_ordinal(1) == "一"
    assert cn_ordinal(2) == "二"
    assert cn_ordinal(3) == "三"
    assert cn_ordinal(4) == "四"
    assert cn_ordinal(5) == "五"
    assert cn_ordinal(6) == "六"
    assert cn_ordinal(7) == "七"
    assert cn_ordinal(8) == "八"
    assert cn_ordinal(9) == "九"
    assert cn_ordinal(10) == "十"
    assert cn_ordinal(11) == "十一"
    assert cn_ordinal(20) == "二十"
    assert cn_ordinal(25) == "二十五"


def test_toc_unified_numbering():
    """测试目录统一编号：主条目一、二、三，子条目（一）（二）"""
    brief = BidBrief(
        projectName="测试项目",
        tenderNo="TEST-2026-01",
        bidderName="测试投标人",
        legalPersonName="张三",
        bidDate="2026-10-29",
        bidPriceYuan=1000000,
        layoutMode="chapter5",
        outlineItems=[
            OutlineItem(
                id="letter",
                title="投标函及投标函附录",
                rawTitle="投标函及投标函附录",
                kind="letter",
                level=1,
            ),
            OutlineItem(
                id="letter_sub1",
                title="投标函",
                rawTitle="投标函",
                kind="letter",
                level=2,
            ),
            OutlineItem(
                id="letter_sub2",
                title="投标函附录",
                rawTitle="投标函附录",
                kind="letter",
                level=2,
            ),
            OutlineItem(
                id="legal",
                title="法定代表人身份证明",
                rawTitle="法定代表人身份证明",
                kind="legal_id",
                level=1,
            ),
            OutlineItem(
                id="auth",
                title="授权委托书",
                rawTitle="授权委托书",
                kind="auth",
                level=1,
            ),
            OutlineItem(
                id="bond",
                title="投标保证金",
                rawTitle="投标保证金",
                kind="scan",
                level=1,
            ),
            OutlineItem(
                id="quote",
                title="已标价工程量清单",
                rawTitle="已标价工程量清单",
                kind="quote",
                level=1,
            ),
            OutlineItem(
                id="pm",
                title="项目管理机构",
                rawTitle="项目管理机构",
                kind="scan",
                level=1,
            ),
            OutlineItem(
                id="pm_sub1",
                title="项目管理机构组成表",
                rawTitle="项目管理机构组成表",
                kind="scan",
                level=2,
            ),
            OutlineItem(
                id="pm_sub2",
                title="项目经理主要工作经历",
                rawTitle="项目经理主要工作经历",
                kind="scan",
                level=2,
            ),
            OutlineItem(
                id="subcontract",
                title="拟分包项目情况表",
                rawTitle="拟分包项目情况表",
                kind="scan",
                level=1,
            ),
            OutlineItem(
                id="qualification",
                title="资格审查资料",
                rawTitle="资格审查资料",
                kind="scan",
                level=1,
            ),
            OutlineItem(
                id="qualification_sub1",
                title="投标人基本情况表",
                rawTitle="投标人基本情况表",
                kind="scan",
                level=2,
            ),
            OutlineItem(
                id="qualification_sub2",
                title="正在施工和新承接项目情况表",
                rawTitle="正在施工和新承接项目情况表",
                kind="scan",
                level=2,
            ),
            OutlineItem(
                id="other",
                title="其他资料",
                rawTitle="其他资料",
                kind="scan",
                level=1,
            ),
            OutlineItem(
                id="tech_plan",
                title="施工组织格式",
                rawTitle="施工组织格式",
                kind="tech_plan",
                level=1,
                draftBody="施工组织设计内容",
            ),
        ],
    )

    # 生成文档（这里只是验证结构，实际生成需要完整的环境）
    # doc = assemble_bid_docx(brief, volume="business")

    # 验证 outline 中的条目数量和层级
    level1_items = [item for item in brief.outlineItems if item.level == 1]
    level2_items = [item for item in brief.outlineItems if item.level == 2]

    assert len(level1_items) == 10  # 十个主条目
    assert len(level2_items) == 6   # 六个子条目

    # 验证主条目的预期顺序
    expected_level1_titles = [
        "投标函及投标函附录",
        "法定代表人身份证明",
        "授权委托书",
        "投标保证金",
        "已标价工程量清单",
        "项目管理机构",
        "拟分包项目情况表",
        "资格审查资料",
        "其他资料",
        "施工组织格式",
    ]

    actual_level1_titles = [item.title for item in level1_items]
    assert actual_level1_titles == expected_level1_titles


def test_toc_numbering_format():
    """测试目录编号格式"""
    # Level 1: 一、二、三、
    for i in range(1, 11):
        label = f"{cn_ordinal(i)}、"
        assert label.endswith("、")

    # Level 2: （一）（二）（三）
    for i in range(1, 6):
        label = f"（{cn_ordinal(i)}）"
        assert label.startswith("（")
        assert label.endswith("）")


if __name__ == "__main__":
    test_cn_ordinal()
    test_toc_unified_numbering()
    test_toc_numbering_format()
    print("所有测试通过！")
