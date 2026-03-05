from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from config import settings

from .chunkers import chunk_documents
from .qwen_client import DashScopeRetrievalClient
from .stores import EphemeralIndex, MilvusSessionStore
from .types import RetrievalChunk, RetrievalHit, RetrievalRequest


class RetrievalService:
    """Unified retrieval service entry for ephemeral and session modes."""

    def __init__(self) -> None:
        self.client = DashScopeRetrievalClient(
            api_key=getattr(settings, "QWEN_API_KEY", "") or os.getenv("DASHSCOPE_API_KEY", ""),
            embedding_model=getattr(settings, "QWEN_EMBEDDING_MODEL", "qwen3-vl-embedding"),
            text_rerank_model=getattr(settings, "QWEN_TEXT_RERANK_MODEL", "qwen3-reranker"),
            vl_rerank_model=getattr(settings, "QWEN_VL_RERANK_MODEL", "qwen3-vl-reranker"),
            allow_fake_model=bool(getattr(settings, "RETRIEVAL_ALLOW_FAKE_MODEL", True)),
        )

        self._session_store: Optional[MilvusSessionStore] = None

    def retrieve(self, request: RetrievalRequest) -> List[RetrievalHit]:
        query_text = str(request.query_text or "").strip()
        query_image = str(request.query_image or "").strip()

        if not query_text and not query_image:
            raise ValueError("query_text or query_image is required")

        top_n = max(1, int(request.top_n or 20))
        top_k = max(1, int(request.top_k or 5))

        if str(request.mode).strip().lower() == "session":
            pairs = self._retrieve_session_mode(
                inputs=request.inputs,
                session_id=str(request.session_id or "").strip(),
                query_text=query_text,
                query_image=query_image,
                filters=request.filters,
                top_n=top_n,
            )
        else:
            pairs = self._retrieve_ephemeral_mode(
                inputs=request.inputs,
                query_text=query_text,
                query_image=query_image,
                filters=request.filters,
                top_n=top_n,
            )

        if not pairs:
            return []

        reranked = self._rerank(
            query_text=query_text,
            query_image=query_image,
            pairs=pairs,
            top_n=top_n,
        )

        reranked = self._dedupe_and_diversify(reranked, per_doc_limit=2)
        reranked = reranked[:top_k]

        return [
            self._to_hit(chunk=chunk, score=score)
            for chunk, score in reranked
        ]

    def drop_session(self, session_id: str) -> None:
        if not session_id:
            return
        store = self._get_session_store()
        store.drop_session(session_id)

    def _retrieve_ephemeral_mode(
        self,
        inputs: List[Dict[str, Any]],
        query_text: str,
        query_image: str,
        filters: Dict[str, Any],
        top_n: int,
    ) -> List[Tuple[RetrievalChunk, float]]:
        chunks = chunk_documents(inputs)
        if not chunks:
            return []

        embeddings = self.client.embed_texts([chunk.text for chunk in chunks])
        query_vector = self._build_query_vector(query_text=query_text, query_image=query_image)

        index = EphemeralIndex()
        index.build(chunks=chunks, vectors=embeddings)
        pairs = index.search(query_vector=query_vector, top_n=top_n, filters=filters)
        return _cap_source_candidates(pairs, max_short_source_items=8)

    def _retrieve_session_mode(
        self,
        inputs: List[Dict[str, Any]],
        session_id: str,
        query_text: str,
        query_image: str,
        filters: Dict[str, Any],
        top_n: int,
    ) -> List[Tuple[RetrievalChunk, float]]:
        if not session_id:
            raise ValueError("session mode requires session_id")

        store = self._get_session_store()

        chunks = chunk_documents(inputs)
        if chunks:
            embeddings = self.client.embed_texts([chunk.text for chunk in chunks])
            store.upsert(session_id=session_id, chunks=chunks, vectors=embeddings)

        query_vector = self._build_query_vector(query_text=query_text, query_image=query_image)
        pairs = store.query(session_id=session_id, query_vector=query_vector, top_n=top_n * 3, filters=filters)

        return _cap_source_candidates(pairs[:top_n], max_short_source_items=8)

    def _build_query_vector(self, query_text: str, query_image: str) -> List[float]:
        vectors: List[List[float]] = []
        if query_text:
            vectors.append(self.client.embed_texts([query_text])[0])
        if query_image:
            vectors.append(self.client.embed_images([query_image])[0])

        if not vectors:
            return [0.0] * 384
        if len(vectors) == 1:
            return vectors[0]

        dim = min(len(vec) for vec in vectors)
        fused = [0.0] * dim
        for vec in vectors:
            for idx in range(dim):
                fused[idx] += vec[idx]
        for idx in range(dim):
            fused[idx] /= len(vectors)
        return fused

    def _rerank(
        self,
        query_text: str,
        query_image: str,
        pairs: Sequence[Tuple[RetrievalChunk, float]],
        top_n: int,
    ) -> List[Tuple[RetrievalChunk, float]]:
        if not pairs:
            return []

        candidates = list(pairs)[:top_n]

        if query_image:
            docs = [{"text": chunk.text, "image": str(chunk.metadata.get("image_path") or "")} for chunk, _ in candidates]
            ranked = self.client.rerank_vl(query_text=query_text, query_image=query_image, candidates=docs, top_k=top_n)
        else:
            docs = [chunk.text for chunk, _ in candidates]
            ranked = self.client.rerank_text(query=query_text, candidates=docs, top_k=top_n)

        output: List[Tuple[RetrievalChunk, float]] = []
        for item in ranked:
            idx = int(item.get("index", -1))
            if idx < 0 or idx >= len(candidates):
                continue
            score = float(item.get("score", 0.0))
            output.append((candidates[idx][0], score))

        if not output:
            return candidates

        output.sort(key=lambda pair: pair[1], reverse=True)
        return output

    def _dedupe_and_diversify(
        self,
        pairs: Sequence[Tuple[RetrievalChunk, float]],
        per_doc_limit: int,
    ) -> List[Tuple[RetrievalChunk, float]]:
        deduped: List[Tuple[RetrievalChunk, float]] = []
        seen_chunk_ids = set()
        per_doc_counts: Dict[str, int] = {}

        for chunk, score in pairs:
            chunk_id = str(chunk.chunk_id)
            if chunk_id in seen_chunk_ids:
                continue
            seen_chunk_ids.add(chunk_id)

            doc_id = str(chunk.metadata.get("doc_id") or chunk_id.split("::")[0])
            current = per_doc_counts.get(doc_id, 0)
            if current >= max(1, per_doc_limit):
                continue
            per_doc_counts[doc_id] = current + 1
            deduped.append((chunk, score))

        return deduped

    def _to_hit(self, chunk: RetrievalChunk, score: float) -> RetrievalHit:
        metadata = chunk.metadata or {}
        return RetrievalHit(
            score=float(score),
            excerpt=chunk.text[:800],
            doc_id=str(metadata.get("doc_id") or chunk.chunk_id.split("::")[0]),
            source_type=chunk.source_type,
            modality=chunk.modality,
            chunk_id=chunk.chunk_id,
            heading_path=str(metadata.get("heading_path") or ""),
            para_id=str(metadata.get("para_id") or ""),
            page=metadata.get("page"),
            url=str(metadata.get("url") or ""),
            title=str(metadata.get("title") or ""),
            published_date=str(metadata.get("published_date") or ""),
            metadata=metadata,
        )

    def _get_session_store(self) -> MilvusSessionStore:
        if self._session_store is None:
            store_uri = getattr(settings, "RETRIEVAL_MILVUS_URI", str(settings.DATA_DIR / "retrieval_milvus.db"))
            ttl = int(getattr(settings, "RETRIEVAL_SESSION_TTL_MINUTES", 60) or 60)
            self._session_store = MilvusSessionStore(uri=str(store_uri), ttl_minutes=ttl)
        return self._session_store


