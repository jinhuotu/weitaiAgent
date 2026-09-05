"""审核闸门、入库队列取消、Qdrant 量化配置（不连 MySQL / Qdrant）。"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.services.knowledge.ingest import _filter_approved_hits, _initial_review_status
from api.services.knowledge.qdrant_store import QdrantKnowledgeStore
from api.services.knowledge.review import review_document
from common.errors import AppError


def test_initial_review_always_approved() -> None:
    assert _initial_review_status(drawing=False) == "approved"
    assert _initial_review_status(drawing=True) == "approved"


@pytest.mark.asyncio
async def test_filter_approved_hits_drops_pending() -> None:
    class Result:
        def all(self) -> list[tuple[str, list[str]]]:
            return [("ok-doc", ["手册"])]

    class Db:
        async def execute(self, _stmt: Any) -> Result:
            return Result()

    hits = [
        {"doc_id": "ok-doc", "content": "命中"},
        {"doc_id": "pending-doc", "content": "不应出现"},
        {"doc_id": "", "content": "无 id"},
    ]
    out = await _filter_approved_hits(Db(), hits)  # type: ignore[arg-type]
    assert [h["doc_id"] for h in out] == ["ok-doc"]


@pytest.mark.asyncio
async def test_filter_approved_hits_drops_bid_foreign() -> None:
    class Result:
        def all(self) -> list[tuple[str, list[str]]]:
            return [
                ("ok-doc", ["手册"]),
                ("bid-doc", ["手动上传", "bid_foreign"]),
            ]

    class Db:
        async def execute(self, _stmt: Any) -> Result:
            return Result()

    hits = [
        {"doc_id": "ok-doc", "content": "ISO 认证要求"},
        {"doc_id": "bid-doc", "content": "投标函", "tags": ["bid_foreign"]},
    ]
    out = await _filter_approved_hits(Db(), hits)  # type: ignore[arg-type]
    assert [h["doc_id"] for h in out] == ["ok-doc"]


def test_classify_kb_skips_bid_package_and_foreign_body() -> None:
    from api.services.knowledge.classify import classify_kb_document, should_skip_kb_retrieval

    skipped = classify_kb_document(name="商务标投标文件.docx", text="资格审查见前附表")
    assert skipped.skip_vectorize
    skipped_fn = classify_kb_document(name="授权委托投标函.pdf", text="")
    assert skipped_fn.skip_vectorize
    skipped_body = classify_kb_document(
        name="手册.txt",
        text="投标人：郑州容新新能源有限公司\n投标函",
    )
    assert skipped_body.skip_vectorize
    skipped_other = classify_kb_document(
        name="某公司材料.txt",
        text="投标人：郑州有爱文化科技有限公司\n盖章页",
    )
    assert skipped_other.skip_vectorize

    allowed = classify_kb_document(
        name="充电桩产品检测报告.pdf",
        text="河南伟泰光电科技有限公司交流充电桩型式试验",
    )
    assert not allowed.skip_vectorize
    invite = classify_kb_document(
        name="充电桩采购项目招标文件.pdf",
        text="投标人须知前附表：须提供营业执照",
    )
    assert not invite.skip_vectorize
    weitai = classify_kb_document(
        name="公司简介.txt",
        text="投标人：河南伟泰光电科技有限公司",
    )
    assert not weitai.skip_vectorize
    requirement = classify_kb_document(
        name="资格审查办法.pdf",
        text="投标人：必须是在中华人民共和国境内注册的、具有独立法人资格的企业（有限公司或股份有限公司）。",
    )
    assert not requirement.skip_vectorize
    guide = classify_kb_document(name="投标文件编制说明.pdf", text="如何装订")
    assert not guide.skip_vectorize
    assert should_skip_kb_retrieval(name="历史投标文件.docx", content="本司报价")
    assert not should_skip_kb_retrieval(name="ISO体系证书说明.pdf", content="质量管理体系")


def _doc(**kw: Any) -> SimpleNamespace:
    base = dict(
        id=1,
        public_id="doc1",
        name="手册",
        source="text",
        kind="doc",
        parent_id=None,
        file_type="txt",
        size=12,
        url=None,
        file_key=None,
        storage_path=None,
        preview_url=None,
        summary="正文",
        char_count=2,
        chunk_count=0,
        tags=[],
        uploader="admin",
        status="ready",
        error_msg=None,
        page_count=0,
        ocr_pages=0,
        ocr_capped=False,
        formula_fallback=False,
        review_status="pending",
        review_comment=None,
        reviewed_by=None,
        reviewed_at=None,
        created_at=datetime.now(timezone.utc),
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _MemDb:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.deleted: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def get(self, _model: Any, _id: Any) -> Any:
        return None

    async def delete(self, row: Any) -> None:
        self.deleted.append(row)

    async def commit(self) -> None:
        return None

    async def refresh(self, _row: Any) -> None:
        return None

    async def execute(self, _stmt: Any) -> Any:
        raise AssertionError("unexpected execute")


@pytest.mark.asyncio
async def test_review_approve_vectorizes(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _doc()
    vec = AsyncMock()
    monkeypatch.setattr(
        "api.services.knowledge.review.get_base_by_public_id",
        AsyncMock(return_value=SimpleNamespace(id=9, public_id="kb1")),
    )
    monkeypatch.setattr("api.services.knowledge.review._get_doc", AsyncMock(return_value=doc))
    monkeypatch.setattr("api.services.knowledge.review._vectorize_document", vec)
    monkeypatch.setattr(
        "api.services.knowledge.review.read_extracted_text", lambda *_a, **_k: "冷却水停机"
    )
    db = _MemDb()
    item = await review_document(
        db,  # type: ignore[arg-type]
        base_public_id="kb1",
        doc_public_id="doc1",
        action="approve",
        comment="ok",
        actor_id=1,
    )
    vec.assert_awaited()
    assert doc.review_status == "approved"
    assert item["reviewStatus"] == "approved"
    assert db.added and db.added[0].action == "approve"


@pytest.mark.asyncio
async def test_review_reject_deletes_vectors(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _doc(review_status="approved", chunk_count=4)
    store = MagicMock()
    monkeypatch.setattr(
        "api.services.knowledge.review.get_base_by_public_id",
        AsyncMock(return_value=SimpleNamespace(id=9, public_id="kb1")),
    )
    monkeypatch.setattr("api.services.knowledge.review._get_doc", AsyncMock(return_value=doc))
    monkeypatch.setattr("api.services.knowledge.review.get_qdrant_store", lambda: store)
    db = _MemDb()
    item = await review_document(
        db,  # type: ignore[arg-type]
        base_public_id="kb1",
        doc_public_id="doc1",
        action="reject",
        comment="不行",
        actor_id=1,
    )
    store.delete_by_doc_id.assert_called_once_with("doc1")
    assert doc.review_status == "rejected"
    assert doc.chunk_count == 0
    assert item["reviewStatus"] == "rejected"


@pytest.mark.asyncio
async def test_cancel_queued_deletes_task_row(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.services.knowledge import queue as q

    task = SimpleNamespace(
        status="queued",
        document_id=1,
        public_id="task1",
        finished_at=None,
        base_id=2,
    )
    doc = SimpleNamespace(status="parsing", error_msg=None)

    class Result:
        def scalar_one_or_none(self) -> Any:
            return task

    db = _MemDb()

    async def execute(_stmt: Any) -> Result:
        return Result()

    db.execute = execute  # type: ignore[method-assign]

    async def get(_model: Any, _id: Any) -> Any:
        return doc

    db.get = get  # type: ignore[method-assign]

    monkeypatch.setattr(
        "api.services.knowledge.bases.get_base_by_public_id",
        AsyncMock(return_value=SimpleNamespace(id=2, public_id="kb1")),
    )
    out = await q.cancel_ingest_task(db, base_public_id="kb1", task_public_id="task1")  # type: ignore[arg-type]
    assert out["cancelled"] is True
    assert task.status == "cancelled"
    assert doc.status == "failed"
    assert db.deleted == [task]


@pytest.mark.asyncio
async def test_cancel_running_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.services.knowledge import queue as q

    task = SimpleNamespace(status="running", document_id=1, public_id="task1")

    class Result:
        def scalar_one_or_none(self) -> Any:
            return task

    db = _MemDb()

    async def execute(_stmt: Any) -> Result:
        return Result()

    db.execute = execute  # type: ignore[method-assign]
    monkeypatch.setattr(
        "api.services.knowledge.bases.get_base_by_public_id",
        AsyncMock(return_value=SimpleNamespace(id=2, public_id="kb1")),
    )
    with pytest.raises(AppError) as ei:
        await q.cancel_ingest_task(db, base_public_id="kb1", task_public_id="task1")  # type: ignore[arg-type]
    assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_enqueue_unindexed_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.services.knowledge.ingest import _enqueue_unindexed_ready

    ready = _doc(id=1, review_status="pending", chunk_count=0, status="ready")
    busy = _doc(id=2, review_status="pending", chunk_count=0, status="ready")
    rejected = _doc(id=3, review_status="rejected", chunk_count=0, status="ready")
    drawing = _doc(id=4, kind="drawing", chunk_count=0, status="ready")
    indexed = _doc(id=5, review_status="approved", chunk_count=8, status="ready")
    enqueued: list[int] = []

    async def fake_enqueue(_db: Any, *, base: Any, doc: Any, force_reextract: bool = False) -> Any:
        del _db, base, force_reextract
        enqueued.append(int(doc.id))
        return SimpleNamespace(public_id="t1")

    monkeypatch.setattr("api.services.knowledge.queue.enqueue_ingest_task", fake_enqueue)

    class Result:
        def scalars(self) -> Result:
            return self

        def all(self) -> list[int]:
            return [2]

    class Db:
        async def execute(self, _stmt: Any) -> Result:
            return Result()

    await _enqueue_unindexed_ready(
        Db(),  # type: ignore[arg-type]
        SimpleNamespace(id=9),
        [ready, busy, rejected, drawing, indexed],
    )
    assert enqueued == [1]
    assert ready.review_status == "approved"
    assert ready.status == "parsing"


def test_qdrant_wrap_unauthorized() -> None:
    from api.services.knowledge.qdrant_store import _wrap_qdrant_exc
    from common.errors import AppError

    err = _wrap_qdrant_exc(RuntimeError("Unexpected Response: 401 (Unauthorized)"))
    assert isinstance(err, AppError)
    assert err.status_code == 502
    assert "向量库鉴权" in err.msg
    store = object.__new__(QdrantKnowledgeStore)
    store.settings = SimpleNamespace(qdrant_quantization="none")
    assert store._quantization_config() is None
    store.settings = SimpleNamespace(qdrant_quantization="int8")
    int8 = store._quantization_config()
    assert int8 is not None
    store.settings = SimpleNamespace(qdrant_quantization="binary")
    binary = store._quantization_config()
    assert binary is not None


def test_parse_timeout_seconds_respects_config(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.services.knowledge import queue as ingest_queue

    monkeypatch.setattr(
        "api.services.knowledge.queue.get_settings",
        lambda: SimpleNamespace(kb_parse_timeout_seconds=3600),
    )
    assert ingest_queue._parse_timeout_seconds() == 3600

    monkeypatch.setattr(
        "api.services.knowledge.queue.get_settings",
        lambda: SimpleNamespace(kb_parse_timeout_seconds=10),
    )
    assert ingest_queue._parse_timeout_seconds() == 60


@pytest.mark.asyncio
async def test_fail_timed_out_ingest_tasks_marks_stale_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import timedelta

    from api.services.knowledge import queue as ingest_queue

    monkeypatch.setattr(
        "api.services.knowledge.queue.get_settings",
        lambda: SimpleNamespace(kb_parse_timeout_seconds=600),
    )

    stale_started = datetime.now(timezone.utc) - timedelta(seconds=900)
    task = SimpleNamespace(
        id=1,
        document_id=10,
        status="running",
        started_at=stale_started,
        created_at=stale_started,
        progress=20,
        error_msg=None,
        finished_at=None,
    )
    doc = SimpleNamespace(id=10, status="parsing", error_msg=None)

    class Db:
        async def execute(self, _stmt: Any) -> Any:
            class Result:
                def scalars(self) -> Result:
                    return self

                def all(self) -> list[Any]:
                    return [task]

            return Result()

        async def get(self, _model: Any, pk: int) -> Any:
            assert pk == 10
            return doc

        async def commit(self) -> None:
            return None

    class SessionCtx:
        async def __aenter__(self) -> Db:
            return Db()

        async def __aexit__(self, *_args: Any) -> None:
            return None

    monkeypatch.setattr(ingest_queue, "AsyncSessionLocal", SessionCtx)

    n = await ingest_queue.fail_timed_out_ingest_tasks()
    assert n == 1
    assert task.status == "failed"
    assert doc.status == "failed"
    assert "解析超时" in (task.error_msg or "")
