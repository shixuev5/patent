import json
from pathlib import Path
from threading import Event

from agents.patent_analysis.src.engines.generator import ContentGenerator
from backend import task_usage_tracking


def test_background_and_features_run_in_parallel(tmp_path: Path, monkeypatch) -> None:
    class StubLLMService:
        pass

    monkeypatch.setattr(
        "agents.patent_analysis.src.engines.generator.get_llm_service",
        lambda: StubLLMService(),
    )

    generator = ContentGenerator(
        patent_data={
            "bibliographic_data": {"invention_title": "原始标题", "abstract": "原始摘要"},
            "claims": [],
            "description": {
                "technical_field": "",
                "background_art": "",
                "technical_effect": "",
                "summary_of_invention": "",
                "detailed_description": "",
            },
            "drawings": [],
        },
        parts_db={},
        image_parts={},
        annotated_dir=tmp_path,
        cache_file=tmp_path / "report_cache.json",
    )

    background_started = Event()
    features_started = Event()

    monkeypatch.setattr(
        generator,
        "_analyze_domain_and_problem",
        lambda: {"technical_field": "领域", "technical_problem": "问题"},
    )
    monkeypatch.setattr(
        generator,
        "_synthesize_solution_package",
        lambda problem_context: {
            "ai_title": "标题",
            "ai_abstract": "摘要",
            "technical_scheme": "技术方案",
        },
    )

    def _fake_background(core_logic):
        background_started.set()
        assert features_started.wait(timeout=1.0)
        return {"background_knowledge": [{"term": "术语"}]}

    def _fake_features(core_logic):
        features_started.set()
        assert background_started.wait(timeout=1.0)
        return {
            "claim_subject_matter": "保护主题",
            "technical_features": [
                {
                    "name": "特征A",
                    "description": "描述",
                    "is_distinguishing": True,
                    "claim_source": "independent",
                }
            ],
        }

    monkeypatch.setattr(generator, "_generate_background_knowledge", _fake_background)
    monkeypatch.setattr(generator, "_extract_features", _fake_features)
    monkeypatch.setattr(
        generator,
        "_verify_evidence",
        lambda core_logic, feature_list: {
            "technical_means": "技术手段",
            "technical_effects": [],
        },
    )
    monkeypatch.setattr(generator, "_generate_figures_analysis", lambda global_context: [])

    report = generator.generate_report_json()

    assert report["ai_title"] == "标题"
    assert report["technical_field"] == "领域"
    assert report["technical_problem"] == "问题"
    assert report["claim_subject_matter"] == "保护主题"
    assert report["technical_means"] == "技术手段"
    assert report["background_knowledge"] == [{"term": "术语"}]
    assert report["technical_features"][0]["name"] == "特征A"


def test_generator_parallel_workers_keep_task_usage_context(tmp_path: Path, monkeypatch) -> None:
    class StubLLMService:
        pass

    monkeypatch.setattr(
        "agents.patent_analysis.src.engines.generator.get_llm_service",
        lambda: StubLLMService(),
    )

    generator = ContentGenerator(
        patent_data={
            "bibliographic_data": {"invention_title": "原始标题", "abstract": "原始摘要"},
            "claims": [],
            "description": {
                "technical_field": "",
                "background_art": "",
                "technical_effect": "",
                "summary_of_invention": "",
                "detailed_description": "",
            },
            "drawings": [],
        },
        parts_db={},
        image_parts={},
        annotated_dir=tmp_path,
        cache_file=None,
    )

    worker_contexts = {}
    collector = task_usage_tracking.create_task_usage_collector(
        task_id="task-parallel-ctx",
        owner_id="authing:user-parallel",
        task_type="patent_analysis",
    )

    monkeypatch.setattr(
        generator,
        "_analyze_domain_and_problem",
        lambda: {"technical_field": "领域", "technical_problem": "问题"},
    )
    monkeypatch.setattr(
        generator,
        "_synthesize_solution_package",
        lambda problem_context: {
            "ai_title": "标题",
            "ai_abstract": "摘要",
            "technical_scheme": "技术方案",
        },
    )

    def _fake_background(core_logic):
        worker_contexts["background"] = task_usage_tracking.get_current_task_usage_context()
        return {"background_knowledge": []}

    def _fake_features(core_logic):
        worker_contexts["features"] = task_usage_tracking.get_current_task_usage_context()
        return {"claim_subject_matter": "", "technical_features": []}

    monkeypatch.setattr(generator, "_generate_background_knowledge", _fake_background)
    monkeypatch.setattr(generator, "_extract_features", _fake_features)
    monkeypatch.setattr(
        generator,
        "_verify_evidence",
        lambda core_logic, feature_list: {"technical_means": "", "technical_effects": []},
    )

    with task_usage_tracking.task_usage_collection(collector):
        _ = generator.generate_core_report_json()

    assert worker_contexts["background"]["task_id"] == "task-parallel-ctx"
    assert worker_contexts["background"]["task_type"] == "patent_analysis"
    assert worker_contexts["features"]["task_id"] == "task-parallel-ctx"
    assert worker_contexts["features"]["task_type"] == "patent_analysis"


