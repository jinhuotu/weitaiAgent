"""四期：报价表/偏离表按招标书表头生成（合成正文，不用真实邀请书）。"""

from __future__ import annotations

from api.services.tenders.outline import extract_outline
from api.services.tenders.quote import parse_quote_from_text, sheet_from_rows
from api.services.tenders.schema import BidBrief, OutlineItem, default_brief
from api.services.tenders.tables import (
    apply_format_table_headers,
    extract_dev_headers,
    extract_quote_headers,
    quote_role,
    resolve_quote_layout,
)


def test_quote_role_reads_module_columns() -> None:
    assert quote_role("系统名称") == "group"
    assert quote_role("子系统") == "group"
    assert quote_role("功能模块") == "name"
    assert quote_role("单价（元）") == "price"
    assert quote_role("合价（元）") == "amount"
    assert quote_role("设备") == "name"
    assert quote_role("设备型号") == "name"
    assert quote_role("产品名称") == "name"
    assert quote_role("参考单价(元)") == "price"
    assert quote_role("市场均价(元/台)") == "price"
    assert quote_role("市场价区间(元)") is None
    assert quote_role("技术规格明细") == "spec"
    assert quote_role("适用场景") == "scene"
    assert quote_role("类别") == "group"
    assert quote_role("技术参数要求") == "spec"


def test_parse_quote_from_system_module_pipe_text() -> None:
    text = (
        "报价表\n"
        "序号 | 系统名称 | 子系统 | 功能模块 | 单价（元） | 合价（元）\n"
        "1 | 制造管理系统 | 用户中心 | 单点登录 | 80000 | 80000\n"
        "2 |  |  | 权限管理 | 20000 | 20000\n"
    )
    sheet = parse_quote_from_text(text)
    assert sheet is not None
    assert len(sheet.lines) == 2
    assert sheet.headers[1] == "系统名称"
    assert sheet.lines[0].name == "单点登录"
    assert sheet.lines[0].groups == ("制造管理系统", "用户中心")
    assert sheet.lines[1].name == "权限管理"
    assert sheet.lines[1].groups[0] == "制造管理系统"
    assert int(sheet.lines[0].amount) == 80000


def test_extract_headers_from_format_chapter_body() -> None:
    quote = extract_quote_headers(
        "序号 | 系统名称 | 功能模块 | 单价（元） | 合价（元）\n |  |  |  | "
    )
    assert quote is not None
    assert "功能模块" in quote.titles
    assert "设备" not in quote.titles
    dev = extract_dev_headers("序号 | 招标文件规定 | 投标响应 | 偏离情况\n1 |  |  | ")
    assert dev is not None
    assert list(dev.titles) == ["序号", "招标文件规定", "投标响应", "偏离情况"]


def test_apply_format_headers_overrides_boq_charger_columns() -> None:
    brief = default_brief()
    brief.quoteHeaders = ["序号", "设备", "技术参数要求", "单位", "数量", "单价", "合价"]
    brief.outlineItems = [
        OutlineItem(
            id="q1",
            title="报价表",
            kind="quote",
            body="序号 | 系统名称 | 子系统 | 功能模块 | 单价（元） | 合价（元）",
        ),
        OutlineItem(
            id="d1",
            title="技术规范书偏离表",
            kind="tech_dev",
            body="序号 | 招标文件规定 | 投标响应 | 偏离情况",
        ),
    ]
    notes = apply_format_table_headers(brief)
    assert "功能模块" in brief.quoteHeaders
    assert "设备" not in brief.quoteHeaders
    assert brief.quoteHeaders[1] == "系统名称"
    assert brief.techDevHeaders[1] == "招标文件规定"
    assert any("功能模块" in n for n in notes)


def test_resolve_quote_layout_prefers_item_body() -> None:
    brief = BidBrief(quoteHeaders=["序号", "设备", "合价"])
    item = OutlineItem(
        title="报价表",
        kind="quote",
        body="序号 | 系统名称 | 功能模块 | 单价（元） | 合价（元）",
    )
    layout = resolve_quote_layout(brief, item)
    assert layout.titles[1] == "系统名称"
    assert "设备" not in layout.titles


def test_sheet_from_rows_xlsx_style_module_headers() -> None:
    sheet = sheet_from_rows(
        [
            ["序号", "系统名称", "功能模块", "数量", "单价", "合价"],
            ["1", "平台", "门户", 1, 1000, 1000],
        ]
    )
    assert sheet is not None
    assert sheet.lines[0].name == "门户"
    assert sheet.lines[0].groups == ("平台",)


def test_outline_extract_keeps_quote_body_with_headers() -> None:
    text = """
第五章 响应文件格式
报价表
技术规范书偏离表
附件 报价表
序号 | 系统名称 | 功能模块 | 单价（元） | 合价（元）
附件 技术规范书偏离表
序号 | 招标文件规定 | 投标响应 | 偏离情况
第六章 合同
"""
    _chapter, items = extract_outline(text)
    by_title = {item.title: item for item in items}
    assert "系统名称" in by_title["报价表"].body
    assert "招标文件规定" in by_title["技术规范书偏离表"].body
