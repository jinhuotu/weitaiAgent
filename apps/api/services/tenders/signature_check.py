# -*- coding: utf-8 -*-
"""
投标文件签章位校验与采购政策特殊标记识别。

关键签章位：
- 投标人盖章
- 法定代表人/委托代理人签字
- 日期

采购政策标记：
- 强制采购节能产品 ★
- 环境标志产品
- 本国产品要求
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SEAL_MARKS = (
    "投标人（盖章）",
    "投标人盖章",
    "（盖章）",
    "单位（盖章）",
    "单位盖章",
)

_SIGNATURE_MARKS = (
    "法定代表人（签字）",
    "法定代表人签字",
    "委托代理人（签字）",
    "委托代理人签字",
    "项目负责人（签字）",
    "（签字）",
)

_DATE_MARKS = (
    "日期",
    "年  月  日",
    "____年____月____日",
)

_ENERGY_MARKS = (
    "强制采购节能产品",
    "节能产品认证证书",
    "节能清单产品",
)

_ENV_MARKS = (
    "环境标志产品",
    "环保产品认证",
    "十环认证",
)

_DOMESTIC_MARKS = (
    "本国产品",
    "国内产品",
    "中国产品标准",
)


@dataclass
class SignatureCheckResult:
    """签章校验结果"""
    has_bidder_seal: bool = False
    has_legal_signature: bool = False
    has_date: bool = False
    missing_positions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ProcurementPolicy:
    """采购政策要求"""
    requires_energy_cert: bool = False
    requires_env_cert: bool = False
    requires_domestic_declaration: bool = False
    energy_products: list[str] = field(default_factory=list)
    env_products: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def check_signature_positions(text: str) -> SignatureCheckResult:
    """
    校验文档中是否包含关键签章位。

    Args:
        text: 文档全文或章节文本

    Returns:
        SignatureCheckResult 包含签章位检测结果
    """
    result = SignatureCheckResult()

    # 检查投标人盖章
    result.has_bidder_seal = any(mark in text for mark in _SEAL_MARKS)

    # 检查法定代表人/委托代理人签字
    result.has_legal_signature = any(mark in text for mark in _SIGNATURE_MARKS)

    # 检查日期
    result.has_date = any(mark in text for mark in _DATE_MARKS)

    # 收集缺失项
    if not result.has_bidder_seal:
        result.missing_positions.append("投标人盖章")
        result.warnings.append("【待签章】缺少投标人盖章签章位，请补充否则可能导致废标")

    if not result.has_legal_signature:
        result.missing_positions.append("法定代表人/委托代理人签字")
        result.warnings.append("【待签章】缺少法定代表人或委托代理人签字，请补充")

    if not result.has_date:
        result.missing_positions.append("日期")
        result.warnings.append("【待签章】缺少日期位，请补充")

    return result


def extract_procurement_policy(invitation: str) -> ProcurementPolicy:
    """
    从招标文件中提取采购政策要求。

    Args:
        invitation: 招标文件全文

    Returns:
        ProcurementPolicy 包含采购政策要求
    """
    policy = ProcurementPolicy()
    compact = re.sub(r"\s+", "", invitation or "")

    # 检查强制节能产品要求
    if any(mark in compact for mark in _ENERGY_MARKS):
        policy.requires_energy_cert = True
        policy.notes.append(
            "★强制采购节能产品：须提供节能产品认证证书，否则无效投标"
        )
        # 提取具体节能产品名称
        for line in invitation.splitlines():
            if any(mark in line for mark in _ENERGY_MARKS):
                # 尝试提取产品名称
                products = re.findall(r"([A-Za-z一-龥]+(?:设备|产品|系统))", line)
                policy.energy_products.extend(products[:3])

    # 检查环境标志产品要求
    if any(mark in compact for mark in _ENV_MARKS):
        policy.requires_env_cert = True
        policy.notes.append(
            "环境标志产品：须提供环境标志产品认证证书（十环认证）"
        )

    # 检查本国产品要求
    if any(mark in compact for mark in _DOMESTIC_MARKS):
        policy.requires_domestic_declaration = True
        policy.notes.append(
            "本国产品要求：须附《关于符合本国产品标准的声明函》"
        )

    return policy


def add_signature_reminders(text: str) -> str:
    """
    在文档正文中补充签章提醒标注。

    Args:
        text: 原始文档正文

    Returns:
        补充了签章提醒的文档正文
    """
    result = check_signature_positions(text)

    if not result.warnings:
        return text

    # 在文档末尾添加签章提醒
    reminders = "\n\n" + "="*60 + "\n"
    reminders += "【签章提醒】以下位置需在最终投标文件中补充签章：\n"
    for warning in result.warnings:
        reminders += f"  • {warning}\n"
    reminders += "="*60 + "\n"

    return text + reminders


def format_policy_requirements(policy: ProcurementPolicy) -> str:
    """
    格式化采购政策要求为可读文本。

    Args:
        policy: 采购政策对象

    Returns:
        格式化的政策要求文本
    """
    if not policy.notes:
        return ""

    lines = ["【采购政策要求】"]
    for note in policy.notes:
        lines.append(f"  {note}")

    if policy.energy_products:
        lines.append(f"  涉及节能产品：{', '.join(set(policy.energy_products))}")

    return "\n".join(lines)
