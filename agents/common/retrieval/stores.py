from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from .types import RetrievalChunk


class EphemeralIndex:
    """Simple in-memory ANN substitute for one-shot retrieval."""

    def __init__(self) -> None:
        self.chunks: List[RetrievalChunk] = []
        self.vectors: List[List[float]] = []

    def build(self, chunks: Sequence[RetrievalChunk], vectors: Sequence[Sequence[float]]) -> None:
        self.chunks = list(chunks)
        self.vectors = [list(vec) for vec in vectors]

    def search(
        self,
        query_vector: Sequence[float],
        top_n: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Tuple[RetrievalChunk, float]]:
        if not self.chunks or not self.vectors:
            return []

        filters = filters or {}
        scored: List[Tuple[int, float]] = []
        for idx, (chunk, vector) in enumerate(zip(self.chunks, self.vectors)):
            if not _matches_filters(chunk, filters):
                continue
            score = _cosine_similarity(query_vector, vector)
            scored.append((idx, score))

        scored.sort(key=lambda item: item[1], reverse=True)
        limit = max(1, min(top_n, len(scored)))
        return [(self.chunks[idx], score) for idx, score in scored[:limit]]


class MilvusSessionStore:
    """Milvus Lite-backed session store for multi-turn retrieval reuse."""

    COLLECTION_NAME = "retrieval_chunks"

    def __init__(
        self,
        uri: str,
        ttl_minutes: int = 60,
    ) -> None:
        self.uri = uri
        self.ttl_minutes = max(1, int(ttl_minutes))
        self._dimension: Optional[int] = None

        try:
            from pymilvus import MilvusClient  # type: ignore
        except Exception as ex:
            raise RuntimeError(
                "pymilvus is required for session mode. Install pymilvus>=2.5.0"
            ) from ex

        parent = os.path.dirname(uri)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.client = MilvusClient(uri=uri)

    def upsert(self, session_id: str, chunks: Sequence[RetrievalChunk], vectors: Sequence[Sequence[float]]) -> None:
        if not chunks:
            return
        if len(chunks) != len(vectors):
            raise ValueError("chunks/vectors length mismatch")
        dimension = len(vectors[0]) if vectors and vectors[0] else 0
        if dimension <= 0:
            raise ValueError("invalid embedding dimension")
        self._ensure_collection(dimension)

        expires_at = (datetime.utcnow() + timedelta(minutes=self.ttl_minutes)).isoformat()
        rows = []
        for chunk, vector in zip(chunks, vectors):
            rows.append(
                {
                    "session_id": session_id,
                    "chunk_id": chunk.chunk_id,
                    "source_type": chunk.source_type,
                    "modality": chunk.modality,
                    "text": chunk.text,
                    "metadata_json": json.dumps(chunk.metadata, ensure_ascii=False),
                    "expires_at": expires_at,
                    "embedding": [float(v) for v in vector],
                }
            )

        self.client.insert(collection_name=self.COLLECTION_NAME, data=rows)

    def query(
        self,
        session_id: str,
        query_vector: Sequence[float],
        top_n: int,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Tuple[RetrievalChunk, float]]:
        if not self.client.has_collection(collection_name=self.COLLECTION_NAME):
            return []

        self.cleanup_expired()

        expr = f'session_id == "{session_id}"'

        hits = self.client.search(
            collection_name=self.COLLECTION_NAME,
            data=[list(query_vector)],
            limit=max(1, int(top_n)),
            filter=expr,
            output_fields=["chunk_id", "source_type", "modality", "text", "metadata_json"],
        )

        results: List[Tuple[RetrievalChunk, float]] = []
        for item in (hits[0] if hits else []):
            entity = item.get("entity", item)
            metadata_json = entity.get("metadata_json") or "{}"
            try:
                metadata = json.loads(metadata_json)
            except Exception:
                metadata = {}

            chunk = RetrievalChunk(
                chunk_id=str(entity.get("chunk_id", "")),
                text=str(entity.get("text", "")),
                source_type=str(entity.get("source_type", "unknown")),
                modality=str(entity.get("modality", "text")),
                metadata=metadata if isinstance(metadata, dict) else {},
            )
            score = float(item.get("distance", item.get("score", 0.0)))
            results.append((chunk, score))

        filters = filters or {}
        if filters:
            results = [(chunk, score) for chunk, score in results if _matches_filters(chunk, filters)]

        results.sort(key=lambda pair: pair[1], reverse=True)
        return results

    def cleanup_expired(self) -> None:
        now_iso = datetime.utcnow().isoformat()
        try:
            self.client.delete(
                collection_name=self.COLLECTION_NAME,
                filter=f'expires_at < "{now_iso}"',
            )
        except Exception as ex:
            logger.warning(f"Milvus TTL cleanup failed: {ex}")

    def drop_session(self, session_id: str) -> None:
        self.client.delete(
            collection_name=self.COLLECTION_NAME,
            filter=f'session_id == "{session_id}"',
        )

    def _ensure_collection(self, dimension: int) -> None:
        if self.client.has_collection(collection_name=self.COLLECTION_NAME):
            return
        self._dimension = int(dimension)
        self.client.create_collection(
            collection_name=self.COLLECTION_NAME,
            dimension=self._dimension,
            metric_type="COSINE",
            consistency_level="Strong",
            auto_id=True,
            enable_dynamic_field=True,
        )


def _matches_filters(chunk: RetrievalChunk, filters: Dict[str, Any]) -> bool:
    if not filters:
        return True

    sources = filters.get("sources") or []
    if sources:
        source_set = {str(item).strip().lower() for item in sources}
        if chunk.source_type.lower() not in source_set:
            return False

    before_date = str(filters.get("before_date") or "").strip()
    if before_date:
        published = str(chunk.metadata.get("published_date") or "").strip()
        if published and published > before_date:
            return False

    return True


def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) != len(b):
        limit = min(len(a), len(b))
        a = a[:limit]
        b = b[:limit]

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for av, bv in zip(a, b):
        dot += av * bv
        norm_a += av * av
        norm_b += bv * bv

    denom = math.sqrt(norm_a) * math.sqrt(norm_b)
    if denom <= 1e-12:
        return 0.0
    return dot / denom
