from api.services.knowledge.rerank import (
    compact_search_query,
    filter_unrelated_tender_hits,
    query_slot_keys,
    restrict_slot_hits,
    restrict_topic_hits,
    topic_keys,
)
from api.services.knowledge.access import TENDER_LIB_PUBLIC_ID


def test_compact_strips_chat_padding() -> None:
    assert compact_search_query("请你帮我查看一下我的身份证") == "身份证"
    assert compact_search_query("帮我看一下信用中国") == "信用中国"
    assert compact_search_query("请你帮我查询一下法人身份证") == "法人身份证"


def test_restrict_topic_drops_other_tender_scans() -> None:
    hits = [
        {"name": "身份证正面照", "content": "居民身份证", "score": 0.4, "doc_id": "id-front"},
        {"name": "图片1", "content": "居民身份证 正面", "score": 0.33, "doc_id": "pic1"},
        {"name": "信用中国截图", "content": "守信激励对象名单", "score": 0.31, "doc_id": "credit"},
        {
            "name": "公司营业执照",
            "content": "统一社会信用代码",
            "score": 0.30,
            "doc_id": "license",
        },
    ]
    q = compact_search_query("请你帮我查看一下我的身份证")
    assert "身份证" in topic_keys(q, hits)
    kept = restrict_topic_hits(q, hits)
    names = [h["name"] for h in kept]
    assert "身份证正面照" in names
    assert "图片1" in names
    assert "信用中国截图" not in names
    assert "公司营业执照" not in names


def test_restrict_credit_query_keeps_credit_only() -> None:
    hits = [
        {"name": "身份证正面照", "content": "居民身份证", "doc_id": "id-front"},
        {"name": "信用中国截图", "content": "守信激励", "doc_id": "credit"},
    ]
    kept = restrict_topic_hits("信用中国", hits)
    assert [h["name"] for h in kept] == ["信用中国截图"]


def test_legal_id_query_drops_agent_slot() -> None:
    hits = [
        {
            "name": "身份证正面照",
            "content": "居民身份证",
            "tags": ["投标资料", "slot:id_legal"],
        },
        {
            "name": "图片2",
            "content": "居民身份证",
            "tags": ["投标资料", "slot:id_agent"],
        },
    ]
    assert query_slot_keys("请你帮我查询一下法人身份证") == {"id_legal"}
    kept = restrict_slot_hits("法人身份证", hits)
    assert [h["name"] for h in kept] == ["身份证正面照"]
    agent = restrict_slot_hits("委托人身份证", hits)
    assert [h["name"] for h in agent] == ["图片2"]
    both = restrict_slot_hits("身份证", hits)
    assert len(both) == 2


def test_unrelated_query_drops_tender_credit_scan() -> None:
    hits = [
        {
            "name": "河南优祺计算机科技有限公司",
            "content": "法人和非法人组织公共信用信息报告 信用中国",
            "score": 0.35,
            "keyword_score": 0.0,
            "kb_id": TENDER_LIB_PUBLIC_ID,
            "doc_id": "credit",
        },
        {
            "name": "省位线工艺手册",
            "content": "衣服省位线怎么做",
            "score": 0.41,
            "keyword_score": 0.8,
            "kb_id": "otherkb",
            "doc_id": "sew",
        },
    ]
    kept = filter_unrelated_tender_hits("做衣服的省位线怎么做", hits)
    assert [h["doc_id"] for h in kept] == ["sew"]


def test_id_query_keeps_tender_id_scan() -> None:
    hits = [
        {
            "name": "身份证正面照",
            "content": "居民身份证 公民身份号码",
            "kb_id": TENDER_LIB_PUBLIC_ID,
            "doc_id": "id-front",
            "tags": ["投标资料", "slot:id_legal"],
        }
    ]
    kept = filter_unrelated_tender_hits("帮我看一下法人身份证", hits)
    assert [h["doc_id"] for h in kept] == ["id-front"]
