from pathlib import Path
from threading import Event
from typing import Any, Dict

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agents.patent_analysis.main import build_runtime_config, create_workflow
from agents.patent_analysis.src.nodes.check_generate_join_node import CheckGenerateJoinNode
from agents.patent_analysis.src.nodes.check_node import CheckNode
from agents.patent_analysis.src.nodes.download_node import DownloadNode
from agents.patent_analysis.src.nodes.extract_node import ExtractNode
from agents.patent_analysis.src.nodes.generate_node import GenerateNode
from agents.patent_analysis.src.nodes.parse_node import ParseNode
from agents.patent_analysis.src.nodes.render_node import RenderNode
from agents.patent_analysis.src.nodes.search_node import SearchNode
from agents.patent_analysis.src.nodes.transform_node import TransformNode
from agents.patent_analysis.src.nodes.vision_node import VisionNode
from agents.patent_analysis.src.state import WorkflowConfig, WorkflowState
from agents.patent_analysis.src.workflow_utils import ensure_pipeline_paths


def _build_state(tmp_path: Path) -> WorkflowState:
    return WorkflowState(
        pn="CNTEST",
        task_id="task1",
        output_dir=str(tmp_path / "output"),
        current_node="start",
        status="pending",
        progress=0.0,
    )


def test_check_and_generate_run_in_parallel(tmp_path: Path, monkeypatch) -> None:
    check_started = Event()
    generate_started = Event()

    monkeypatch.setattr(DownloadNode, "run", lambda self, state: {})
    monkeypatch.setattr(ParseNode, "run", lambda self, state: {})
    monkeypatch.setattr(TransformNode, "run", lambda self, state: {"patent_data": {}})
    monkeypatch.setattr(ExtractNode, "run", lambda self, state: {"parts_db": {}})
    monkeypatch.setattr(VisionNode, "run", lambda self, state: {"image_parts": {}})

    def _fake_check(self, state):
        check_started.set()
        assert generate_started.wait(timeout=1.0)
        return {"check_result": {"consistency": "ok"}}

    def _fake_generate(self, state):
        generate_started.set()
        assert check_started.wait(timeout=1.0)
        return {"report_json": {"ai_title": "ok"}}

    monkeypatch.setattr(CheckNode, "run", _fake_check)
    monkeypatch.setattr(GenerateNode, "run", _fake_generate)
    monkeypatch.setattr(CheckGenerateJoinNode, "run", lambda self, state: {})
    monkeypatch.setattr(SearchNode, "run", lambda self, state: {"search_json": {"ok": True}})
    monkeypatch.setattr(
        RenderNode,
        "run",
        lambda self, state: {
            "status": "completed",
            "final_output_pdf": str(tmp_path / "out.pdf"),
        },
    )

    workflow = create_workflow(WorkflowConfig(max_retries=1))
    result = workflow.invoke(_build_state(tmp_path))
    result_dict = result if isinstance(result, dict) else result.model_dump()

    assert result_dict["status"] == "completed"
    assert result_dict["check_result"] == {"consistency": "ok"}
    assert result_dict["report_json"] == {"ai_title": "ok"}


def test_check_failure_stops_search_and_render(tmp_path: Path, monkeypatch) -> None:
    flags = {"search_called": False, "render_called": False}

    monkeypatch.setattr(DownloadNode, "run", lambda self, state: {})
    monkeypatch.setattr(ParseNode, "run", lambda self, state: {})
    monkeypatch.setattr(TransformNode, "run", lambda self, state: {"patent_data": {}})
    monkeypatch.setattr(ExtractNode, "run", lambda self, state: {"parts_db": {}})
    monkeypatch.setattr(VisionNode, "run", lambda self, state: {"image_parts": {}})

    def _raise_check(self, state):
        raise RuntimeError("check failed")

    monkeypatch.setattr(CheckNode, "run", _raise_check)
    monkeypatch.setattr(GenerateNode, "run", lambda self, state: {"report_json": {"ai_title": "ok"}})

    monkeypatch.setattr(CheckGenerateJoinNode, "run", lambda self, state: {})

    def _fake_search(self, state):
        flags["search_called"] = True
        return {"search_json": {"ok": True}}

    def _fake_render(self, state):
        flags["render_called"] = True
        return {"status": "completed", "final_output_pdf": str(tmp_path / "out.pdf")}

    monkeypatch.setattr(SearchNode, "run", _fake_search)
    monkeypatch.setattr(RenderNode, "run", _fake_render)

    workflow = create_workflow(WorkflowConfig(max_retries=1))
    result = workflow.invoke(_build_state(tmp_path))
    result_dict = result if isinstance(result, dict) else result.model_dump()

    assert result_dict["status"] == "failed"
    assert flags["search_called"] is False
    assert flags["render_called"] is False