def test_verify_evidence_runs_means_then_effects_with_expected_params(
    tmp_path: Path, monkeypatch
) -> None:
    class StubLLMService:
        def __init__(self):
            self.calls = []

        def invoke_text_json(self, messages, task_kind, temperature):
            self.calls.append(
                {
                    "messages": messages,
                    "task_kind": task_kind,
                    "temperature": temperature,
                }
            )
            if task_kind == "technical_means_generation":
                return {"technical_means": "通过 **特征A** [1.1] 建立稳定反馈机制"}
            if task_kind == "technical_effect_verification":
                return {
                    "technical_effects": [
                        {"effect": "次要优化", "tcs_score": 3},
                        {"effect": "核心突破", "tcs_score": 5},
                    ]
                }
            raise AssertionError(f"unexpected task kind: {task_kind}")

    llm_stub = StubLLMService()
    monkeypatch.setattr(
        "agents.patent_analysis.src.engines.generator.get_llm_service",
        lambda: llm_stub,
    )

    generator = ContentGenerator(
        patent_data={
            "bibliographic_data": {"invention_title": "原始标题", "abstract": "原始摘要"},
            "claims": [],
            "description": {
                "technical_field": "",
                "background_art": "",
                "technical_effect": "提升鲁棒性",
                "summary_of_invention": "",
                "detailed_description": "实施例细节",
            },
            "drawings": [],
        },
        parts_db={},
        image_parts={},
        annotated_dir=tmp_path,
        cache_file=None,
    )

    result = generator._verify_evidence(
        core_logic={"technical_problem": "噪声干扰导致误检", "technical_scheme": "自适应识别方案"},
        feature_list=[{"name": "特征A", "is_distinguishing": True}],
    )

    assert len(llm_stub.calls) == 2
    assert llm_stub.calls[0]["task_kind"] == "technical_means_generation"
    assert llm_stub.calls[0]["temperature"] == 0.2
    assert llm_stub.calls[1]["task_kind"] == "technical_effect_verification"
    assert llm_stub.calls[1]["temperature"] == 0.0
    assert "通过 **特征A** [1.1] 建立稳定反馈机制" in llm_stub.calls[1]["messages"][1]["content"]
    assert "特征归属树" in llm_stub.calls[1]["messages"][1]["content"]
    assert result["technical_means"] == "通过 **特征A** [1.1] 建立稳定反馈机制"
    assert [item["tcs_score"] for item in result["technical_effects"]] == [5, 3]


