"""Vector failures must not block deleting real files and persisted records."""
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from api.services.knowledge import ingest
from api.services.knowledge.asr import pipeline
from common.errors import AppError, ErrorCode
from db.base import Base
from db.models.knowledge import (
    KnowledgeBase,
    KnowledgeBaseAcl,
    KnowledgeDocument,
    KnowledgeDocumentReview,
    KnowledgeIngestTask,
)


class AsyncDatabase:
    """Use actual SQLite ORM deletions through the service's async interface."""
    def __init__(self, session):
        self.session = session
        self.flush_count = 0

    async def execute(self, statement):
        return self.session.execute(statement)

    async def delete(self, document):
        self.session.delete(document)

    async def flush(self):
        self.flush_count += 1
        self.session.flush()

    async def commit(self):
        self.session.commit()

    async def refresh(self, row):
        self.session.refresh(row)

    async def get(self, model, key):
        return self.session.get(model, key)


@pytest.fixture
def originals(tmp_path, monkeypatch):
    root = tmp_path / 'storage'
    root.mkdir()
    monkeypatch.setattr(ingest, '_storage_root', lambda: root)
    monkeypatch.setattr(pipeline, '_storage_root', lambda: root)
    base = SimpleNamespace(id=1, public_id='test-library')
    monkeypatch.setattr(ingest, 'get_base_by_public_id', AsyncMock(return_value=base))
    engine = create_engine('sqlite:///' + (tmp_path / 'records.sqlite').as_posix())
    Base.metadata.create_all(engine, tables=[model.__table__ for model in (
        KnowledgeBase, KnowledgeBaseAcl, KnowledgeDocument, KnowledgeDocumentReview, KnowledgeIngestTask)])
    paths = {}
    with Session(engine) as session:
        session.add(KnowledgeBase(id=1, public_id=base.public_id, name='测试资料库'))
        for index, key in enumerate(('parent', 'child', 'untouched'), 1):
            original = f'knowledge/{base.public_id}/{key}.pdf'
            session.add(KnowledgeDocument(
                id=index, public_id=key, base_id=base.id, name=key, source='file',
                parent_id='parent' if key == 'child' else None,
                # Exercise both original path representations without changing
                # their existing priority in production code.
                file_key=original if key == 'child' else None,
                storage_path=original, status='ready', review_status='approved'))
            paths[key] = []
            for relative in (original, ingest._extracted_text_key(base.public_id, key),
                             pipeline._cues_key(base.public_id, key)):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'test original or sidecar')
                paths[key].append(path)
        session.commit()
    yield engine, base, paths
    engine.dispose()


def unavailable():
    return AppError(ErrorCode.INTERNAL, 'vector service unavailable', status_code=503)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['factory_503', 'factory_connect', 'delete_503', 'child_delete_503', 'healthy'])
async def test_deletion_cleans_files_and_database_despite_vectors(originals, monkeypatch, caplog, failure):
    engine, base, paths = originals
    delete_vectors = Mock()
    factory = Mock(return_value=SimpleNamespace(delete_by_doc_id=delete_vectors))
    if failure == 'factory_503':
        factory.side_effect = unavailable()
    elif failure == 'factory_connect':
        factory.side_effect = ConnectionError('connection refused')
    elif failure == 'delete_503':
        delete_vectors.side_effect = unavailable()
    elif failure == 'child_delete_503':
        delete_vectors.side_effect = [unavailable(), None]
    monkeypatch.setattr(ingest, 'get_qdrant_store', factory)

    with Session(engine) as session, caplog.at_level(logging.WARNING, logger='api.kb.ingest'):
        db = AsyncDatabase(session)
        result = await ingest.delete_document_record(db, base_public_id=base.public_id, doc_public_id='parent')
        assert result['id'] == 'parent'
        assert db.flush_count == 1
        # Service still leaves commit ownership to its caller.
        session.commit()
    with Session(engine) as session:
        assert list(session.scalars(select(KnowledgeDocument.public_id))) == ['untouched']
    assert all(not path.exists() for key in ('parent', 'child') for path in paths[key])
    assert all(path.is_file() for path in paths['untouched'])
    factory.assert_called_once_with()
    if failure.startswith('factory'):
        delete_vectors.assert_not_called()
    else:
        assert delete_vectors.call_args_list == [call('child'), call('parent')]
    if failure != 'healthy':
        assert any('failed' in record.message for record in caplog.records)
    else:
        assert not caplog.records


