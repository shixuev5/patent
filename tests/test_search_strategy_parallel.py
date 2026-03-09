from pathlib import Path
from threading import Event

from agents.patent_analysis.src.search import SearchStrategyGenerator


def test_matrix_and_semantic_run_in_parallel(tmp_path: Path, monkeypatch) -> None:
    class StubLLMService:
        pass

    monkeypatch.setattr(
        "agents.patent_analysis.src.search.get_llm_service", lambda: StubLLMService()
    )

    generator = SearchStrategyGenerator(
        patent_data={"bibliographic_data": {"ipc_classifications": []}},
        report_data={"technical_means": "技术手段"},
        cache_file=tmp_path / "search_cache.json",
    )

    matrix_started = Event()
    semantic_started = Event()

    monkeypatch.setattr(generator, "_build_matrix_context", lambda: "ctx")

    def _fake_matrix(context: str):
        matrix_started.set()
        assert semantic_started.wait(timeout=1.0)
        return [
            {
                "element_name": "要素A",
                "element_role": "Subject",
                "element_type": "Product_Structure",
                "keywords_zh": ["要素A"],
                "keywords_en": ["element*"],
                "ipc_cpc_ref": ["G06F 16/00"],
            }
        ]

    def _fake_semantic():
        semantic_started.set()
        assert matrix_started.wait(timeout=1.0)
        return {"name": "语义检索", "description": "desc", "content": "query"}

    monkeypatch.setattr(generator, "_build_search_matrix", _fake_matrix)
    monkeypatch.setattr(generator, "_build_semantic_strategy", _fake_semantic)

    result = generator.generate_strategy()

    assert "search_matrix" in result
    assert "semantic_strategy" in result
    assert result["search_matrix"][0]["element_name"] == "要素A"
    assert result["semantic_strategy"]["content"] == "query"