def retrieve_segments(
    query_text: str = "",
    query_image: str = "",
    inputs: Optional[List[Dict[str, Any]]] = None,
    mode: str = "ephemeral",
    session_id: str = "",
    top_n: int = 20,
    top_k: int = 5,
    filters: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Function-call friendly retrieval entrypoint."""

    service = _get_retrieval_service()
    request = RetrievalRequest(
        query_text=query_text,
        query_image=query_image,
        inputs=inputs or [],
        mode=mode,
        session_id=session_id,
        top_n=top_n,
        top_k=top_k,
        filters=filters or {},
    )
    hits = service.retrieve(request)
    return [
        {
            "score": hit.score,
            "excerpt": hit.excerpt,
            "doc_id": hit.doc_id,
            "source_type": hit.source_type,
            "modality": hit.modality,
            "chunk_id": hit.chunk_id,
            "heading_path": hit.heading_path,
            "para_id": hit.para_id,
            "page": hit.page,
            "url": hit.url,
            "title": hit.title,
            "published_date": hit.published_date,
            "metadata": hit.metadata,
        }
        for hit in hits
    ]


def drop_retrieval_session(session_id: str) -> None:
    service = _get_retrieval_service()
    service.drop_session(session_id=session_id)


_retrieval_service: Optional[RetrievalService] = None


def _get_retrieval_service() -> RetrievalService:
    global _retrieval_service
    if _retrieval_service is None:
        _retrieval_service = RetrievalService()
    return _retrieval_service


def _cap_source_candidates(
    pairs: Sequence[Tuple[RetrievalChunk, float]],
    max_short_source_items: int,
) -> List[Tuple[RetrievalChunk, float]]:
    counts: Dict[str, int] = {}
    output: List[Tuple[RetrievalChunk, float]] = []

    for chunk, score in pairs:
        source = str(chunk.source_type or "").lower()
        if source in {"openalex", "zhihuiya"}:
            current = counts.get(source, 0)
            if current >= max_short_source_items:
                continue
            counts[source] = current + 1
        output.append((chunk, score))
    return output
