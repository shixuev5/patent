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
        lambda: {
            "name": "语义检索",
            "description": "desc",
            "queries": [{"block_id": "B1", "effect_cluster_ids": ["E1"], "content": "query"}],
        },
    )

    result = generator.build_semantic_strategy()
    assert result["queries"][0]["content"] == "query"


def test_build_effect_clusters_assigns_b_subblocks_and_hub_feature(monkeypatch) -> None:
    class StubLLMService:
        pass

    monkeypatch.setattr(
        "agents.patent_analysis.src.engines.search.get_llm_service", lambda: StubLLMService()
    )
    generator = SearchStrategyGenerator(
        patent_data={"bibliographic_data": {"ipc_classifications": []}},
        report_data={
            "technical_features": [
                {"name": "特征A", "description": "描述A"},
                {"name": "特征B", "description": "描述B"},
                {"name": "特征C", "description": "描述C"},
            ],
            "technical_effects": [
                {"effect": "效果1", "tcs_score": 5, "contributing_features": ["特征A", "特征B"]},
                {"effect": "效果2", "tcs_score": 5, "contributing_features": ["特征B", "特征C"]},
            ],
        },
    )

    bundle = generator._build_effect_clusters()
    clusters = bundle["effect_clusters"]

    assert [item["block_id"] for item in clusters] == ["B1", "B2"]
    assert [item["effect_cluster_ids"] for item in clusters] == [["E1"], ["E2"]]
    assert bundle["hub_features"] == {"特征B"}


def test_build_effect_clusters_fallbacks_to_highest_score_when_no_score_5(monkeypatch) -> None:
    class StubLLMService:
        pass

    monkeypatch.setattr(
        "agents.patent_analysis.src.engines.search.get_llm_service", lambda: StubLLMService()
    )
    generator = SearchStrategyGenerator(
        patent_data={"bibliographic_data": {"ipc_classifications": []}},
        report_data={
            "technical_effects": [
                {"effect": "效果1", "tcs_score": 3, "contributing_features": ["特征A"]},
                {"effect": "效果2", "tcs_score": 4, "contributing_features": ["特征B"]},
                {"effect": "效果3", "tcs_score": 4, "contributing_features": ["特征C"]},
            ],
        },
    )

    bundle = generator._build_effect_clusters()
    clusters = bundle["effect_clusters"]
    assert [item["block_id"] for item in clusters] == ["B1", "B2"]
    assert [item["effect_text"] for item in clusters] == ["效果2", "效果3"]


def test_build_semantic_strategy_outputs_queries_by_effect_cluster(monkeypatch) -> None:
    class StubLLMService:
        pass

    monkeypatch.setattr(
        "agents.patent_analysis.src.engines.search.get_llm_service", lambda: StubLLMService()
    )
    generator = SearchStrategyGenerator(
        patent_data={"bibliographic_data": {"ipc_classifications": []}},
        report_data={
            "technical_features": [
                {"name": "特征A", "description": "描述A"},
                {"name": "特征B", "description": "描述B"},
            ],
            "technical_effects": [
                {"effect": "效果1", "tcs_score": 5, "contributing_features": ["特征A"]},
                {"effect": "效果2", "tcs_score": 5, "contributing_features": ["特征B"]},
            ],
        },
    )

    monkeypatch.setattr(
        generator,
        "_generate_semantic_query",
        lambda raw_text, **kwargs: (
            f"query-{kwargs.get('block_id')}-{','.join(kwargs.get('effect_cluster_ids') or [])}"
        ),
    )
    result = generator._build_semantic_strategy()
    assert [item["block_id"] for item in result["queries"]] == ["B1", "B2"]
    assert result["queries"][0]["effect"] == "效果1"
    assert result["queries"][0]["content"] == "query-B1-E1"
    assert result["queries"][1]["content"] == "query-B2-E2"


