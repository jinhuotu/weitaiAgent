"""
测试投标文件特殊标记识别与生成约束功能
"""

from api.services.tenders.classifier import (
    _has_veto,
    _has_important,
    _requirement_strength,
)
from api.services.tenders.signature_check import (
    check_signature_positions,
    extract_procurement_policy,
    add_signature_reminders,
    format_policy_requirements,
)


def test_three_level_marks():
    """测试三级条款标记识别"""
    # mandatory 级别
    assert _has_veto("★ 投标人必须具备XXX资质，否则废标")
    assert _has_veto("※ 实质性要求：不得负偏离")
    assert _has_veto("实质性条款：必须满足")
    assert _requirement_strength("★ 必须响应") == "mandatory"

    # important 级别
    assert _has_important("▲ 重要技术参数：负偏离扣分")
    assert _has_important("▲ 重要性条款")
    assert _requirement_strength("▲ 重要商务条款") == "important"

    # general 级别
    assert _requirement_strength("一般技术要求") == "general"
    assert _requirement_strength("投标人提供技术方案") == "general"

    print("✓ 三级条款标记识别测试通过")


def test_signature_check():
    """测试签章校验"""
    # 完整签章文档
    full_text = """
    投标文件
    投标人（盖章）：某某公司
    法定代表人（签字）：张三
    日期：2024年10月28日
    """
    result = check_signature_positions(full_text)
    assert result.has_bidder_seal
    assert result.has_legal_signature
    assert result.has_date
    assert len(result.missing_positions) == 0

    # 缺少签章文档
    incomplete_text = """
    投标文件
    某某公司技术方案
    """
    result2 = check_signature_positions(incomplete_text)
    assert not result2.has_bidder_seal
    assert not result2.has_legal_signature
    assert not result2.has_date
    assert len(result2.missing_positions) == 3
    assert len(result2.warnings) == 3

    print("✓ 签章校验测试通过")


def test_procurement_policy():
    """测试采购政策识别"""
    # 强制节能产品
    invitation1 = """
    本项目涉及强制采购节能产品，投标人须提供节能产品认证证书。
    空调设备、照明产品必须为节能清单产品。
    """
    policy1 = extract_procurement_policy(invitation1)
    assert policy1.requires_energy_cert
    assert len(policy1.notes) > 0
    assert "节能产品认证证书" in policy1.notes[0]

    # 环境标志产品
    invitation2 = """
    本项目要求投标产品须为环境标志产品，提供十环认证证书。
    """
    policy2 = extract_procurement_policy(invitation2)
    assert policy2.requires_env_cert

    # 本国产品
    invitation3 = """
    投标人须符合本国产品标准，提交《关于符合本国产品标准的声明函》。
    """
    policy3 = extract_procurement_policy(invitation3)
    assert policy3.requires_domestic_declaration

    # 格式化输出
    formatted = format_policy_requirements(policy1)
    assert "【采购政策要求】" in formatted

    print("✓ 采购政策识别测试通过")


def test_signature_reminders():
    """测试签章提醒"""
    text = "投标文件正文内容"
    reminded = add_signature_reminders(text)
    assert "【签章提醒】" in reminded
    assert "待签章" in reminded

    print("✓ 签章提醒测试通过")


if __name__ == "__main__":
    test_three_level_marks()
    test_signature_check()
    test_procurement_policy()
    test_signature_reminders()
    print("\n所有测试通过！")