def test_transform_node_refreshes_output_name_from_publication_number(tmp_path: Path, monkeypatch) -> None:
    state = WorkflowState(
        pn="",
        task_id="task2",
        output_dir=str(tmp_path / "output"),
        current_node="start",
        status="pending",
        progress=0.0,
    )
    paths, _ = ensure_pipeline_paths(state)
    state.paths = paths

    raw_md = Path(paths["raw_md"])
    raw_md.parent.mkdir(parents=True, exist_ok=True)
    raw_md.write_text("# mock markdown", encoding="utf-8")

    monkeypatch.setattr(
        "agents.patent_analysis.src.nodes.transform_node.extract_structured_data",
        lambda content, method="hybrid": {
            "bibliographic_data": {"publication_number": "CN123456A"},
            "claims": [],
        },
    )

    node = TransformNode(WorkflowConfig())
    updates = node(state)

    assert updates["status"] == "running"
    assert updates["resolved_pn"] == "CN123456A"
    assert Path(updates["paths"]["final_pdf"]).name == "CN123456A.pdf"
    assert Path(updates["paths"]["final_md"]).name == "CN123456A.md"


def test_workflow_checkpoint_requires_runtime_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(DownloadNode, "run", lambda self, state: {})
    monkeypatch.setattr(ParseNode, "run", lambda self, state: {})
    monkeypatch.setattr(TransformNode, "run", lambda self, state: {"patent_data": {}})
    monkeypatch.setattr(ExtractNode, "run", lambda self, state: {"parts_db": {}})
    monkeypatch.setattr(VisionNode, "run", lambda self, state: {"image_parts": {}})
    monkeypatch.setattr(CheckNode, "run", lambda self, state: {"check_result": {"ok": True}})
    monkeypatch.setattr(GenerateNode, "run", lambda self, state: {"report_json": {"ok": True}})
    monkeypatch.setattr(CheckGenerateJoinNode, "run", lambda self, state: {})
    monkeypatch.setattr(SearchNode, "run", lambda self, state: {"search_json": {"ok": True}})
    monkeypatch.setattr(RenderNode, "run", lambda self, state: {"status": "completed"})

    workflow = create_workflow(
        WorkflowConfig(
            max_retries=1,
            enable_checkpoint=True,
            checkpointer=InMemorySaver(),
        )
    )
    state = _build_state(tmp_path)

    with pytest.raises(ValueError):
        workflow.invoke(state)

    result = workflow.invoke(state, config=build_runtime_config("task-checkpoint-test"))
    result_dict = result if isinstance(result, dict) else result.model_dump()
    assert result_dict["status"] == "completed"


def test_generate_node_uses_cache_dir_and_not_legacy_intermediate(tmp_path: Path, monkeypatch) -> None:
    state = WorkflowState(
        pn="CNTEST",
        task_id="task3",
        output_dir=str(tmp_path / "output"),
        current_node="start",
        status="pending",
        progress=0.0,
        patent_data={"bibliographic_data": {}, "claims": [], "description": {}, "drawings": []},
        parts_db={"1": {"name": "part"}},
        image_parts={},
    )

    cache_dir = tmp_path / "cache"
    captured: Dict[str, Any] = {"cache_file": None}

    class _FakeGenerator:
        def __init__(self, patent_data, parts_db, image_parts, annotated_dir, cache_file=None):
            captured["cache_file"] = cache_file

        def generate_report_json(self):
            return {"ai_title": "ok"}

    monkeypatch.setattr("agents.patent_analysis.src.nodes.generate_node.ContentGenerator", _FakeGenerator)

    node = GenerateNode(WorkflowConfig(cache_dir=str(cache_dir)))
    updates = node(state)

    assert updates["report_json"] == {"ai_title": "ok"}
    assert Path(str(captured["cache_file"])) == cache_dir / "generate_cache.json"
    assert not (Path(updates["paths"]["root"]) / "report_intermediate.json").exists()


def test_search_node_uses_cache_dir_and_not_legacy_intermediate(tmp_path: Path, monkeypatch) -> None:
    state = WorkflowState(
        pn="CNTEST",
        task_id="task4",
        output_dir=str(tmp_path / "output"),
        current_node="start",
        status="pending",
        progress=0.0,
        patent_data={"bibliographic_data": {}, "claims": [], "description": {}, "drawings": []},
        report_json={"ai_title": "ok", "technical_features": [], "technical_effects": []},
    )

    cache_dir = tmp_path / "cache"
    captured: Dict[str, Any] = {"cache_file": None}

    class _FakeSearchGenerator:
        def __init__(self, patent_data, report_data, cache_file=None):
            captured["cache_file"] = cache_file

        def generate_strategy(self):
            return {"search_matrix": [], "semantic_strategy": {"content": "q"}}

    monkeypatch.setattr("agents.patent_analysis.src.nodes.search_node.SearchStrategyGenerator", _FakeSearchGenerator)

    node = SearchNode(WorkflowConfig(cache_dir=str(cache_dir)))
    updates = node(state)

    assert updates["search_json"] == {"search_matrix": [], "semantic_strategy": {"content": "q"}}
    assert Path(str(captured["cache_file"])) == cache_dir / "search_cache.json"
    assert not (Path(updates["paths"]["root"]) / "search_strategy_intermediate.json").exists()
