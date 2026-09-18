from api.services.ai.kb_originals import preview_kind, unique_tender_docs
from api.services.ai.prompts import KB_IMAGE_HINT, TENDER_LIB_HINT, VISION_HINT, build_system_prompt
from api.services.knowledge.access import TENDER_LIB_PUBLIC_ID
import pytest


def test_preview_kind() -> None:
    assert preview_kind("jpg", has_file=True) == "image"
    assert preview_kind("pdf", has_file=True) == "pdf"
    assert preview_kind("docx", has_file=True) == "file"
    assert preview_kind("jpg", has_file=False) == ""


def test_unique_tender_docs_dedupes() -> None:
    rows = unique_tender_docs(
        [
            {
                "doc_id": "a1",
                "kb_id": TENDER_LIB_PUBLIC_ID,
                "has_file": True,
                "preview_kind": "image",
                "name": "身份证正面照",
            },
            {
                "doc_id": "a1",
                "kb_id": TENDER_LIB_PUBLIC_ID,
                "has_file": True,
                "preview_kind": "image",
                "name": "身份证正面照",
            },
            {
                "doc_id": "b2",
                "kb_id": TENDER_LIB_PUBLIC_ID,
                "has_file": True,
                "preview_kind": "image",
                "name": "身份证背面照",
            },
            {
                "doc_id": "c3",
                "kb_id": "otherkb",
                "has_file": True,
                "preview_kind": "image",
                "name": "别的库",
            },
            {
                "doc_id": "d4",
                "kb_id": TENDER_LIB_PUBLIC_ID,
                "has_file": False,
                "preview_kind": "",
                "name": "空项",
            },
        ]
    )
    assert [r["doc_id"] for r in rows] == ["a1", "b2"]


def test_unique_tender_docs_skips_unrelated_query() -> None:
    rows = unique_tender_docs(
        [
            {
                "doc_id": "credit",
                "kb_id": TENDER_LIB_PUBLIC_ID,
                "has_file": True,
                "preview_kind": "image",
                "name": "河南优祺计算机科技有限公司",
                "content": "法人和非法人组织公共信用信息报告",
            },
            {
                "doc_id": "id-front",
                "kb_id": TENDER_LIB_PUBLIC_ID,
                "has_file": True,
                "preview_kind": "image",
                "name": "身份证正面照",
                "content": "居民身份证",
            },
        ],
        query="做衣服的省位线怎么做",
    )
    assert rows == []
    kept = unique_tender_docs(
        [
            {
                "doc_id": "id-front",
                "kb_id": TENDER_LIB_PUBLIC_ID,
                "has_file": True,
                "preview_kind": "image",
                "name": "身份证正面照",
                "content": "居民身份证",
            }
        ],
        query="查看身份证",
    )
    assert [r["doc_id"] for r in kept] == ["id-front"]


@pytest.mark.asyncio
async def test_tender_lib_prompt_allows_id_scan() -> None:
    text = await build_system_prompt(
        None,  # type: ignore[arg-type]
        [
            {
                "name": "身份证正面照",
                "content": "公民身份号码示例",
                "score": 0.4,
                "kb_id": TENDER_LIB_PUBLIC_ID,
            }
        ],
        use_knowledge=True,
        kb_ids=[TENDER_LIB_PUBLIC_ID],
        has_images=False,
        has_kb_images=True,
    )
    assert text is not None
    assert TENDER_LIB_HINT in text
    assert KB_IMAGE_HINT in text
    assert VISION_HINT not in text
    other = await build_system_prompt(
        None,  # type: ignore[arg-type]
        [{"name": "手册", "content": "参数", "score": 0.5, "kb_id": "kbother"}],
        use_knowledge=True,
        kb_ids=["kbother"],
    )
    assert other is not None
    assert "投标资料库" not in other


@pytest.mark.asyncio
async def test_kb_miss_only_injects_mark() -> None:
    text = await build_system_prompt(
        None,  # type: ignore[arg-type]
        [],
        use_knowledge=True,
        has_images=False,
    )
    assert text == "【知识库检索结果】未检索到匹配片段。"
    assert "第一句必须是" not in text


@pytest.mark.asyncio
async def test_prompt_skips_tender_hint_without_originals() -> None:
    text = await build_system_prompt(
        None,  # type: ignore[arg-type]
        [
            {
                "name": "河南优祺计算机科技有限公司",
                "content": "公共信用信息报告",
                "score": 0.3,
                "kb_id": TENDER_LIB_PUBLIC_ID,
            }
        ],
        use_knowledge=True,
        kb_ids=[TENDER_LIB_PUBLIC_ID],
        has_images=False,
        has_kb_images=False,
    )
    assert text is not None
    assert "投标资料库" not in text
    assert VISION_HINT not in text
    assert "您提供的图片" not in text


def test_drop_fs_tools_for_tender_lib() -> None:
    from api.services.mcp.runtime import drop_fs_tools_for_tender_lib

    rows = [
        {"name": "read_media_file", "toolId": "1"},
        {"name": "list_tables", "toolId": "2"},
    ]
    kept = drop_fs_tools_for_tender_lib(rows, [TENDER_LIB_PUBLIC_ID])
    assert [t["name"] for t in kept] == ["list_tables"]
    assert drop_fs_tools_for_tender_lib(rows, ["other"]) == rows


def test_default_seed_owns_reply_rules() -> None:
    from api.services.prompts.configs import _DEFAULT_SEED_CONTENT

    assert "当前未从知识库中检索出您想要的信息" in _DEFAULT_SEED_CONTENT
    assert "read_media_file" in _DEFAULT_SEED_CONTENT
    assert "【本次附带资料库原件】" in _DEFAULT_SEED_CONTENT
