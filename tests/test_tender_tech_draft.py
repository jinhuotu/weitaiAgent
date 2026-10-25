"""通用技术标草稿：分类、组卷不抄邀请书、无事实/无历史库也能起草。"""

from __future__ import annotations

from api.services.tenders.categories import PENDING_FACT, chapter_draft_text
from api.services.tenders.extract import _SYSTEM
from api.services.tenders.outline import classify_kind, items_for_volume
from api.services.tenders.schema import BidBrief, BidRequirement, DeviationLine, OutlineItem, QuoteLineIn
from api.services.tenders.tech_draft import (
    draft_deviations,
    draft_tech,
    fallback_draft,
    pick_library_lines,
    quote_facts,
    rank_past_hits,
    retrieve_past_fragments,
    scrub_fragment,
    tech_red_lines,
)


def test_extract_prompt_not_charger_specific() -> None:
    assert "充电站" not in _SYSTEM
    assert "桩数" not in _SYSTEM
    assert "7kW" not in _SYSTEM
    assert "纯章节标题列表页" in _SYSTEM
    assert "attachToSection" in _SYSTEM
    assert "绝不能进目录" in _SYSTEM


def test_extract_outline_keeps_construction_org() -> None:
    from api.services.tenders.outline import extract_outline

    text = """
第五章 投标文件格式
一、投标函
二、施工组织设计
三、售后服务方案
四、技术偏差表
第六章 评标办法
"""
    _chapter, items = extract_outline(text)
    titles = [item.title for item in items]
    kinds = {item.title: item.kind for item in items}
    assert "施工组织设计" in titles
    assert kinds["施工组织设计"] == "tech_chapter"
    assert kinds["售后服务方案"] == "tech_chapter"


def test_classify_generic_tech_titles() -> None:
    assert classify_kind("施工组织设计") == "tech_chapter"
    assert classify_kind("售后服务方案") == "tech_chapter"
    assert classify_kind("培训方案") == "tech_chapter"
    assert classify_kind("技术标准和要求") == "unknown"


def test_assemble_writes_draft_not_invite_body(tmp_path) -> None:
    from docx import Document

    from api.services.tenders.assemble import assemble_bid_docx

    brief = BidBrief(
        projectName="某信息化项目",
        tenderer="某招标人",
        layoutMode="outline",
        generateVolume="technical",
        includeCommitment=False,
        attachQualifications=False,
        outlineItems=[
            OutlineItem(
                id="t1",
                title="售后服务方案",
                kind="tech_chapter",
                source="generate",
                body="招标人要求提供三年驻场运维，并列出备件清单。",
                inviteExcerpt="招标人要求提供三年驻场运维，并列出备件清单。",
                draftBody="我方提供质保期内远程支持，驻场人数【待补充】。",
                draftStatus="drafted",
            ),
            OutlineItem(id="d1", title="技术偏差表", kind="tech_dev", source="generate"),
        ],
        deviationLines=[
            DeviationLine(seq="1", requirement="三年驻场", response="【待响应】", deviation="待确认"),
        ],
    )
    path, _ = assemble_bid_docx(brief, tmp_path / "tech.docx", volume="technical")
    blob = "\n".join(p.text for p in Document(str(path)).paragraphs)
    assert "我方提供质保期内远程支持" in blob
    assert "列出备件清单" not in blob
    assert "售后服务方案" in blob


def test_fallback_marks_missing_facts() -> None:
    item = OutlineItem(id="t1", title="培训方案", kind="tech_chapter", inviteExcerpt="需提供操作培训")
    text = fallback_draft(item, facts="", scores=["培训具有针对性"])
    assert PENDING_FACT in text
    assert "操作培训" in text


def test_draft_tech_without_history_and_facts() -> None:
    import asyncio

    brief = BidBrief(
        outlineItems=[
            OutlineItem(
                id="t1",
                title="培训方案",
                kind="tech_chapter",
                inviteExcerpt="提供不少于2次操作培训",
            )
        ]
    )

    async def _no_hits(*_a, **_k):
        return []

    out, notes = asyncio.run(draft_tech(brief, search=_no_hits))
    item = next(i for i in out.outlineItems if i.id == "t1")
    assert item.draftBody
    assert item.draftStatus == "drafted"
    assert PENDING_FACT in item.draftBody or "【待响应】" in item.draftBody
    assert any("起草" in n for n in notes)


def test_draft_tech_uses_tagged_history_hits() -> None:
    import asyncio

    brief = BidBrief(
        outlineItems=[
            OutlineItem(id="t1", title="售后服务方案", kind="tech_chapter", inviteExcerpt="质保三年"),
        ]
    )
    seen: dict = {}

    async def _search(_db, *, query, top_k=3, kb_ids=None):
        seen["query"] = query
        seen["kb"] = kb_ids
        return [
            {"content": "历史售后写法和响应节奏", "tags": ["tech_bid"]},
            {"content": "无关片段", "tags": ["other"]},
        ]

    out, notes = asyncio.run(draft_tech(brief, kb_ids=["kb1"], search=_search, client=None))
    item = next(i for i in out.outlineItems if i.id == "t1")
    assert "历史写法" in item.draftBody
    assert any("历史写法" in n for n in notes)
    assert "售后" in seen["query"]