def test_verify_evidence_uses_split_cache_keys(tmp_path: Path, monkeypatch) -> None:
    class StubLLMService:
        def __init__(self):
            self.calls = 0

        def invoke_text_json(self, messages, task_kind, temperature):
            self.calls += 1
            if task_kind == "technical_means_generation":
                return {"technical_means": "机理"}
            if task_kind == "technical_effect_verification":
                return {"technical_effects": [{"effect": "效果", "tcs_score": 4}]}
            raise AssertionError(f"unexpected task kind: {task_kind}")

    llm_stub = StubLLMService()
    monkeypatch.setattr(
        "agents.patent_analysis.src.engines.generator.get_llm_service",
        lambda: llm_stub,
    )

    cache_file = tmp_path / "report_cache.json"
    generator = ContentGenerator(
        patent_data={
            "bibliographic_data": {"invention_title": "原始标题", "abstract": "原始摘要"},
            "claims": [],
            "description": {
                "technical_field": "",
                "background_art": "",
                "technical_effect": "",
                "summary_of_invention": "",
                "detailed_description": "实施例细节",
            },
            "drawings": [],
        },
        parts_db={},
        image_parts={},
        annotated_dir=tmp_path,
        cache_file=cache_file,
    )

    core_logic = {"technical_problem": "问题", "technical_scheme": "方案"}
    feature_list = [{"name": "特征A", "is_distinguishing": True}]
    _ = generator._verify_evidence(core_logic, feature_list)
    _ = generator._verify_evidence(core_logic, feature_list)

    assert llm_stub.calls == 2
    cache_data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert "technical_means" in cache_data
    assert "technical_effects" in cache_data
    assert "verification" not in cache_data


def test_generate_core_report_no_longer_writes_verification_cache_key(
    tmp_path: Path, monkeypatch
) -> None:
    class StubLLMService:
        pass

    monkeypatch.setattr(
        "agents.patent_analysis.src.engines.generator.get_llm_service",
        lambda: StubLLMService(),
    )

    cache_file = tmp_path / "report_cache.json"
    generator = ContentGenerator(
        patent_data={
            "bibliographic_data": {"invention_title": "原始标题", "abstract": "原始摘要"},
            "claims": [],
            "description": {
                "technical_field": "",
                "background_art": "",
                "technical_effect": "",
                "summary_of_invention": "",
                "detailed_description": "",
            },
            "drawings": [],
        },
        parts_db={},
        image_parts={},
        annotated_dir=tmp_path,
        cache_file=cache_file,
    )

    monkeypatch.setattr(
        generator,
        "_analyze_domain_and_problem",
        lambda: {"technical_field": "领域", "technical_problem": "问题"},
    )
    monkeypatch.setattr(
        generator,
        "_synthesize_solution_package",
        lambda problem_context: {
            "ai_title": "标题",
            "ai_abstract": "摘要",
            "technical_scheme": "技术方案",
        },
    )
    monkeypatch.setattr(generator, "_generate_background_knowledge", lambda core_logic: {})
    monkeypatch.setattr(
        generator,
        "_extract_features",
        lambda core_logic: {"claim_subject_matter": "", "technical_features": []},
    )
    monkeypatch.setattr(
        generator,
        "_verify_evidence",
        lambda core_logic, feature_list: {"technical_means": "", "technical_effects": []},
    )

    _ = generator.generate_core_report_json()

    cache_data = json.loads(cache_file.read_text(encoding="utf-8"))
    assert "verification" not in cache_data


def test_format_claims_to_text_includes_parent_relationship(tmp_path: Path, monkeypatch) -> None:
    class StubLLMService:
        pass

    monkeypatch.setattr(
        "agents.patent_analysis.src.engines.generator.get_llm_service",
        lambda: StubLLMService(),
    )

    generator = ContentGenerator(
        patent_data={
            "bibliographic_data": {},
            "claims": [
                {"claim_id": "1", "claim_type": "independent", "claim_text": "一种装置..."},
                {
                    "claim_id": "2",
                    "claim_type": "dependent",
                    "claim_text": "根据权利要求1所述的装置...",
                    "parent_claim_ids": ["1"],
                },
            ],
            "description": {},
            "drawings": [],
        },
        parts_db={},
        image_parts={},
        annotated_dir=tmp_path,
        cache_file=None,
    )

    all_claims_text = generator._format_claims_to_text()
    independent_only_text = generator._format_claims_to_text(only_independent=True)

    assert "### Claim 1 [独立权利要求 (Independent)]" in all_claims_text
    assert "### Claim 2 [从属权利要求 (Dependent, 引用 Claim 1)]" in all_claims_text
    assert "Claim 2" not in independent_only_text


