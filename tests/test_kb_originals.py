from api.services.ai.kb_originals import preview_kind, unique_tender_docs
from api.services.ai.prompts import TENDER_LIB_HINT, build_system_prompt
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
        has_images=True,
    )
    assert text is not None
    assert TENDER_LIB_HINT[:12] in text
    assert "read_media_file" in text
    other = await build_system_prompt(
        None,  # type: ignore[arg-type]
        [{"name": "手册", "content": "参数", "score": 0.5, "kb_id": "kbother"}],
        use_knowledge=True,
        kb_ids=["kbother"],
    )
    assert other is not None
    assert "投标资料库" not in other


def test_drop_fs_tools_for_tender_lib() -> None:
    from api.services.mcp.runtime import drop_fs_tools_for_tender_lib

    rows = [
        {"name": "read_media_file", "toolId": "1"},
        {"name": "list_tables", "toolId": "2"},
    ]
    kept = drop_fs_tools_for_tender_lib(rows, [TENDER_LIB_PUBLIC_ID])
    assert [t["name"] for t in kept] == ["list_tables"]
    assert drop_fs_tools_for_tender_lib(rows, ["other"]) == rows
