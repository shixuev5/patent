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
