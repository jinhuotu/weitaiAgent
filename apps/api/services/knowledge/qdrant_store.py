"""Qdrant 知识库向量存储。集合名来自配置 qdrant_collection（默认 weitai_knowledge）。"""

from __future__ import annotations
from functools import lru_cache
from typing import Any
from uuid import uuid4
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm
from common.config import Settings, get_settings
from common.errors import AppError, ErrorCode
from common.logging import get_logger
logger = get_logger(__name__)


def _wrap_qdrant_exc(exc: Exception) -> AppError:
    if isinstance(exc, AppError):
        return exc
    text = str(exc) or type(exc).__name__
    lowered = text.lower()
    if "401" in text or "unauthorized" in lowered or "403" in text or "forbidden" in lowered:
        return AppError(
            ErrorCode.INTERNAL,
            "向量库鉴权失败，请检查 QDRANT_API_KEY 是否与当前 Qdrant 服务匹配",
            status_code=502,
        )
    return AppError(
        ErrorCode.INTERNAL,
        (
            "cannot connect to Qdrant at "
            f"{get_settings().qdrant_url}; start Docker service "
            "`docker compose -f infra/docker-compose.yml up -d qdrant` "
            f"({text})"
        ),
        status_code=503,
    )


class QdrantKnowledgeStore:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        kwargs: dict[str, Any] = {"url": self.settings.qdrant_url}
        if self.settings.qdrant_api_key:
            kwargs["api_key"] = self.settings.qdrant_api_key
        self.client = QdrantClient(**kwargs)
        self.collection = self.settings.qdrant_collection
        try:
            self.client.get_collections()
        except Exception as exc:  # noqa: BLE001
            raise _wrap_qdrant_exc(exc) from exc
    def ensure_collection(self, vector_size: int) -> None:
        existing = {c.name for c in self.client.get_collections().collections}
        if self.collection in existing:
            info = self.client.get_collection(self.collection)
            try:
                current = info.config.params.vectors.size  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                current = vector_size
            if current != vector_size:
                raise AppError(
                    ErrorCode.INTERNAL,
                    (
                        f"qdrant collection '{self.collection}' dim={current}, "
                        f"embedding dim={vector_size}; recreate collection or align embedding"
                    ),
                    status_code=500,
                )
            self._ensure_payload_indexes()
            return
        self.client.create_collection(
            collection_name=self.collection,
            vectors_config=qm.VectorParams(size=vector_size, distance=qm.Distance.COSINE),
            quantization_config=self._quantization_config(),
        )
        logger.info(
            "created qdrant collection=%s dim=%s quant=%s",
            self.collection,
            vector_size,
            (self.settings.qdrant_quantization or "none"),
        )
        self._ensure_payload_indexes()

    def _quantization_config(self) -> qm.QuantizationConfig | None:
        name = (self.settings.qdrant_quantization or "none").strip().lower()
        if name in {"", "none", "off", "false"}:
            return None
        if name in {"int8", "scalar"}:
            return qm.ScalarQuantization(
                scalar=qm.ScalarQuantizationConfig(
                    type=qm.ScalarType.INT8,
                    quantile=0.99,
                    always_ram=True,
                )
            )
        if name in {"binary", "bin"}:
            return qm.BinaryQuantization(
                binary=qm.BinaryQuantizationConfig(always_ram=True)
            )
        raise AppError(
            ErrorCode.VALIDATION,
            f"不支持的 QDRANT_QUANTIZATION={name}（可用 none / int8 / binary）",
            status_code=422,
        )

    def collection_info(self) -> dict[str, Any]:
        existing = {c.name for c in self.client.get_collections().collections}
        if self.collection not in existing:
            return {
                "name": self.collection,
                "exists": False,
                "points": 0,
                "vectorSize": None,
                "quantization": (self.settings.qdrant_quantization or "none"),
                "envQuantization": (self.settings.qdrant_quantization or "none"),
            }
        info = self.client.get_collection(self.collection)
        dim = None
        try:
            dim = info.config.params.vectors.size  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            dim = None
        quant = "none"
        try:
            qcfg = info.config.quantization_config
            if qcfg is not None:
                quant = type(qcfg).__name__.replace("Quantization", "").lower() or "on"
        except Exception:  # noqa: BLE001
            quant = "unknown"
        return {
            "name": self.collection,
            "exists": True,
            "points": int(getattr(info, "points_count", 0) or 0),
            "vectorSize": dim,
            "quantization": quant,
            "envQuantization": (self.settings.qdrant_quantization or "none"),
        }

    def recreate_collection(self, vector_size: int) -> None:
        existing = {c.name for c in self.client.get_collections().collections}
        if self.collection in existing:
            self.client.delete_collection(self.collection)
        self.client.create_collection(
            collection_name=self.collection,
            vectors_config=qm.VectorParams(size=vector_size, distance=qm.Distance.COSINE),
            quantization_config=self._quantization_config(),
        )
        self._ensure_payload_indexes()
        logger.info("recreated qdrant collection=%s dim=%s", self.collection, vector_size)

    def _ensure_payload_indexes(self) -> None:
        for field in ("doc_id", "kb_id", "review"):
            try:
                self.client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field,
                    field_schema=qm.PayloadSchemaType.KEYWORD,
                )
            except Exception:  # noqa: BLE001
                pass

    def upsert_chunks(
        self,
        *,
        public_id: str,
        kb_id: str,
        name: str,
        source: str,
        tags: list[str] | None,
        chunks: list[str],
        vectors: list[list[float]],
    ) -> list[str]:
        if len(chunks) != len(vectors):
            raise AppError(ErrorCode.INTERNAL, "chunks/vectors size mismatch", status_code=500)
        self.ensure_collection(len(vectors[0]))
        point_ids: list[str] = []
        points: list[qm.PointStruct] = []
        for idx, (content, vector) in enumerate(zip(chunks, vectors, strict=True)):
            pid = str(uuid4())
            point_ids.append(pid)
            points.append(
                qm.PointStruct(
                    id=pid,
                    vector=vector,
                    payload={
                        "doc_id": public_id,
                        "kb_id": kb_id,
                        "name": name,
                        "source": source,
                        "chunk_index": idx,
                        "content": content,
                        "tags": tags or [],
                        "review": "approved",
                    },
                )
            )
        self.client.upsert(collection_name=self.collection, points=points, wait=True)
        return point_ids
    def list_chunks_by_doc_id(
        self,
        public_id: str,
        *,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """按 doc_id 拉回入库文本块（用于资料预览）。"""
        existing = {c.name for c in self.client.get_collections().collections}
        if self.collection not in existing:
            return []
        out: list[dict[str, Any]] = []
        offset = None
        while len(out) < limit:
            batch_limit = min(100, limit - len(out))
            points, offset = self.client.scroll(
                collection_name=self.collection,
                scroll_filter=qm.Filter(
                    must=[
                        qm.FieldCondition(
                            key="doc_id",
                            match=qm.MatchValue(value=public_id),
                        )
                    ]
                ),
                limit=batch_limit,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                out.append(
                    {
                        "chunkIndex": int(payload.get("chunk_index") or 0),
                        "content": str(payload.get("content") or ""),
                    }
                )
            if offset is None or not points:
                break
        out.sort(key=lambda x: x["chunkIndex"])
        return out

    def fetch_neighbor_chunks(
        self,
        seeds: list[dict[str, Any]],
        *,
        window: int = 1,
    ) -> list[dict[str, Any]]:
        """把命中块的前后页一并取出（small-to-big），补全条款上下文。"""
        if window <= 0 or not seeds:
            return []
        wanted: dict[tuple[str, int], float] = {}
        for h in seeds:
            doc_id = str(h.get("doc_id") or "")
            if not doc_id:
                continue
            try:
                idx = int(h.get("chunk_index") or 0)
            except (TypeError, ValueError):
                continue
            score = float(h.get("score") or 0.0)
            for j in range(max(0, idx - window), idx + window + 1):
                key = (doc_id, j)
                wanted[key] = max(wanted.get(key, 0.0), score)

        out: list[dict[str, Any]] = []
        seen_seed = {
            (str(h.get("doc_id") or ""), int(h.get("chunk_index") or 0)) for h in seeds
        }
        by_doc: dict[str, list[int]] = {}
        for doc_id, idx in wanted:
            by_doc.setdefault(doc_id, []).append(idx)

        existing = {c.name for c in self.client.get_collections().collections}
        if self.collection not in existing:
            return []

        for doc_id, indexes in by_doc.items():
            points, _offset = self.client.scroll(
                collection_name=self.collection,
                scroll_filter=qm.Filter(
                    must=[
                        qm.FieldCondition(
                            key="doc_id",
                            match=qm.MatchValue(value=doc_id),
                        ),
                        qm.FieldCondition(
                            key="chunk_index",
                            match=qm.MatchAny(any=sorted(set(indexes))),
                        ),
                    ]
                ),
                limit=max(8, len(set(indexes))),
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                idx = int(payload.get("chunk_index") or 0)
                key = (doc_id, idx)
                if key in seen_seed:
                    continue
                parent = wanted.get(key, 0.0)
                out.append(
                    {
                        "content": payload.get("content", ""),
                        "score": round(parent * 0.92, 6),
                        "vector_score": 0.0,
                        "keyword_score": 0.0,
                        "doc_id": payload.get("doc_id"),
                        "kb_id": payload.get("kb_id"),
                        "name": payload.get("name"),
                        "chunk_index": idx,
                        "tags": payload.get("tags") or [],
                        "neighbor": True,
                    }
                )
        return out

    def search_text_contains(
        self,
        *,
        needles: list[str],
        kb_id: str | None = None,
        kb_ids: list[str] | None = None,
        limit: int = 24,
        max_scan: int = 20_000,
    ) -> list[dict[str, Any]]:
        """按正文子串召回。时序表所有行向量几乎一样，精确时间点必须走字面匹配。"""
        keys = [n.strip() for n in needles if n and n.strip()]
        if not keys:
            return []
        existing = {c.name for c in self.client.get_collections().collections}
        if self.collection not in existing:
            return []
        query_filter = None
        ids = [x for x in (kb_ids or []) if x]
        if not ids and kb_id:
            ids = [kb_id]
        if ids:
            if len(ids) == 1:
                match = qm.MatchValue(value=ids[0])
            else:
                match = qm.MatchAny(any=ids)
            query_filter = qm.Filter(
                must=[qm.FieldCondition(key="kb_id", match=match)]
            )
        out: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        offset = None
        scanned = 0
        batch = min(256, max(32, limit * 4))
        while scanned < max_scan and len(out) < limit:
            points, offset = self.client.scroll(
                collection_name=self.collection,
                scroll_filter=query_filter,
                limit=min(batch, max_scan - scanned),
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            if not points:
                break
            scanned += len(points)
            for point in points:
                payload = point.payload or {}
                content = str(payload.get("content") or "")
                if not any(key in content for key in keys):
                    continue
                doc_id = str(payload.get("doc_id") or "")
                idx = int(payload.get("chunk_index") or 0)
                mark = (doc_id, idx)
                if mark in seen:
                    continue
                seen.add(mark)
                out.append(
                    {
                        "content": content,
                        "score": 1.0,
                        "doc_id": payload.get("doc_id"),
                        "kb_id": payload.get("kb_id"),
                        "name": payload.get("name"),
                        "chunk_index": idx,
                        "tags": payload.get("tags") or [],
                        "lexical": True,
                    }
                )
                if len(out) >= limit:
                    break
            if offset is None:
                break
        if out:
            logger.info(
                "lexical kb search needles=%s hits=%s scanned=%s",
                keys[:3],
                len(out),
                scanned,
            )
        return out

    def delete_by_doc_id(self, public_id: str) -> None:
        existing = {c.name for c in self.client.get_collections().collections}
        if self.collection not in existing:
            return
        self.client.delete(
            collection_name=self.collection,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(
                    must=[
                        qm.FieldCondition(
                            key="doc_id",
                            match=qm.MatchValue(value=public_id),
                        )
                    ]
                )
            ),
            wait=True,
        )
    def delete_by_kb_id(self, kb_id: str) -> None:
        existing = {c.name for c in self.client.get_collections().collections}
        if self.collection not in existing:
            return
        self.client.delete(
            collection_name=self.collection,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(
                    must=[
                        qm.FieldCondition(
                            key="kb_id",
                            match=qm.MatchValue(value=kb_id),
                        )
                    ]
                )
            ),
            wait=True,
        )
    def search(
        self,
        *,
        vector: list[float],
        top_k: int = 5,
        min_score: float = 0.0,
        kb_id: str | None = None,
        kb_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        query_filter = None
        ids = [x for x in (kb_ids or []) if x]
        if not ids and kb_id:
            ids = [kb_id]
        if ids:
            if len(ids) == 1:
                match = qm.MatchValue(value=ids[0])
            else:
                match = qm.MatchAny(any=ids)
            query_filter = qm.Filter(
                must=[
                    qm.FieldCondition(
                        key="kb_id",
                        match=match,
                    )
                ]
            )
        try:
            existing = {c.name for c in self.client.get_collections().collections}
            if self.collection not in existing:
                return []
            self.ensure_collection(len(vector))
            response = self.client.query_points(
                collection_name=self.collection,
                query=vector,
                query_filter=query_filter,
                limit=top_k,
                with_payload=True,
                score_threshold=min_score if min_score > 0 else None,
            )
        except AppError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _wrap_qdrant_exc(exc) from exc
        out: list[dict[str, Any]] = []
        for hit in response.points:
            payload = hit.payload or {}
            out.append(
                {
                    "content": payload.get("content", ""),
                    "score": float(hit.score or 0.0),
                    "doc_id": payload.get("doc_id"),
                    "kb_id": payload.get("kb_id"),
                    "name": payload.get("name"),
                    "chunk_index": payload.get("chunk_index"),
                    "tags": payload.get("tags") or [],
                }
            )
        return out


@lru_cache
def get_qdrant_store() -> QdrantKnowledgeStore:
    return QdrantKnowledgeStore()