@pytest.mark.asyncio
async def test_nonexistent_document_does_not_contact_vector_store(originals, monkeypatch):
    engine, base, paths = originals
    factory = Mock(side_effect=AssertionError('no vector call for missing document'))
    monkeypatch.setattr(ingest, 'get_qdrant_store', factory)
    with Session(engine) as session:
        db = AsyncDatabase(session)
        assert await ingest.delete_document_record(db, base_public_id=base.public_id, doc_public_id='missing') is None
        assert db.flush_count == 0
    factory.assert_not_called()
    assert all(path.is_file() for group in paths.values() for path in group)


@pytest.mark.asyncio
async def test_healthy_vector_store_removes_only_parent_and_child_vectors(originals, monkeypatch):
    from qdrant_client import QdrantClient
    from qdrant_client.http import models
    from api.services.knowledge.qdrant_store import QdrantKnowledgeStore

    engine, base, _paths = originals
    client = QdrantClient(location=':memory:')
    collection = 'delete-regression'
    try:
        client.create_collection(collection, vectors_config=models.VectorParams(size=1, distance=models.Distance.COSINE))
        client.upsert(collection, points=[models.PointStruct(id=index, vector=[1.0], payload={'doc_id': key})
                                         for index, key in enumerate(('parent', 'child', 'untouched'), 1)])
        # Exercise the unchanged production vector deletion method without
        # contacting or stopping the user's running Qdrant service.
        store = object.__new__(QdrantKnowledgeStore)
        store.client = client
        store.collection = collection
        monkeypatch.setattr(ingest, 'get_qdrant_store', lambda: store)
        with Session(engine) as session:
            await ingest.delete_document_record(AsyncDatabase(session), base_public_id=base.public_id, doc_public_id='parent')
            session.commit()
        points, _ = client.scroll(collection, limit=10)
        assert [point.payload['doc_id'] for point in points] == ['untouched']
    finally:
        client.close()


_PARSED_TEXT = '这是已核实的通用资料说明，供资料查询与生成引用。' * 30 + '正文尾部校验标记。'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['factory_502', 'factory_503', 'factory_connect', 'delete_503', 'upsert_503'])
async def test_vector_failure_keeps_original_and_pending_parsed_content(originals, monkeypatch, caplog, failure):
    engine, base, paths = originals
    store = Mock()
    factory = Mock(return_value=store)
    if failure.startswith('factory'):
        factory.side_effect = (ConnectionError('connection refused') if failure == 'factory_connect'
                               else AppError(ErrorCode.INTERNAL, 'Qdrant unavailable',
                                             status_code=502 if failure == 'factory_502' else 503))
    elif failure == 'delete_503':
        store.delete_by_doc_id.side_effect = unavailable()
    else:
        store.upsert_chunks.side_effect = unavailable()
    monkeypatch.setattr(ingest, 'get_qdrant_store', factory)
    embedder = SimpleNamespace(embed_documents=AsyncMock(side_effect=lambda chunks: [[1.0] for _ in chunks]))
    embedding_client = AsyncMock(return_value=embedder)
    monkeypatch.setattr(ingest, 'get_embedding_client', embedding_client)
    with Session(engine) as session, caplog.at_level(logging.WARNING, logger='api.kb.ingest'):
        doc = session.get(KnowledgeDocument, 1)
        doc.summary = 'PERFJSON:{"verified":"preserved"}'
        result = await ingest._vectorize_document(AsyncDatabase(session), doc, _PARSED_TEXT)
        assert result is False
        session.commit()
    with Session(engine) as session:
        doc = session.get(KnowledgeDocument, 1)
        assert doc.status == 'ready' and doc.chunk_count == 0
        assert doc.char_count == len(_PARSED_TEXT)
        assert doc.error_msg.startswith('待向量化')
        assert doc.summary == 'PERFJSON:{"verified":"preserved"}'
    assert paths['parent'][0].is_file()
    assert ingest.read_extracted_text(base.public_id, 'parent') == _PARSED_TEXT
    assert any('vectorization deferred' in record.message for record in caplog.records)
    if failure.startswith('factory'):
        embedding_client.assert_not_awaited()


