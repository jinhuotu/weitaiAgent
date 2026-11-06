"""Word library regressions with independently specified content and identities."""
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from docx import Document

from api.services.knowledge.parsers import extract_text_from_file
from api.services.tenders import library_kb as library
from common.errors import AppError


def word_bytes():
    document = Document()
    document.add_heading("技术服务方案", level=1)
    document.add_paragraph("负责现场安装指导。")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "岗位"
    table.cell(0, 1).text = "人数"
    table.cell(1, 0).text = "电缆工程师"
    table.cell(1, 1).text = "4"
    document.add_paragraph("随货提交安装说明书。")
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


@pytest.mark.asyncio
async def test_word_parser_preserves_body_table_order_and_values(tmp_path):
    source = tmp_path / "方案.docx"
    original = word_bytes()
    source.write_bytes(original)
    result = await extract_text_from_file(source, ocr_budget=0)
    assert result.text.splitlines() == [
        "技术服务方案", "负责现场安装指导。", "岗位 | 人数",
        "电缆工程师 | 4", "随货提交安装说明书。",
    ]
    assert source.read_bytes() == original


@pytest.mark.asyncio
async def test_unknown_titles_create_separate_entries_and_reuse_same_title(monkeypatch):
    documents = []
    base = SimpleNamespace(id=1)
    db = SimpleNamespace(execute=AsyncMock(), add=documents.append,
                         commit=AsyncMock(), refresh=AsyncMock())
    monkeypatch.setattr(library, "ensure_library_base", AsyncMock(return_value=base))
    monkeypatch.setattr(library, "_list_docs", AsyncMock(side_effect=lambda *_: list(documents)))
    monkeypatch.setattr(library, "list_slot_files", lambda *_: [])

    async def rows(*_):
        return [library.item_to_slot(doc, []) for doc in documents]

    monkeypatch.setattr(library, "list_library_items", rows)
    first = await library.create_library_item(db, title="电缆敷设施工专项方案")
    second = await library.create_library_item(db, title="运行维护培训课程")
    repeated = await library.create_library_item(db, title="电缆敷设施工专项方案")
    assert first["key"] != second["key"]
    assert first["key"] != "other_materials"
    assert second["title"] == "运行维护培训课程"
    assert repeated["docId"] == first["docId"]
    assert len(documents) == 2


@pytest.fixture
def upload_context(tmp_path, monkeypatch):
    children = []
    parent = SimpleNamespace(public_id="parent", name="电缆技术服务方案",
                             summary="安装指导", tags=["slot:custom_service"],
                             file_key=None, storage_path=None)
    db = SimpleNamespace(add=children.append, commit=AsyncMock(), refresh=AsyncMock())
    monkeypatch.setattr(library, "_get_parent", AsyncMock(return_value=(parent, children)))
    monkeypatch.setattr(library, "list_library_items", AsyncMock(return_value=[
        {"key": "custom_service", "title": parent.name},
    ]))
    monkeypatch.setattr(library, "ensure_library_base", AsyncMock(return_value=SimpleNamespace(id=1)))
    monkeypatch.setattr(library, "require_embedding_ready", AsyncMock())
    monkeypatch.setattr(library, "_attach_facts_extract", AsyncMock())
    monkeypatch.setattr(library, "_enqueue_library_doc", AsyncMock())
    monkeypatch.setattr(library, "_files_of", lambda *_: [])
    monkeypatch.setattr(library, "item_to_slot", lambda *_: {"key": "custom_service"})
    monkeypatch.setattr(library, "resolve_storage_path", lambda key: tmp_path / key)
    return db, parent, children


@pytest.mark.asyncio
async def test_word_upload_keeps_selected_parent_and_original_download(upload_context, monkeypatch):
    db, parent, children = upload_context
    original = word_bytes()
    await library.save_library_file(db, "custom_service", filename="技术服务方案.docx", data=original)
    child, = children
    path = library.resolve_storage_path(child.file_key)
    assert path.read_bytes() == original
    assert child.parent_id == "parent"
    assert child.file_type == "docx"
    assert "material-key:custom_service" in child.tags
    library._enqueue_library_doc.assert_awaited_once()
    assert library._enqueue_library_doc.call_args.kwargs["force_reextract"] is True
    monkeypatch.setattr(library, "get_base_by_public_id", AsyncMock(return_value=SimpleNamespace(id=1)))
    db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: child))
    child.name = "解析后显示标题"
    data, filename, mime = await library.open_library_file(db, child.public_id)
    assert data == original
    assert filename == "技术服务方案.docx"
    assert mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.mark.asyncio
async def test_invalid_word_does_not_replace_or_create(upload_context):
    db, _, children = upload_context
    with pytest.raises(AppError, match="Word 无法打开"):
        await library.save_library_file(db, "custom_service", filename="损坏.docx",
                                        data=b"not a Word archive", replace=True)
    assert children == []
    library._get_parent.assert_not_awaited()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_uses_key_returned_by_matching_existing_item(upload_context, monkeypatch):
    db, parent, _ = upload_context
    from common.errors import ErrorCode
    missing = AppError(ErrorCode.NOT_FOUND, "资料项不存在", status_code=404)
    monkeypatch.setattr(library, "_get_parent", AsyncMock(side_effect=[
        missing, (parent, []), (parent, []),
    ]))
    monkeypatch.setattr(library, "create_library_item", AsyncMock(return_value={"key": "custom_service"}))
    await library.save_library_file(db, "new_candidate", filename="方案.docx", data=word_bytes(),
                                    material_title="电缆技术服务方案")
    assert [call.args[1] for call in library._get_parent.call_args_list] == [
        "new_candidate", "custom_service", "custom_service",
    ]


@pytest.mark.asyncio
async def test_management_lists_empty_custom_item_without_changing_recognition(monkeypatch):
    row = {"key": "custom_service", "title": "电缆敷设培训", "docId": "parent", "fileCount": 0, "files": []}
    monkeypatch.setattr(library, "ensure_library_base", AsyncMock(return_value=SimpleNamespace(public_id="base")))
    monkeypatch.setattr(library, "enqueue_library_rag_backfill", AsyncMock())
    monkeypatch.setattr(library, "legal_persons_from_library", AsyncMock())
    monkeypatch.setattr(library, "list_slots_status_kb", AsyncMock(return_value=[]))
    monkeypatch.setattr(library, "list_library_items", AsyncMock(return_value=[row]))
    result = await library.library_payload_kb(None)
    assert result["slots"][0]["key"] == "custom_service"
    assert result["slots"][0]["title"] == "电缆敷设培训"
    assert result["slots"][0]["docId"] == "parent"


@pytest.mark.asyncio
async def test_word_download_header_encodes_original_chinese_name(monkeypatch):
    from urllib.parse import quote
    from api.routers import tenders

    original = b"original Word bytes"
    monkeypatch.setattr(tenders.kb_access, "require_tender_lib_read", AsyncMock())
    monkeypatch.setattr(tenders, "open_library_file", AsyncMock(return_value=(
        original, "安装指导方案.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )))
    response = await tenders.tenders_library_file("file", None, None, thumb=False)
    assert response.body == original
    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''" + quote("安装指导方案.docx")
