from pathlib import Path

from agents.common.retrieval import LocalEvidenceRetriever


def test_search_prefers_embodiment_for_embodiment_intent(tmp_path: Path) -> None:
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


def test_search_respects_doc_filters(tmp_path: Path) -> None:
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


def test_build_evidence_cards_respects_limits(tmp_path: Path) -> None:
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

