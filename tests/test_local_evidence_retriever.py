from pathlib import Path

import sqlite3

from agents.common.retrieval import LocalEvidenceRetriever
from config import settings


class _FakeEmbeddingProvider:
    embedding_dim = 8

    def encode_queries(self, texts):
        return [self._encode(text) for text in texts]

    def encode_passages(self, texts):
        return [self._encode(text) for text in texts]

    def _encode(self, text):
        value = str(text or "").lower()
        vector = [0.0] * self.embedding_dim
        groups = [
            ["定位", "position", "alignment"],
            ["导轨", "rail", "guide"],
            ["移动", "slide", "move"],
            ["锁定", "lock", "locking"],
            ["结构", "structure"],
        ]
        for idx, group in enumerate(groups):
            if any(token in value for token in group):
                vector[idx] += 1.0
        if not any(vector):
            vector[0] = 1.0
        norm = sum(item * item for item in vector) ** 0.5
        return [item / norm for item in vector]


def _patch_fake_embeddings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RETRIEVAL_EMBEDDING_MODEL", "fake/bge-m3")
    monkeypatch.setattr(
        LocalEvidenceRetriever,
        "_build_embedding_provider",
        lambda self: _FakeEmbeddingProvider(),
    )


def test_search_prefers_embodiment_for_embodiment_intent(tmp_path: Path, monkeypatch) -> None:
    _patch_fake_embeddings(monkeypatch)
    retriever = LocalEvidenceRetriever(db_path=str(tmp_path / "local.db"), chunk_chars=80, chunk_overlap=20)
    retriever.build_index(
        [
            {
                "doc_id": "D1",
                "source_type": "comparison_document",
                "title": "Doc 1",
                "content": (
                    "背景技术：本发明涉及检测装置。\n\n"
                    "具体实施方式：定位架沿导轨移动并锁定，实现精准对准。\n\n"
                    "权利要求1：一种装置，包括定位架。"
                ),
            }
        ]
    )

    hits = retriever.search("定位架 导轨 移动 锁定", intent="embodiment", top_k=3)
    assert hits
    assert hits[0]["section_type"] in {"embodiment", "claim"}
    assert hits[0]["retrieval_channels"]
    assert "fusion_score" in hits[0]


def test_search_respects_doc_filters(tmp_path: Path, monkeypatch) -> None:
    _patch_fake_embeddings(monkeypatch)
    retriever = LocalEvidenceRetriever(db_path=str(tmp_path / "local.db"), chunk_chars=100, chunk_overlap=20)
    retriever.build_index(
        [
            {"doc_id": "D1", "source_type": "comparison_document", "title": "1", "content": "A结构用于定位。"},
            {"doc_id": "D2", "source_type": "comparison_document", "title": "2", "content": "B结构用于定位。"},
        ]
    )

    hits = retriever.search("B结构", intent="fact_verification", doc_filters=["D2"], top_k=5)
    assert hits
    assert all(item["doc_id"] == "D2" for item in hits)


def test_build_evidence_cards_respects_limits(tmp_path: Path, monkeypatch) -> None:
    _patch_fake_embeddings(monkeypatch)
    retriever = LocalEvidenceRetriever(db_path=str(tmp_path / "local.db"), chunk_chars=120, chunk_overlap=40)
    retriever.build_index(
        [
            {
                "doc_id": "D1",
                "source_type": "comparison_document",
                "title": "Doc 1",
                "content": "这是一个很长的证据段落。" * 80,
            }
        ]
    )
    hits = retriever.search("证据 段落", intent="fact_verification", top_k=8)
    bundle = retriever.build_evidence_cards(
        candidates=hits,
        context_k=2,
        max_context_chars=140,
        max_quote_chars=70,
        read_window=1,
    )
    cards = bundle["cards"]
    assert cards
    assert len(cards) <= 2
    assert all(len(item["quote"]) <= 70 for item in cards)
    assert bundle["trace"]["context_chars"] <= 140


def test_build_index_records_embedding_metadata_and_languages(tmp_path: Path, monkeypatch) -> None:
    _patch_fake_embeddings(monkeypatch)
    retriever = LocalEvidenceRetriever(db_path=str(tmp_path / "local.db"), chunk_chars=100, chunk_overlap=20)
    meta = retriever.build_index(
        [
            {
                "doc_id": "D1",
                "source_type": "comparison_document",
                "title": "mixed",
                "content": "中文定位结构 English rail locking mechanism.",
            }
        ]
    )

    assert meta["embedding_model"] == "fake/bge-m3"
    assert meta["embedding_dim"] == 8
    assert "mixed" in meta["indexed_languages"]
    assert meta["documents"] == [
        {
            "doc_id": "D1",
            "source_type": "comparison_document",
            "doc_language": "mixed",
        }
    ]
    assert "embedding_provider" not in meta

    with sqlite3.connect(str(tmp_path / "local.db")) as conn:
        meta_rows = dict(conn.execute("SELECT key, value FROM retrieval_meta").fetchall())
    assert "embedding_provider" not in meta_rows


def test_dense_search_can_bridge_cross_language_terms(tmp_path: Path, monkeypatch) -> None:
    _patch_fake_embeddings(monkeypatch)
    retriever = LocalEvidenceRetriever(db_path=str(tmp_path / "local.db"), chunk_chars=100, chunk_overlap=20)
    retriever.build_index(
        [
            {
                "doc_id": "EN1",
                "source_type": "comparison_document",
                "title": "english",
                "content": "The rail locking structure slides into position and keeps alignment stable.",
            }
        ]
    )

    hits = retriever.search("导轨 锁定 定位", intent="fact_verification", top_k=3)
    assert hits
    assert hits[0]["doc_id"] == "EN1"
    assert "dense" in hits[0]["retrieval_channels"]


def test_existing_index_is_not_cleared_on_reopen(tmp_path: Path, monkeypatch) -> None:
    _patch_fake_embeddings(monkeypatch)
    db_path = tmp_path / "local.db"
    retriever = LocalEvidenceRetriever(db_path=str(db_path), chunk_chars=100, chunk_overlap=20)
    retriever.build_index(
        [
            {
                "doc_id": "D1",
                "source_type": "comparison_document",
                "title": "Doc 1",
                "content": "定位架沿导轨移动并锁定。",
            }
        ]
    )

    reopened = LocalEvidenceRetriever(db_path=str(db_path), chunk_chars=100, chunk_overlap=20)
    hits = reopened.search("定位架 导轨 锁定", intent="fact_verification", top_k=3)
    assert hits
    assert hits[0]["doc_id"] == "D1"