def test_normalize_search_matrix_includes_v2_fields(monkeypatch) -> None:
    class StubLLMService:
        pass

    monkeypatch.setattr(
        "agents.patent_analysis.src.engines.search.get_llm_service", lambda: StubLLMService()
    )
    generator = SearchStrategyGenerator(
        patent_data={"bibliographic_data": {"ipc_classifications": []}},
        report_data={},
    )
    raw = [
        {
            "element_name": "要素A",
            "element_role": "KeyFeature",
            "block_id": "b2",
            "effect_cluster_ids": ["e2"],
            "is_hub_feature": True,
            "term_frequency": "low",
            "priority_tier": "core",
            "element_type": "Product_Structure",
            "keywords_zh": ["要素A"],
            "keywords_en": ["element*"],
            "ipc_cpc_ref": ["G06F 16/00"],
        },
        {
            "element_name": "要素B",
            "element_role": "Functional",
            "element_type": "Method_Process",
            "keywords_zh": ["要素B"],
            "keywords_en": ["method*"],
            "ipc_cpc_ref": [],
        },
    ]
    normalized = generator._normalize_search_matrix(raw)

    assert normalized[0]["block_id"] == "B2"
    assert normalized[0]["effect_cluster_ids"] == ["E2"]
    assert normalized[0]["is_hub_feature"] is True
    assert normalized[0]["term_frequency"] == "low"
    assert normalized[0]["priority_tier"] == "core"
    assert normalized[1]["block_id"] == "C"
    assert normalized[1]["effect_cluster_ids"] == []
    assert normalized[1]["term_frequency"] == "high"
    assert normalized[1]["priority_tier"] == "assist"


def test_build_execution_plan_returns_full_list(monkeypatch) -> None:
    class StubLLMService:
        def invoke_text_json(self, messages, task_kind, temperature):
            assert task_kind == "execution_plan_generation"
            return [
                {
                    "step_name": "B1子块核心特征检索",
                    "condition": "语义检索未命中破坏新颖性文献时执行",
                    "search_logic": "[主题A] AND [特征B1]",
                    "rationale": "锁定B1核心特征进行新颖性打击",
                    "database": "专利数据库",
                },
                {
                    "step_name": "B1降噪限定",
                    "condition": "召回量超过500篇时执行",
                    "search_logic": "([主题A] AND [特征B1]) AND [共通C]",
                    "rationale": "引入高频限定特征进行收敛",
                    "database": "专利数据库",
                },
            ]

    monkeypatch.setattr(
        "agents.patent_analysis.src.engines.search.get_llm_service", lambda: StubLLMService()
    )
    generator = SearchStrategyGenerator(
        patent_data={"bibliographic_data": {"ipc_classifications": ["A61N 5/00"]}},
        report_data={},
    )
    result = generator.build_execution_plan(
        [
            {
                "element_name": "主题A",
                "block_id": "A",
                "term_frequency": "high",
                "priority_tier": "filter",
            },
            {
                "element_name": "特征B1",
                "block_id": "B1",
                "term_frequency": "low",
                "priority_tier": "core",
            },
            {
                "element_name": "共通C",
                "block_id": "C",
                "term_frequency": "high",
                "priority_tier": "assist",
            },
        ]
    )

    assert len(result) == 2
    assert result[0]["step_name"] == "B1子块核心特征检索"
    assert result[1]["search_logic"] == "([主题A] AND [特征B1]) AND [共通C]"


def test_build_execution_plan_returns_empty_when_llm_fails(monkeypatch) -> None:
    class StubLLMService:
        def invoke_text_json(self, messages, task_kind, temperature):
            raise RuntimeError("llm failed")

    monkeypatch.setattr(
        "agents.patent_analysis.src.engines.search.get_llm_service", lambda: StubLLMService()
    )
    generator = SearchStrategyGenerator(
        patent_data={"bibliographic_data": {"ipc_classifications": []}},
        report_data={},
    )

    result = generator.build_execution_plan(
        [{"element_name": "特征B1", "block_id": "B1", "term_frequency": "low", "priority_tier": "core"}]
    )
    assert result == []
