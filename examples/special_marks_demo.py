"""
投标文件特殊标记功能集成示例

展示如何在生成投标文件时集成：
1. 三级条款识别
2. 签章校验
3. 采购政策识别
4. 暗标格式约束
"""

from docx import Document
from api.services.tenders.schema import BidBrief
from api.services.tenders.classifier import _requirement_strength
from api.services.tenders.signature_check import (
    check_signature_positions,
    extract_procurement_policy,
    add_signature_reminders,
    format_policy_requirements,
)
from api.services.tenders.format_rules import apply_anonymous_tech_format


def process_bid_with_special_marks(
    brief: BidBrief,
    invitation_text: str,
    doc_path: str,
    is_anonymous: bool = False
) -> dict:
    """
    处理投标文件，应用特殊标记识别与约束。

    Args:
        brief: 投标简要信息
        invitation_text: 招标文件全文
        doc_path: Word 文档路径
        is_anonymous: 是否为暗标

    Returns:
        处理结果字典，包含警告、政策要求等
    """
    result = {
        "requirements": [],
        "mandatory_count": 0,
        "important_count": 0,
        "general_count": 0,
        "signature_warnings": [],
        "procurement_policy": {},
        "format_notes": [],
    }

    # 1. 三级条款识别与统计
    print("1️⃣  识别三级条款标记...")
    for req in brief.bidRequirements:
        strength = _requirement_strength(req.text or "")
        result["requirements"].append({
            "text": req.text,
            "strength": strength,
            "volume": req.volume,
            "category": req.category,
        })
        if strength == "mandatory":
            result["mandatory_count"] += 1
        elif strength == "important":
            result["important_count"] += 1
        else:
            result["general_count"] += 1

    print(f"   ✓ 必须响应项（★）: {result['mandatory_count']}")
    print(f"   ✓ 重要响应项（▲）: {result['important_count']}")
    print(f"   ✓ 一般要求: {result['general_count']}")

    # 2. 签章校验
    print("\n2️⃣  校验签章位...")
    doc = Document(doc_path)
    full_text = "\n".join(para.text for para in doc.paragraphs)
    sig_check = check_signature_positions(full_text)

    if sig_check.missing_positions:
        print(f"   ⚠️  缺少签章位: {', '.join(sig_check.missing_positions)}")
        result["signature_warnings"] = sig_check.warnings
        # 添加签章提醒到文档
        reminded_text = add_signature_reminders(full_text)
        # 这里可以将提醒添加到文档末尾或作为独立提示
    else:
        print("   ✓ 签章位完整")

    # 3. 采购政策识别
    print("\n3️⃣  识别采购政策...")
    policy = extract_procurement_policy(invitation_text)

    if policy.requires_energy_cert:
        print("   ⚠️  要求节能产品认证")
        result["procurement_policy"]["energy"] = {
            "required": True,
            "products": policy.energy_products,
        }

    if policy.requires_env_cert:
        print("   ⚠️  要求环境标志认证（十环）")
        result["procurement_policy"]["environment"] = True

    if policy.requires_domestic_declaration:
        print("   ⚠️  要求本国产品声明")
        result["procurement_policy"]["domestic"] = True

    if policy.notes:
        formatted_policy = format_policy_requirements(policy)
        print(f"\n{formatted_policy}")
        result["procurement_policy"]["notes"] = policy.notes

    # 4. 暗标格式约束（仅技术标暗标）
    if is_anonymous:
        print("\n4️⃣  应用暗标硬格式...")
        format_notes = apply_anonymous_tech_format(doc, brief, line_spacing_pt=28.0)
        result["format_notes"] = format_notes
        for note in format_notes:
            print(f"   ✓ {note}")
        # 保存应用格式后的文档
        anon_path = doc_path.replace(".docx", "_anonymous.docx")
        doc.save(anon_path)
        print(f"   ✓ 暗标文档已保存: {anon_path}")

    return result


def print_summary(result: dict):
    """打印处理结果摘要"""
    print("\n" + "="*60)
    print("【投标文件特殊标记处理摘要】")
    print("="*60)

    # 条款统计
    print(f"\n条款统计:")
    print(f"  ★ 必须响应项: {result['mandatory_count']} 条（负偏离=废标）")
    print(f"  ▲ 重要响应项: {result['important_count']} 条（负偏离扣分）")
    print(f"  □ 一般要求: {result['general_count']} 条")

    # 签章警告
    if result["signature_warnings"]:
        print(f"\n签章提醒:")
        for warning in result["signature_warnings"]:
            print(f"  ⚠️  {warning}")

    # 采购政策
    policy = result.get("procurement_policy", {})
    if policy:
        print(f"\n采购政策要求:")
        if policy.get("energy"):
            products = policy["energy"].get("products", [])
            print(f"  ★ 节能产品: {', '.join(products) if products else '需认证'}")
        if policy.get("environment"):
            print(f"  ★ 环境标志: 需十环认证")
        if policy.get("domestic"):
            print(f"  ★ 本国产品: 需声明函")

    # 格式说明
    if result["format_notes"]:
        print(f"\n暗标格式:")
        for note in result["format_notes"]:
            print(f"  ✓ {note}")

    print("\n" + "="*60)


# 使用示例
if __name__ == "__main__":
    # 模拟数据
    from api.services.tenders.schema import BidRequirement

    brief = BidBrief(
        projectName="某市政府办公设备采购项目",
        tenderNo="XM-2024-001",
        bidRequirements=[
            BidRequirement(
                text="★ 投标人必须具备ISO9001质量管理体系认证，否则无效投标",
                volume="business",
                category="qualification",
            ),
            BidRequirement(
                text="▲ 重要技术参数：CPU主频不低于2.5GHz，负偏离扣5分",
                volume="technical",
                category="technical",
            ),
            BidRequirement(
                text="投标人应提供售后服务方案",
                volume="technical",
                category="technical",
            ),
        ],
        bidderName="某某科技有限公司",
        legalPersonName="张三",
        bidDate="2024年10月28日",
    )

    invitation_text = """
    某市政府办公设备采购项目招标文件

    一、项目概况
    本项目采购办公电脑及配套设备。

    二、资格要求
    ★ 投标人必须具备ISO9001质量管理体系认证，否则无效投标。

    三、技术要求
    ▲ 重要技术参数：
    - CPU主频不低于2.5GHz，负偏离扣5分
    - 内存不小于8GB，负偏离扣3分

    四、采购政策
    本项目涉及强制采购节能产品，投标产品须为节能清单产品。
    投标人须提供节能产品认证证书，否则无效投标。
    涉及产品：台式电脑、显示器、空调设备。

    本项目要求投标产品符合本国产品标准，须附《关于符合本国产品标准的声明函》。

    五、暗标要求
    技术标采用暗标方式，全文不得出现投标人名称、标识及人员信息。
    格式要求：宋体四号、行距28磅、首行缩进2字符。
    """

    # 创建模拟文档（实际使用中替换为真实文档路径）
    print("处理投标文件特殊标记...\n")

    # 技术标（暗标）
    result = process_bid_with_special_marks(
        brief=brief,
        invitation_text=invitation_text,
        doc_path="technical_bid.docx",
        is_anonymous=True
    )

    print_summary(result)
