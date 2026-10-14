"""本标缺格式章时，知识库只建议目录/表头，不抄正文。"""

from __future__ import annotations

from api.services.tenders.extract import apply_kb_format_suggestions
from api.services.tenders.outline import has_invite_format_chapter, titles_from_format_text
from api.services.tenders.schema import BidBrief, OutlineItem

_KB_FORMAT = """
第五章 投标文件格式
一、投标函
致：某电厂
投标报价：100万元
二、法定代表人身份证明
三、分项报价表
序号 | 名称 | 规格型号 | 单位 | 数量 | 单价（元） | 合价
1 | 开关柜 | 10kV | 面 | 2 | 30000 | 60000
四、技术偏离表
序号 | 招标文件要求 | 投标文件响应 | 偏差
"""


def test_has_invite_format_chapter() -> None:
    assert has_invite_format_chapter("第五章 投标文件格式")
    assert has_invite_format_chapter("响应文件组成")
    assert not has_invite_format_chapter("")
    assert not has_invite_format_chapter("投标须知")


def test_titles_from_format_text_strips_body() -> None:
    items = titles_from_format_text(_KB_FORMAT)
    titles = [item.title for item in items]
    assert any("投标函" in t for t in titles)
    assert any("报价" in t for t in titles)
    assert all(not (item.body or "").strip() for item in items)
    assert all(item.source == "generate" for item in items)
    blob = "\n".join(item.body or "" for item in items)
    assert "100万" not in blob
    assert "开关柜" not in blob


def test_kb_format_fills_empty_outline_and_headers() -> None:
    brief = BidBrief()
    notes = apply_kb_format_suggestions(
        brief,
        "",
        [{"name": "格式说明.docx", "content": _KB_FORMAT}],
    )
    titles = [item.title for item in brief.outlineItems]
    assert any("投标函" in t for t in titles)
    assert brief.layoutMode == "outline"
    assert any("来自知识库" in n for n in notes)
    assert all("100万" not in (item.body or "") for item in brief.outlineItems)
    assert "名称" in brief.quoteHeaders
    assert "数量" in brief.quoteHeaders
    assert "招标文件要求" in brief.techDevHeaders


def test_kb_format_skips_when_invite_has_format_chapter() -> None:
    brief = BidBrief(
        outlineItems=[],
        quoteHeaders=[],
        layoutMode="chapter5",
    )
    notes = apply_kb_format_suggestions(
        brief,
        "第五章 投标文件格式",
        [{"name": "格式说明.docx", "content": _KB_FORMAT}],
    )
    assert notes == []
    assert brief.outlineItems == []
    assert brief.quoteHeaders == []
    assert brief.layoutMode == "chapter5"


def test_kb_format_does_not_replace_existing_outline_or_headers() -> None:
    letter = OutlineItem(
        id="o01",
        title="投标函",
        kind="letter",
        source="copy",
        body="致：本标招标人\n我方确认收到招标文件",
    )
    brief = BidBrief(
        outlineItems=[letter],
        quoteHeaders=["序号", "设备", "单位", "数量", "单价（元）", "合价"],
        layoutMode="outline",
    )
    apply_kb_format_suggestions(
        brief,
        "",
        [{"name": "格式说明.docx", "content": _KB_FORMAT}],
    )
    assert len([i for i in brief.outlineItems if i.title == "投标函"]) == 1
    assert "我方确认收到招标文件" in (brief.outlineItems[0].body or "")
    assert brief.quoteHeaders[1] == "设备"
    assert "开关柜" not in (brief.outlineItems[0].body or "")
