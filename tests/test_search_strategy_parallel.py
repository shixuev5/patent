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
        {
            "element_name": "降低摩擦系数",
            "element_role": "Effect",
            "effect_cluster_ids": ["e1"],
            "element_type": "Parameter_Condition",
            "term_frequency": "low",
            "priority_tier": "core",
            "keywords_zh": ["减摩"],
            "keywords_en": ["friction* reduc*"],
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
    assert normalized[2]["block_id"] == "E"
    assert normalized[2]["effect_cluster_ids"] == ["E1"]
    assert normalized[2]["term_frequency"] == "high"
    assert normalized[2]["priority_tier"] == "filter"


def test_build_matrix_context_groups_block_c_by_dependency(monkeypatch) -> None:
    class StubLLMService:
        pass

    monkeypatch.setattr(
        "agents.patent_analysis.src.engines.search.get_llm_service", lambda: StubLLMService()
    )
    generator = SearchStrategyGenerator(
        patent_data={"bibliographic_data": {"ipc_classifications": []}},
        report_data={
            "technical_features": [
                {
                    "name": "主装置",
                    "description": "主装置描述",
                    "claim_source": "independent",
                    "is_distinguishing": False,
                },
                {"name": "核心特征A", "description": "核心A描述"},
                {"name": "核心特征B", "description": "核心B描述"},
                {"name": "协同特征X", "description": "协同X描述"},
                {"name": "全局特征Y", "description": "全局Y描述"},
            ],
            "technical_effects": [
                {"effect": "效果1", "tcs_score": 5, "contributing_features": ["核心特征A"]},
                {"effect": "效果2", "tcs_score": 5, "contributing_features": ["核心特征B"]},
                {
                    "effect": "协同效果1",
                    "tcs_score": 4,
                    "dependent_on": "核心特征A",
                    "contributing_features": ["协同特征X"],
                    "rationale": "通过X增强A",
                },
                {
                    "effect": "全局效果",
                    "tcs_score": 3,
                    "contributing_features": ["全局特征Y"],
                    "rationale": "通用降噪",
                },
            ],
        },
    )

    context = generator._build_matrix_context()

    assert "[依附于核心突破点的协同/使能特征 (强关联降噪)]" in context
    assert "专用于配合/使能 【E1】 效果的特征" in context
    assert "【协同特征X】 (TCS: 4)" in context
    assert "[无明确依附的全局补充特征 (通用降噪)]" in context
    assert "【全局特征Y】 (TCS: 3)" in context
    assert "=== 4. Block E: 效果与功能锚点 (Effect - Optional Precision Filter) ===" in context
    assert "[B1/E1] 效果锚点: 效果1" in context


def test_build_semantic_cluster_text_includes_dependent_features(monkeypatch) -> None:
    class StubLLMService:
        pass

    monkeypatch.setattr(
        "agents.patent_analysis.src.engines.search.get_llm_service", lambda: StubLLMService()
    )
    generator = SearchStrategyGenerator(
        patent_data={"bibliographic_data": {"ipc_classifications": []}},
        report_data={
            "claim_subject_matter": "旋转机械监测系统",
            "technical_problem": "弱信号易被噪声淹没",
            "technical_features": [
                {"name": "核心特征A", "description": "核心A描述"},
                {"name": "协同特征X", "description": "协同X描述"},
            ],
            "technical_effects": [
                {"effect": "效果1", "tcs_score": 5, "contributing_features": ["核心特征A"]},
                {
                    "effect": "协同效果1",
                    "tcs_score": 4,
                    "dependent_on": "核心特征A",
                    "contributing_features": ["协同特征X"],
                    "rationale": "通过X增强A",
                },
            ],
        },
    )
    bundle = generator._build_effect_clusters()
    cluster = bundle["effect_clusters"][0]

    text = generator._build_semantic_cluster_text(cluster, bundle)
    assert "[配套的使能/协同技术手段 (用于细化上述核心特征的落地结构)]" in text
    assert "【协同特征】: 协同特征X (TCS: 4)" in text
    assert "协同机理: 通过X增强A" in text


def test_normalize_search_matrix_clears_a_cluster_and_keeps_c_cluster(monkeypatch) -> None:
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
            "element_name": "主题A",
            "element_role": "Subject",
            "block_id": "A",
            "effect_cluster_ids": ["E9"],
            "element_type": "Product_Structure",
            "keywords_zh": ["主题A"],
            "keywords_en": ["subject*"],
            "ipc_cpc_ref": [],
        },
        {
            "element_name": "限定C",
            "element_role": "Functional",
            "block_id": "C",
            "effect_cluster_ids": ["e2"],
            "element_type": "Parameter_Condition",
            "keywords_zh": ["限定C"],
            "keywords_en": ["limit*"],
            "ipc_cpc_ref": [],
        },
    ]
    normalized = generator._normalize_search_matrix(raw)

    assert normalized[0]["effect_cluster_ids"] == []
    assert normalized[1]["effect_cluster_ids"] == ["E2"]


