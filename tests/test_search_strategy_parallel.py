from agents.patent_analysis.src.engines.search import SearchStrategyGenerator


def test_build_search_matrix_uses_context(monkeypatch) -> None:
    class StubLLMService:
        pass

    monkeypatch.setattr(
        "agents.patent_analysis.src.engines.search.get_llm_service", lambda: StubLLMService()
    )

    generator = SearchStrategyGenerator(
        patent_data={"bibliographic_data": {"ipc_classifications": []}},
        report_data={"technical_means": "技术手段"},
    )

    monkeypatch.setattr(generator, "_build_matrix_context", lambda: "ctx")

    def _fake_matrix(context: str):
        assert context == "ctx"
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

    monkeypatch.setattr(generator, "_build_search_matrix", _fake_matrix)
    result = generator.build_search_matrix()

    assert result[0]["element_name"] == "要素A"


def test_build_semantic_strategy_delegates(monkeypatch) -> None:
    class StubLLMService:
        pass

    monkeypatch.setattr(
        "agents.patent_analysis.src.engines.search.get_llm_service", lambda: StubLLMService()
    )

    generator = SearchStrategyGenerator(
        patent_data={"bibliographic_data": {"ipc_classifications": []}},
        report_data={"technical_means": "技术手段"},
    )
    monkeypatch.setattr(
        generator,
        "_build_semantic_strategy",
        lambda: {"name": "语义检索", "description": "desc", "content": "query"},
    )

    result = generator.build_semantic_strategy()
    assert result["content"] == "query"
