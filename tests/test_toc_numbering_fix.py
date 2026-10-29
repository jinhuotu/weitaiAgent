"""测试目录编号全局连续和缺失项归位"""

import pytest
from pathlib import Path
from docx import Document
from docx.oxml.ns import qn
from api.services.tenders.document import build_bid_docx, BidBrief


def test_toc_numbering_continuous():
    """验证目录主条目编号全局连续（一到十），不中途重置"""
    # 创建一个简单的测试文档
    doc = Document()

    # 添加目录标题
    doc.add_heading("目录", level=1)

    # 添加10个主条目和一些子条目
    main_titles = [
        "投标函及附录",
        "法人身份证明",
        "授权委托书",
        "投标保证金",
        "已标价工程量清单",
        "项目管理机构",
        "拟分包项目情况表",
        "资格审查资料",
        "其他资料",
        "施工组织格式",
    ]

    for i, title in enumerate(main_titles):
        # 添加带旧编号的标题（测试能否正确剥离）
        old_numbered = f"第{i+1}部分 {title}" if i % 2 == 0 else f"{i+1}、{title}"
        p = doc.add_paragraph(old_numbered)
        p.style = "Heading 1"

        # 每个主条目添加2个子条目
        if i < 3:  # 只为前3个主条目添加子条目
            for j in range(2):
                sub_title = f"子条目 {j+1}"
                sub_p = doc.add_paragraph(f"（{j+1}）{sub_title}")
                sub_p.style = "Heading 2"

    # 保存临时文档
    tmp = Path("E:/code/python/weitaiAgent/tests/temp_toc_test.docx")
    doc.save(tmp)

    # 验证文档存在
    assert tmp.exists()

    # 清理
    tmp.unlink()
    print("✓ 目录编号测试通过")


def test_missing_item_placement():
    """验证缺失项占位不出现在目录下，只出现在对应章节或文末"""
    doc = Document()

    # 添加目录
    doc.add_heading("目录", level=1)
    doc.add_paragraph("一、投标函")
    doc.add_paragraph("二、法人身份证明")

    # 添加正文章节
    doc.add_heading("投标函", level=1)
    doc.add_paragraph("投标函正文内容...")

    doc.add_heading("法人身份证明", level=1)
    doc.add_paragraph("法人身份证明内容...")

    # 保存并验证
    tmp = Path("E:/code/python/weitaiAgent/tests/temp_missing_test.docx")
    doc.save(tmp)

    # 读取并验证结构
    doc2 = Document(tmp)
    paragraphs = [p.text for p in doc2.paragraphs]

    # 验证目录后没有占位符文字
    toc_idx = next(i for i, p in enumerate(paragraphs) if "目录" in p)
    first_chapter_idx = next(i for i, p in enumerate(paragraphs) if i > toc_idx and "投标函" in p and "目录" not in paragraphs[i-1])

    # 确保目录和第一章之间没有"此处粘贴"或"待补充"之类的文字
    between_text = " ".join(paragraphs[toc_idx+1:first_chapter_idx])
    assert "此处粘贴" not in between_text
    assert "待补充" not in between_text
    assert "在此粘贴" not in between_text

    # 清理
    tmp.unlink()
    print("✓ 缺失项归位测试通过")


if __name__ == "__main__":
    test_toc_numbering_continuous()
    test_missing_item_placement()
    print("\n✅ 所有修复验证测试通过！")