def test_retrieve_prefers_tech_bid_tag() -> None:
    import asyncio

    async def _search(_db, **_k):
        return [
            {"content": "普通片段", "tags": []},
            {"content": "技术标片段", "tags": ["tech_bid"]},
        ]

    texts = asyncio.run(
        retrieve_past_fragments(
            None,
            OutlineItem(id="t1", title="技术方案", kind="tech_chapter"),
            search=_search,
        )
    )
    assert texts == ["技术标片段"]


def test_deviation_unmatched_is_pending() -> None:
    brief = BidBrief(
        deviationLines=[
            DeviationLine(seq="1", requirement="防护等级 IP54", response="", deviation=""),
        ],
        quoteLines=[QuoteLineIn(name="交换机", spec="24口千兆")],
    )
    out = draft_deviations(brief, quote_facts(brief))
    row = out.deviationLines[0]
    assert row.response == "【待响应】"
    assert row.deviation == "待确认"


def test_deviation_matched_from_quote() -> None:
    brief = BidBrief(
        deviationLines=[
            DeviationLine(seq="1", requirement="24口千兆交换机", response="", deviation=""),
        ],
        quoteLines=[QuoteLineIn(name="交换机", spec="24口千兆")],
    )
    out = draft_deviations(brief, quote_facts(brief))
    row = out.deviationLines[0]
    assert "24口千兆" in row.response
    assert row.deviation == "待确认"


def test_deviation_keeps_user_confirmed() -> None:
    brief = BidBrief(
        deviationLines=[
            DeviationLine(
                seq="1",
                requirement="24口千兆交换机",
                response="按报价配置",
                deviation="无偏差",
            ),
        ],
        quoteLines=[QuoteLineIn(name="交换机", spec="24口千兆")],
    )
    out = draft_deviations(brief, quote_facts(brief))
    row = out.deviationLines[0]
    assert row.response == "按报价配置"
    assert row.deviation == "无偏差"


def test_deviation_rows_empty_skips_quote() -> None:
    from api.services.tenders.document import _deviation_rows

    brief = BidBrief(quoteLines=[QuoteLineIn(name="高压开关柜", spec="10kV")])
    blob = "\n".join(" ".join(row) for row in _deviation_rows(brief))
    assert "高压开关柜" not in blob
    assert "待确认" in blob


def test_chapter_draft_text_not_reuse_note_on_many_chapters() -> None:
    brief = BidBrief(
        techPlanNote="旧的一段实施方案",
        outlineItems=[
            OutlineItem(id="a", title="施工组织设计", kind="tech_chapter"),
            OutlineItem(id="b", title="培训方案", kind="tech_chapter"),
        ],
    )
    a = chapter_draft_text(brief.outlineItems[0], brief)
    b = chapter_draft_text(brief.outlineItems[1], brief)
    assert "【待响应】" in a
    assert "【待响应】" in b


def test_default_volume_adds_tech_chapter_not_works_plan() -> None:
    brief = BidBrief(layoutMode="chapter5")
    tech = items_for_volume(brief, "technical")
    kinds = [item.kind for item in tech]
    titles = [item.title for item in tech]
    assert "tech_dev" in kinds
    assert "tech_chapter" in kinds or "tech_plan" in kinds
    assert any(item.title in {"技术方案", "技术标（实施方案）"} or item.kind == "tech_chapter" for item in tech)
    assert titles.index("技术方案") < titles.index("技术偏差表")
    biz = [item.title for item in items_for_volume(brief, "business")]
    assert biz.index("投标函") < biz.index("分项报价表") < biz.index("商务偏离表")
    assert "技术方案" not in biz
    assert "技术偏差表" not in biz


def test_rank_prefers_won_and_drops_lost_body() -> None:
    samples, avoid = rank_past_hits(
        [
            {"content": "普通写法一段", "tags": ["tech_bid"], "doc_id": "a"},
            {"content": "中标写法一段", "tags": ["tech_bid", "outcome:won"], "doc_id": "b"},
            {"content": "失败写法一段", "tags": ["tech_bid", "outcome:lost"], "doc_id": "c"},
        ],
        {
            "c": {"verdict": "bad", "problemTags": ["工期"], "note": "交货写超了"},
        },
    )
    assert samples == ["中标写法一段", "普通写法一段"]
    assert "失败写法" not in " ".join(samples)
    assert any("工期" in line and "交货写超了" in line for line in avoid)


def test_scrub_drops_current_project_line() -> None:
    brief = BidBrief(projectName="郑州高新新能源项目", tenderNo="26-GCZN-0150", tenderer="郑州某某建设集团")
    text = scrub_fragment("中标响应写法如下\n郑州高新新能源项目报价128万\n质保期内远程支持", brief)
    assert "中标响应写法" in text
    assert "远程支持" in text
    assert "郑州高新新能源项目" not in text
    assert "26-GCZN-0150" not in text


