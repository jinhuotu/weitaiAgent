"""测试目录编号功能"""
from docx import Document
from docx.oxml.ns import qn
from api.services.tenders.native_format import _toc_para_level, _read_toc_entries
from api.services.tenders.schema import BidBrief, OutlineItem
from lxml import etree


def test_toc_para_level_reads_numpr():
    """测试 _toc_para_level 能正确读取 numPr/ilvl"""
    # 创建测试文档
    doc = Document()
    para = doc.add_paragraph("测试条目")

    # 添加 numPr 编号信息
    p_pr = para._element.get_or_add_pPr()
    num_pr = etree.SubElement(p_pr, qn("w:numPr"))
    ilvl = etree.SubElement(num_pr, qn("w:ilvl"))
    ilvl.set(qn("w:val"), "0")  # ilvl=0 应该对应 level=1

    # 测试读取 ilvl
    level = _toc_para_level(para._element, "测试条目")
    assert level == 1, f"Expected level 1, got {level}"

    # 测试 ilvl=1 对应 level=2
    ilvl.set(qn("w:val"), "1")
    level = _toc_para_level(para._element, "测试条目")
    assert level == 2, f"Expected level 2, got {level}"

    # 测试 ilvl=2 对应 level=3
    ilvl.set(qn("w:val"), "2")
    level = _toc_para_level(para._element, "测试条目")
    assert level == 3, f"Expected level 3, got {level}"

    print("✓ _toc_para_level 正确读取 numPr/ilvl")


def test_read_toc_entries():
    """测试读取目录条目"""
    doc = Document()

    # 添加目录标题
    toc_title = doc.add_paragraph("目录")

    # 添加主条目（ilvl=0）
    for i, title in enumerate([
        "投标函附录",
        "法定代表人身份证明",
        "授权委托书",
        "投标保证金",
        "已标价工程量清单",
        "项目管理机构",
        "拟分包项目情况表",
        "资格审查资料",
        "其他资料",
        "施工组织格式"
    ]):
        para = doc.add_paragraph(title)
        p_pr = para._element.get_or_add_pPr()
        num_pr = etree.SubElement(p_pr, qn("w:numPr"))
        ilvl = etree.SubElement(num_pr, qn("w:ilvl"))
        ilvl.set(qn("w:val"), "0")

    # 添加子条目（ilvl=1）
    for title in ["项目管理机构组成表", "项目经理主要工作经历"]:
        para = doc.add_paragraph(title)
        p_pr = para._element.get_or_add_pPr()
        num_pr = etree.SubElement(p_pr, qn("w:numPr"))
        ilvl = etree.SubElement(num_pr, qn("w:ilvl"))
        ilvl.set(qn("w:val"), "1")

    # 构建 block（目录标题 + 所有条目）
    block = [para._element for para in doc.paragraphs]

    # 读取目录条目
    entries = _read_toc_entries(block)

    # 验证读取结果
    print(f"读取到 {len(entries)} 条目录条目")
    for entry in entries:
        print(f"  level={entry['level']}, title={entry['title']}")

    # 验证主条目
    main_entries = [e for e in entries if e['level'] == 1]
    assert len(main_entries) == 10, f"Expected 10 main entries, got {len(main_entries)}"

    # 验证子条目
    sub_entries = [e for e in entries if e['level'] == 2]
    assert len(sub_entries) == 2, f"Expected 2 sub entries, got {len(sub_entries)}"

    print("✓ _read_toc_entries 正确读取所有条目")


if __name__ == "__main__":
    test_toc_para_level_reads_numpr()
    test_read_toc_entries()
    print("\n所有测试通过！")