def test_extract_features_keeps_llm_order_and_prompt_requires_claim_id(
    tmp_path: Path, monkeypatch
) -> None:
    class StubLLMService:
        def __init__(self):
            self.calls = []

        def invoke_text_json(self, messages, task_kind, temperature):
            self.calls.append(
                {
                    "messages": messages,
                    "task_kind": task_kind,
                    "temperature": temperature,
                }
            )
            return {
                "claim_subject_matter": "测试主题",
                "technical_features": [
                    {
                        "name": "后出现的非区别特征",
                        "claim_id": "2",
                        "is_distinguishing": False,
                        "claim_source": "dependent",
                    },
                    {
                        "name": "先出现的区别特征",
                        "claim_id": "1",
                        "is_distinguishing": True,
                        "claim_source": "independent",
                    },
                ],
            }

    llm_stub = StubLLMService()
    monkeypatch.setattr(
        "agents.patent_analysis.src.engines.generator.get_llm_service",
        lambda: llm_stub,
    )

    generator = ContentGenerator(
        patent_data={
            "bibliographic_data": {},
            "claims": [
                {"claim_id": "1", "claim_type": "independent", "claim_text": "一种装置..."},
                {
                    "claim_id": "2",
                    "claim_type": "dependent",
                    "claim_text": "根据权利要求1所述的装置...",
                    "parent_claim_ids": ["1"],
                },
            ],
            "description": {},
            "drawings": [],
        },
        parts_db={},
        image_parts={},
        annotated_dir=tmp_path,
        cache_file=None,
    )

    response = generator._extract_features(
        {"technical_problem": "问题", "technical_scheme": "方案"}
    )

    assert [item["name"] for item in response["technical_features"]] == [
        "后出现的非区别特征",
        "先出现的区别特征",
    ]
    assert llm_stub.calls[0]["task_kind"] == "claim_feature_reasoning"
    assert '"claim_id"' in llm_stub.calls[0]["messages"][0]["content"]
    assert "[权X]" in llm_stub.calls[0]["messages"][0]["content"]


def test_build_feature_menu_str_uses_claim_scoped_numbering(
    tmp_path: Path, monkeypatch
) -> None:
    class StubLLMService:
        pass

    monkeypatch.setattr(
        "agents.patent_analysis.src.engines.generator.get_llm_service",
        lambda: StubLLMService(),
    )

    generator = ContentGenerator(
        patent_data={
            "bibliographic_data": {},
            "claims": [
                {"claim_id": "1", "claim_type": "independent", "claim_text": "独立权1"},
                {
                    "claim_id": "2",
                    "claim_type": "dependent",
                    "claim_text": "从属权2",
                    "parent_claim_ids": ["1"],
                },
            ],
            "description": {},
            "drawings": [],
        },
        parts_db={},
        image_parts={},
        annotated_dir=tmp_path,
        cache_file=None,
    )

    menu = generator._build_feature_menu_str(
        [
            {"name": "从属特征", "claim_id": "2", "is_distinguishing": False},
            {"name": "独立特征A", "claim_id": "1", "is_distinguishing": True},
            {"name": "独立特征B", "claim_id": "1", "is_distinguishing": False},
        ]
    )

    assert "▶ [Claim 1] (独立权利要求 / 根节点):" in menu
    assert "- [1.1] 独立特征A (★区别特征)" in menu
    assert "- [1.2] 独立特征B (前序/从权常规特征)" in menu
    assert "↳ [Claim 2] (从属权利要求，引用 Claim 1):" in menu
    assert "- [2.1] 从属特征 (前序/从权常规特征)" in menu