def test_tech_red_lines_skip_business() -> None:
    brief = BidBrief(
        bidRequirements=[
            BidRequirement(text="技术负偏离作废标处理", volume="technical", strength="mandatory"),
            BidRequirement(text="未盖章按否决投标处理", volume="business", strength="mandatory"),
            BidRequirement(text="售后服务方案具有针对性", category="scoring", volume="technical"),
        ]
    )
    lines = tech_red_lines(brief)
    assert lines == ["技术负偏离作废标处理"]


def test_library_cites_cert_not_person_name() -> None:
    item = OutlineItem(id="t1", title="企业资质", kind="tech_chapter", inviteExcerpt="须提供资质证书")
    bundles = [
        {
            "key": "资质",
            "role": "company",
            "fields": [
                {"name": "证书编号", "value": "A1234567", "conf": 0.9},
                {"name": "姓名", "value": "郭志伟", "conf": 1},
            ],
        }
    ]
    lines = pick_library_lines(item, ["资质证书齐全"], bundles)
    assert lines == ["证书编号：A1234567"]
    assert pick_library_lines(
        OutlineItem(id="t2", title="培训方案", kind="tech_chapter"),
        [],
        bundles,
    ) == []


def test_draft_searches_score_and_keeps_red_line() -> None:
    import asyncio

    brief = BidBrief(
        projectName="郑州高新新能源项目",
        outlineItems=[
            OutlineItem(id="t1", title="售后服务方案", kind="tech_chapter", inviteExcerpt="质保三年"),
        ],
        bidRequirements=[
            BidRequirement(
                text="售后服务方案具有针对性",
                category="scoring",
                targetKind="tech_chapter",
                targetTitle="售后服务方案",
            ),
            BidRequirement(text="技术负偏离作废标处理", volume="technical", strength="mandatory"),
            BidRequirement(text="未盖章按否决投标处理", volume="business", strength="mandatory"),
        ],
    )
    queries: list[str] = []

    async def _search(_db, *, query, top_k=3, kb_ids=None):
        queries.append(query)
        if "针对性" in query:
            return [{"content": "按评分点写响应节奏\n郑州高新新能源项目不该出现", "tags": ["tech_bid", "outcome:won"]}]
        return [
            {"content": "中标售后写法", "tags": ["tech_bid", "outcome:won"]},
            {"content": "失败售后写法", "tags": ["tech_bid", "outcome:lost"]},
        ]

    out, notes = asyncio.run(
        draft_tech(
            brief,
            search=_search,
            client=None,
            catalog_facts=[
                {
                    "key": "资质",
                    "role": "company",
                    "fields": [
                        {"name": "证书编号", "value": "A1234567", "conf": 1},
                        {"name": "姓名", "value": "郭志伟", "conf": 1},
                    ],
                }
            ],
        )
    )
    item = next(i for i in out.outlineItems if i.id == "t1")
    assert any("针对性" in q for q in queries)
    assert "技术负偏离作废标处理" in item.draftBody
    assert "未盖章" not in item.draftBody
    assert "评分点响应参考" in item.draftBody
    assert "郑州高新新能源项目" not in item.draftBody
    assert "禁止再犯" in item.draftBody
    assert any("避开" in n for n in notes)
    assert any("评分点检索" in n for n in notes)
    # 售后章对不上资质资料，不把证照号塞进来
    assert "A1234567" not in item.draftBody
    assert "郭志伟" not in item.draftBody


def test_draft_quotes_library_cert() -> None:
    import asyncio

    brief = BidBrief(
        outlineItems=[
            OutlineItem(id="t1", title="企业资质", kind="tech_chapter", inviteExcerpt="提供资质证书"),
        ]
    )

    async def _search(_db, *, query, top_k=3, kb_ids=None):
        return []

    out, notes = asyncio.run(
        draft_tech(
            brief,
            search=_search,
            client=None,
            catalog_facts=[
                {
                    "key": "资质",
                    "role": "company",
                    "fields": [
                        {"name": "证书编号", "value": "A1234567", "conf": 0.95},
                        {"name": "姓名", "value": "郭志伟", "conf": 1},
                    ],
                }
            ],
        )
    )
    item = next(i for i in out.outlineItems if i.id == "t1")
    assert "A1234567" in item.draftBody
    assert "郭志伟" not in item.draftBody
    assert any("资料库" in n for n in notes)


def test_scoring_requirement_maps_to_empty_chapter() -> None:
    from api.services.tenders.outline import ensure_tech_chapters

    brief = BidBrief(
        bidRequirements=[
            BidRequirement(
                id="r01",
                text="售后服务方案具有针对性",
                category="scoring",
                targetKind="tech_chapter",
                targetTitle="售后服务方案",
            )
        ],
    )
    out = ensure_tech_chapters([], brief)
    titles = [item.title for item in out]
    assert any("售后" in t for t in titles)
