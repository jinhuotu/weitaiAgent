"""Two-axis value policy for tender filling.

The decision uses provenance first and the field's semantic kind second.  It
does not allow an invitation source to invent entity facts.
"""

from __future__ import annotations

from collections.abc import Iterable


SOURCE_POLICY: tuple[tuple[str, str], ...] = (
    ("invitation:", "A"),
    ("license:", "B"),
    ("id:", "B"),
    ("qual:", "B"),
    ("finance:", "B"),
)

_ENTITY_KEYWORDS = (
    "姓名", "性别", "年龄", "职务", "身份证", "职称", "证书号", "项目经理", "负责人", "法人", "代表",
    "公司", "企业", "单位", "住所", "地址", "电话", "传真", "网址", "邮编", "成立", "营业期限",
    "注册资本", "信用代码", "资质", "业绩", "财务", "净资产", "营收",
)
_CONVENTION_KEYWORDS = (
    "工期", "质量", "范围", "内容", "地点", "保证金", "有效期", "评分", "标准", "要求", "规模", "概况", "资金", "标段",
)


def field_kind(field: str) -> str:
    """Return ``实体类`` or ``约定类`` using conservative keyword matching."""

    value = str(field or "")
    if any(keyword in value for keyword in _CONVENTION_KEYWORDS):
        return "约定类"
    if any(keyword in value for keyword in _ENTITY_KEYWORDS):
        return "实体类"
    return "实体类"


def classify_value(field: str, source: str) -> str:
    """Return ``A`` / ``B`` / ``C`` from source and field kind.

    Invitation provenance is A only for convention fields; entity fields are
    C because an invitation cannot establish company or personnel facts.
    License, identity, qualification and finance provenance is B for either
    field kind. Empty or unregistered provenance is C.
    """

    prefix = (source or "").strip().lower()
    if not prefix:
        return "C"
    for marker, category in SOURCE_POLICY:
        if prefix.startswith(marker):
            if category == "A":
                return "A" if field_kind(field) == "约定类" else "C"
            return "B"
    return "C"


def policy_counts(items: Iterable[tuple[str, str]]) -> dict[str, int]:
    counts = {"A": 0, "B": 0, "C": 0}
    for field, source in items:
        counts[classify_value(field, source)] += 1
    return counts


def should_fill(field: str, source: str, value: object) -> bool:
    return bool(str(value or "").strip()) and classify_value(field, source) in {"A", "B"}

