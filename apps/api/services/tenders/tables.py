"""报价表 / 偏离表表头：从招标书格式章或清单抽取，不用 LLM。"""

from __future__ import annotations

import re
from dataclasses import dataclass

from api.services.tenders.schema import BidBrief, OutlineItem

_NORM = re.compile(r"[\s/／（）()【】\[\]:：]")

CHARGER_QUOTE_HEADERS: tuple[str, ...] = (
    "序号",
    "设备",
    "技术参数要求",
    "单位",
    "数量",
    "不含税综合单价（元）",
    "合价",
)
GENERIC_QUOTE_HEADERS: tuple[str, ...] = (
    "序号",
    "名称",
    "规格型号",
    "单位",
    "数量",
    "单价（元）",
    "合价",
)
GENERIC_DEV_HEADERS: tuple[str, ...] = (
    "序号",
    "招标文件要求",
    "投标文件响应",
    "偏差",
)

_WIDTH = {
    "seq": 8,
    "group": 16,
    "name": 22,
    "spec": 40,
    "unit": 8,
    "qty": 10,
    "price": 16,
    "amount": 16,
    "requirement": 36,
    "response": 36,
    "deviation": 12,
    "extra": 14,
    "skip": 10,
}


def compact_header(text: object) -> str:
    return _NORM.sub("", str(text or "")).lower()


@dataclass(frozen=True)
class HeaderLayout:
    titles: tuple[str, ...]
    roles: tuple[str, ...]


def quote_role(cell: object) -> str | None:
    n = compact_header(cell)
    if not n:
        return None
    if n in {"序号", "编号", "no", "num"} or n.startswith("序号"):
        return "seq"
    if any(
        k in n
        for k in (
            "技术参数",
            "技术要求",
            "参数要求",
            "规格型号",
            "规格参数",
            "特征描述",
            "工作内容",
            "设备参数",
            "配置要求",
        )
    ):
        return "spec"
    if n in {"规格", "参数", "明细", "功率"} or n.endswith("明细"):
        return "spec"
    if n.startswith("额定功率"):
        return "spec"
    if "参数" in n and "单价" not in n and "合价" not in n:
        return "spec"
    if any(k in n for k in ("适用场景", "应用场景", "使用场景")):
        return "scene"
    if n in {"单位", "计量单位"} or (n.endswith("单位") and "招标" not in n and "采购" not in n):
        return "unit"
    if any(k in n for k in ("工程量", "数量")):
        return "qty"
    if any(k in n for k in ("成本价", "成本单价", "进货价", "采购价")):
        return "cost_price"
    if any(k in n for k in ("指导售价", "销售单价", "报价单价", "投标单价")):
        return "sell_price"
    if any(k in n for k in ("综合单价", "不含税单价", "参考单价", "市场均价", "均价")) and "区间" not in n:
        return "price"
    if "单价" in n and "区间" not in n:
        return "price"
    if "合价" in n or (n in {"金额"} or n.endswith("金额")):
        return "amount"
    if any(k in n for k in ("功能模块", "模块名称")) or n == "模块":
        return "name"
    if "子系统" in n:
        return "group"
    if n in {"系统", "类别", "分类"} or n.endswith("系统名称") or n.endswith("类别"):
        return "group"
    if n.endswith("系统") and "系数" not in n and "配置" not in n:
        return "group"
    if any(
        k in n
        for k in (
            "设备名称",
            "设备型号",
            "产品名称",
            "产品型号",
            "项目名称",
            "货物名称",
            "物料名称",
            "品名",
            "服务内容",
        )
    ):
        return "name"
    if n in {"设备", "名称", "项目", "货物", "物料"}:
        return "name"
    return None


def dev_role(cell: object) -> str | None:
    n = compact_header(cell)
    if not n:
        return None
    if n in {"序号", "编号", "no"} or n.startswith("序号"):
        return "seq"
    if any(k in n for k in ("偏差说明", "偏离说明", "偏离情况", "偏差情况")):
        return "deviation"
    if n in {"偏差", "偏离"} or n.endswith("偏差") or n.endswith("偏离"):
        return "deviation"
    if any(
        k in n
        for k in (
            "投标文件响应",
            "投标响应",
            "响应内容",
            "响应情况",
            "响应条款",
            "供应商响应",
        )
    ):
        return "response"
    if any(
        k in n
        for k in (
            "招标文件要求",
            "招标要求",
            "招标文件规定",
            "条款内容",
            "商务条款",
            "技术要求",
            "规范要求",
            "采购要求",
        )
    ):
        return "requirement"
    if "响应" in n:
        return "response"
    if "要求" in n or "规定" in n or "条款" in n:
        return "requirement"
    return None


def classify_quote_headers(cells: list[object]) -> HeaderLayout | None:
    titles = _trim_titles(cells)
    if len(titles) < 2:
        return None
    roles = [quote_role(title) or "extra" for title in titles]
    if "name" not in roles:
        for i in range(len(roles) - 1, -1, -1):
            if roles[i] == "group":
                roles[i] = "name"
                break
    if "name" not in roles:
        return None
    if not any(role in {"qty", "price", "amount"} for role in roles):
        return None
    return HeaderLayout(titles=titles, roles=tuple(roles))