def test_build_matrix_context_matches_dependent_on_with_loose_text(monkeypatch) -> None:
    class StubLLMService:
        pass

    monkeypatch.setattr(
        "agents.patent_analysis.src.engines.search.get_llm_service", lambda: StubLLMService()
    )
    generator = SearchStrategyGenerator(
        patent_data={"bibliographic_data": {"ipc_classifications": []}},
        report_data={
            "technical_features": [
                {"name": "核心特征A", "description": "核心A描述"},
                {"name": "协同特征X", "description": "协同X描述"},
            ],
            "technical_effects": [
                {"effect": "效果1", "tcs_score": 5, "contributing_features": ["核心特征A"]},
                {
                    "effect": "协同效果1",
                    "tcs_score": 4,
                    "dependent_on": "1. 核心特征A",
                    "contributing_features": ["协同特征X"],
                    "rationale": "通过X增强A",
                },
            ],
        },
    )

    context = generator._build_matrix_context()
    assert "专用于配合/使能 【E1】 效果的特征" in context
    assert "【协同特征X】 (TCS: 4)" in context


def test_build_semantic_cluster_text_matches_dependent_on_with_loose_text(monkeypatch) -> None:
    class StubLLMService:
        pass

    monkeypatch.setattr(
        "agents.patent_analysis.src.engines.search.get_llm_service", lambda: StubLLMService()
    )
    generator = SearchStrategyGenerator(
        patent_data={"bibliographic_data": {"ipc_classifications": []}},
        report_data={
            "technical_features": [
                {"name": "核心特征A", "description": "核心A描述"},
                {"name": "协同特征X", "description": "协同X描述"},
            ],
            "technical_effects": [
                {"effect": "效果1", "tcs_score": 5, "contributing_features": ["核心特征A"]},
                {
                    "effect": "协同效果1",
                    "tcs_score": 4,
                    "dependent_on": "核心特征A(对应E1)",
                    "contributing_features": ["协同特征X"],
                    "rationale": "通过X增强A",
                },
            ],
        },
    )
    bundle = generator._build_effect_clusters()
    cluster = bundle["effect_clusters"][0]

    text = generator._build_semantic_cluster_text(cluster, bundle)
    assert "【协同特征】: 协同特征X (TCS: 4)" in text


def test_generate_semantic_query_prompt_uses_single_braces_json_example(monkeypatch) -> None:
    class StubLLMService:
        def __init__(self):
            self.messages = None

        def invoke_text_json(self, messages, task_kind, temperature):
            self.messages = messages
            return {"semantic_query": "query"}

    llm = StubLLMService()
    monkeypatch.setattr(
        "agents.patent_analysis.src.engines.search.get_llm_service", lambda: llm
    )
    generator = SearchStrategyGenerator(
        patent_data={"bibliographic_data": {"ipc_classifications": []}},
        report_data={},
    )

    result = generator._generate_semantic_query(
        "输入文本",
        block_id="B1",
        effect_cluster_ids=["E1"],
    )

    assert result == "query"
    assert llm.messages is not None
    system_prompt = llm.messages[0]["content"]
    assert '"semantic_query":' in system_prompt
    assert "{{" not in system_prompt
    assert "}}" not in system_prompt