@pytest.mark.asyncio
async def test_qdrant_recovery_reindexes_full_cached_text_and_clears_pending(originals, monkeypatch):
    from qdrant_client import QdrantClient
    from api.services.knowledge import review
    from api.services.knowledge.qdrant_store import QdrantKnowledgeStore

    engine, base, paths = originals
    monkeypatch.setattr(ingest, 'get_qdrant_store', Mock(side_effect=unavailable()))
    embedder = SimpleNamespace(embed_documents=AsyncMock(side_effect=lambda chunks: [[1.0] for _ in chunks]))
    monkeypatch.setattr(ingest, 'get_embedding_client', AsyncMock(return_value=embedder))
    with Session(engine) as session:
        for index in (2, 3):
            session.get(KnowledgeDocument, index).kind = 'drawing'
        doc = session.get(KnowledgeDocument, 1)
        await ingest._vectorize_document(AsyncDatabase(session), doc, _PARSED_TEXT)
        session.commit()
    # Reindex while still down must not report a successful vector write.
    with Session(engine) as session:
        stats = await review.reindex_approved_documents(AsyncDatabase(session))
        assert stats == {'reindexed': 0, 'failed': 1, 'total': 1}
    client = QdrantClient(location=':memory:')
    try:
        store = object.__new__(QdrantKnowledgeStore)
        store.client = client
        store.collection = 'pending-reindex-regression'
        store.settings = SimpleNamespace(qdrant_quantization='none')
        store._ensure_payload_indexes = lambda: None
        monkeypatch.setattr(ingest, 'get_qdrant_store', lambda: store)
        with Session(engine) as session:
            stats = await review.reindex_approved_documents(AsyncDatabase(session))
            assert stats == {'reindexed': 1, 'failed': 0, 'total': 1}
        with Session(engine) as session:
            doc = session.get(KnowledgeDocument, 1)
            assert doc.status == 'ready' and doc.chunk_count > 0 and doc.error_msg is None
            count = doc.chunk_count
        chunks = store.list_chunks_by_doc_id('parent')
        assert len(chunks) == count
        assert '正文尾部校验标记' in ''.join(chunk['content'] for chunk in chunks)
        with Session(engine) as session:
            await review.reindex_approved_documents(AsyncDatabase(session))
        assert len(store.list_chunks_by_doc_id('parent')) == count
        assert paths['parent'][0].is_file()
    finally:
        client.close()


@pytest.mark.asyncio
async def test_background_task_succeeds_with_pending_vectors(originals, monkeypatch):
    from api.services.knowledge import queue

    engine, base, paths = originals

    @asynccontextmanager
    async def sessions():
        with Session(engine) as session:
            yield AsyncDatabase(session)

    monkeypatch.setattr(ingest, 'AsyncSessionLocal', sessions)
    monkeypatch.setattr(queue, 'AsyncSessionLocal', sessions)
    monkeypatch.setattr(ingest, 'get_qdrant_store', Mock(side_effect=unavailable()))
    monkeypatch.setattr(ingest, 'get_embedding_client', AsyncMock(side_effect=AssertionError('must defer before embedding')))
    ingest.write_extracted_text(base.public_id, 'parent', _PARSED_TEXT)
    with Session(engine) as session:
        session.get(KnowledgeDocument, 1).status = 'parsing'
        session.add(KnowledgeIngestTask(id=1, public_id='test-task', base_id=base.id,
                                       document_id=1, status='running', progress=5))
        session.commit()
    await queue._run_claimed(SimpleNamespace(id=1, public_id='test-task', force_reextract=False))
    with Session(engine) as session:
        task = session.get(KnowledgeIngestTask, 1)
        doc = session.get(KnowledgeDocument, 1)
        assert task.status == 'succeeded' and task.progress == 100
        assert doc.status == 'ready' and doc.chunk_count == 0 and doc.error_msg.startswith('待向量化')
    assert paths['parent'][0].is_file()
    assert ingest.read_extracted_text(base.public_id, 'parent') == _PARSED_TEXT


@pytest.mark.asyncio
async def test_embedding_failure_is_not_silently_reclassified(originals, monkeypatch):
    engine, _base, _paths = originals
    monkeypatch.setattr(ingest, 'get_qdrant_store', Mock(return_value=Mock()))
    monkeypatch.setattr(ingest, 'get_embedding_client', AsyncMock(side_effect=AppError(
        ErrorCode.INTERNAL, 'Embedding authentication failed', status_code=502)))
    with Session(engine) as session:
        doc = session.get(KnowledgeDocument, 1)
        with pytest.raises(AppError, match='Embedding authentication failed'):
            await ingest._vectorize_document(AsyncDatabase(session), doc, _PARSED_TEXT)
        assert not (doc.error_msg or '').startswith('待向量化')