def classify_dev_headers(cells: list[object]) -> HeaderLayout | None:
    titles = _trim_titles(cells)
    if len(titles) < 2:
        return None
    roles = [dev_role(title) or "extra" for title in titles]
    if "requirement" not in roles:
        return None
    if "response" not in roles and "deviation" not in roles:
        return None
    return HeaderLayout(titles=titles, roles=tuple(roles))


def extract_quote_headers(text: str) -> HeaderLayout | None:
    return _extract_layout(text, classify_quote_headers)


def extract_dev_headers(text: str) -> HeaderLayout | None:
    return _extract_layout(text, classify_dev_headers)


def resolve_quote_layout(brief: BidBrief, item: OutlineItem | None = None) -> HeaderLayout:
    body = (item.body if item is not None else "") or ""
    from_body = extract_quote_headers(body)
    if from_body is not None:
        return from_body
    stored = _layout_from_stored(brief.quoteHeaders, brief.quoteRoles, classify_quote_headers)
    if stored is not None:
        return stored
    return HeaderLayout(titles=GENERIC_QUOTE_HEADERS, roles=tuple(quote_role(h) or "extra" for h in GENERIC_QUOTE_HEADERS))


def resolve_dev_layout(brief: BidBrief, item: OutlineItem) -> HeaderLayout:
    from_body = extract_dev_headers(item.body or "")
    if from_body is not None:
        return from_body
    stored_headers = brief.techDevHeaders if item.kind == "tech_dev" else brief.bizDevHeaders
    stored = _layout_from_stored(stored_headers, [], classify_dev_headers)
    if stored is not None:
        return stored
    return HeaderLayout(titles=GENERIC_DEV_HEADERS, roles=tuple(dev_role(h) or "extra" for h in GENERIC_DEV_HEADERS))


def apply_format_table_headers(brief: BidBrief) -> list[str]:
    """格式章表头覆盖清单表头，供大纲组卷使用。"""
    notes: list[str] = []
    quote_set = False
    biz_set = False
    tech_set = False
    for item in brief.outlineItems or []:
        if item.skipped:
            continue
        if item.kind == "quote" and not quote_set:
            layout = extract_quote_headers(item.body or "")
            if layout is not None:
                brief.quoteHeaders = list(layout.titles)
                brief.quoteRoles = list(layout.roles)
                quote_set = True
                notes.append("报价表表头已按招标书格式章抽取：" + " / ".join(layout.titles))
        elif item.kind == "biz_dev" and not biz_set:
            layout = extract_dev_headers(item.body or "")
            if layout is not None:
                brief.bizDevHeaders = list(layout.titles)
                biz_set = True
                notes.append("商务偏离表表头已按招标书抽取：" + " / ".join(layout.titles))
        elif item.kind == "tech_dev" and not tech_set:
            layout = extract_dev_headers(item.body or "")
            if layout is not None:
                brief.techDevHeaders = list(layout.titles)
                tech_set = True
                notes.append("技术偏离表表头已按招标书抽取：" + " / ".join(layout.titles))
    return notes


def width_ratios(roles: tuple[str, ...] | list[str]) -> tuple[int, ...]:
    weights = [_WIDTH.get(role, 12) for role in roles]
    return tuple(weights or (12,))


def name_amount_indexes(roles: tuple[str, ...] | list[str]) -> tuple[int, int]:
    amount_i = next((i for i, role in enumerate(roles) if role == "amount"), max(len(roles) - 1, 0))
    name_i = next((i for i, role in enumerate(roles) if role in {"name", "group"}), 1 if len(roles) > 1 else 0)
    return name_i, amount_i


def group_indexes(layout: HeaderLayout) -> list[int]:
    name_i = next((i for i, role in enumerate(layout.roles) if role == "name"), None)
    return [i for i, role in enumerate(layout.roles) if role == "group" and i != name_i]


def _trim_titles(cells: list[object]) -> tuple[str, ...]:
    titles = [str(cell or "").strip() for cell in cells]
    while titles and not titles[-1]:
        titles.pop()
    while titles and not titles[0]:
        titles.pop(0)
    return tuple(titles)


def _layout_from_stored(
    headers: list[str] | None,
    roles: list[str] | None,
    classify,
) -> HeaderLayout | None:
    titles = tuple((h or "").strip() for h in (headers or []) if (h or "").strip())
    if not titles:
        return None
    stored_roles = [r for r in (roles or []) if r]
    if len(stored_roles) == len(titles):
        if "name" in stored_roles or "requirement" in stored_roles:
            return HeaderLayout(titles=titles, roles=tuple(stored_roles))
    return classify(list(titles))


def _extract_layout(text: str, classify) -> HeaderLayout | None:
    best: HeaderLayout | None = None
    for row in _iter_header_rows(text):
        layout = classify(row)
        if layout is None:
            continue
        if best is None or len(layout.titles) > len(best.titles):
            best = layout
    return best


def _iter_header_rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.count("|") >= 2:
            rows.append([part.strip() for part in line.strip("|").split("|")])
            continue
        if "\t" in line:
            parts = [part.strip() for part in line.split("\t") if part.strip()]
            if len(parts) >= 3:
                rows.append(parts)
    return rows
