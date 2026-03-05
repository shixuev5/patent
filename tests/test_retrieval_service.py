from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

from agents.common.retrieval.chunkers import chunk_documents
from agents.common.retrieval.service import RetrievalService
from agents.common.retrieval.types import RetrievalChunk, RetrievalRequest


class StubClient:
    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        return [self._vec(text) for text in texts]

    def embed_images(self, image_paths: List[str]) -> List[List[float]]:
        return [self._vec(path) for path in image_paths]

    def rerank_text(self, query: str, candidates: List[str], top_k: int) -> List[Dict[str, Any]]:
        scores = []
        q = query.lower().strip()
        for idx, item in enumerate(candidates):
            score = 1.0 if q and q in item.lower() else 0.1
            scores.append({"index": idx, "score": score})
        scores.sort(key=lambda x: x["score"], reverse=True)
        return scores[:top_k]

    def rerank_vl(
        self,
        query_text: str,
        query_image: str,
        candidates: List[Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        scores = []
        q = query_text.lower().strip()
        for idx, item in enumerate(candidates):
            text = str(item.get("text") or "").lower()
            score = 0.8 if q and q in text else 0.2
            scores.append({"index": idx, "score": score})
        scores.sort(key=lambda x: x["score"], reverse=True)
        return scores[:top_k]

    @staticmethod
    def _vec(text: str) -> List[float]:
        seed = sum(ord(ch) for ch in str(text))
        return [float((seed + 13 * i) % 97) / 97.0 for i in range(384)]


class StubSessionStore:
    def __init__(self) -> None:
        self._rows: Dict[str, List[Tuple[RetrievalChunk, List[float]]]] = {}

    def upsert(self, session_id: str, chunks: Sequence[RetrievalChunk], vectors: Sequence[Sequence[float]]) -> None:
        bucket = self._rows.setdefault(session_id, [])
        for chunk, vector in zip(chunks, vectors):
            bucket.append((chunk, list(vector)))

    def query(self, session_id: str, query_vector: Sequence[float], top_n: int, filters: Dict[str, Any]) -> List[Tuple[RetrievalChunk, float]]:
        rows = self._rows.get(session_id, [])
        scored = []
        for chunk, vector in rows:
            score = sum(a * b for a, b in zip(query_vector[:16], vector[:16]))
            scored.append((chunk, float(score)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_n]

    def drop_session(self, session_id: str) -> None:
        self._rows.pop(session_id, None)


def test_chunk_documents_handles_markdown_web_short_text() -> None:
    docs = [
        {
            "source_type": "patent",
            "doc_id": "P1",
            "content": "# 总述\n\n第一段关于结构A。\n\n第二段关于权利要求1 的限定。",
        },
        {
            "source_type": "tavily_web",
            "doc_id": "W1",
            "title": "网页",
            "url": "https://example.com",
            "content": "这是网页正文。包含多个句子。用于测试切分。",
        },
        {
            "source_type": "openalex",
            "doc_id": "A1",
            "title": "Paper",
            "abstract": "This is an abstract used for retrieval test.",
        },
    ]

    chunks = chunk_documents(docs)
    assert len(chunks) >= 3
    assert any(chunk.source_type == "patent" for chunk in chunks)
    assert any(chunk.source_type == "web" for chunk in chunks)
    assert any(chunk.source_type == "openalex" for chunk in chunks)


def test_retrieval_service_ephemeral_mode() -> None:
    service = RetrievalService()
    service.client = StubClient()

    docs = [
        {
            "source_type": "patent",
            "doc_id": "D1",
            "content": "# 结构\n\n电池模组包括壳体和极耳，强调散热通道设计。",
        },
        {
            "source_type": "non_patent",
            "doc_id": "D2",
            "content": "# 论文\n\n本文讨论散热结构与导热材料。",
        },
    ]

    request = RetrievalRequest(
        query_text="散热结构",
        inputs=docs,
        mode="ephemeral",
        top_n=20,
        top_k=5,
    )
    hits = service.retrieve(request)

    assert 1 <= len(hits) <= 5
    assert all(hit.excerpt for hit in hits)
    assert hits[0].score >= hits[-1].score


def test_retrieval_service_session_mode_with_stub_store() -> None:
    service = RetrievalService()
    service.client = StubClient()
    service._session_store = StubSessionStore()

    docs = [
        {
            "source_type": "openalex",
            "doc_id": "OA1",
            "title": "Heat Dissipation",
            "abstract": "A thermal channel design for battery enclosure",
        }
    ]

    request = RetrievalRequest(
        query_text="thermal channel",
        inputs=docs,
        mode="session",
        session_id="S-1",
        top_n=20,
        top_k=5,
    )
    hits = service.retrieve(request)

    assert len(hits) >= 1
    assert hits[0].doc_id == "OA1"
