from pathlib import Path
from threading import Event
from typing import Any, Dict

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agents.patent_analysis.main import build_runtime_config, create_workflow
from agents.patent_analysis.src.nodes.download_node import DownloadNode
from agents.patent_analysis.src.nodes.extract_node import ExtractNode
from agents.patent_analysis.src.nodes.generate_core_node import GenerateCoreNode
from agents.patent_analysis.src.nodes.generate_figures_node import GenerateFiguresNode
from agents.patent_analysis.src.nodes.parse_node import ParseNode
from agents.patent_analysis.src.nodes.render_node import RenderNode
from agents.patent_analysis.src.nodes.search_join_node import SearchJoinNode
from agents.patent_analysis.src.nodes.search_matrix_node import SearchMatrixNode
from agents.patent_analysis.src.nodes.search_semantic_node import SearchSemanticNode
from agents.patent_analysis.src.nodes.transform_node import TransformNode
from agents.patent_analysis.src.nodes.vision_annotate_node import VisionAnnotateNode
from agents.patent_analysis.src.nodes.vision_extract_node import VisionExtractNode
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


def test_generate_core_and_vision_annotate_run_in_parallel(tmp_path: Path, monkeypatch) -> None:
    vision_annotate_started = Event()
    generate_core_started = Event()

    monkeypatch.setattr(DownloadNode, "run", lambda self, state: {})
    monkeypatch.setattr(ParseNode, "run", lambda self, state: {})
    monkeypatch.setattr(TransformNode, "run", lambda self, state: {"patent_data": {}})
    monkeypatch.setattr(ExtractNode, "run", lambda self, state: {"parts_db": {}})
    monkeypatch.setattr(
        VisionExtractNode,
        "run",
        lambda self, state: {"image_parts": {}, "image_labels": {}},
    )
    def _fake_vision_annotate(self, state):
        vision_annotate_started.set()
        assert generate_core_started.wait(timeout=1.0)
        return {"image_labels": {}}

    def _fake_generate_core(self, state):
        generate_core_started.set()
        assert vision_annotate_started.wait(timeout=1.0)
        return {"report_core_json": {"ai_title": "ok"}}

    monkeypatch.setattr(VisionAnnotateNode, "run", _fake_vision_annotate)
    monkeypatch.setattr(GenerateCoreNode, "run", _fake_generate_core)
    monkeypatch.setattr(
        GenerateFiguresNode,
        "run",
        lambda self, state: {"analysis_json": {"ai_title": "ok"}},
    )
    monkeypatch.setattr(
        SearchMatrixNode,
        "run",
        lambda self, state: {"search_matrix": []},
    )
    monkeypatch.setattr(
        SearchSemanticNode,
        "run",
        lambda self, state: {"search_semantic_strategy": {"content": "q"}},
    )
    monkeypatch.setattr(
        SearchJoinNode,
        "run",
        lambda self, state: {"search_json": {"ok": True}},
    )
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
    assert result_dict["analysis_json"] == {"ai_title": "ok"}


def test_generate_core_failure_stops_search_and_render(tmp_path: Path, monkeypatch) -> None:
    flags = {
        "search_matrix_called": False,
        "search_semantic_called": False,
        "search_join_called": False,
        "render_called": False,
    }

    monkeypatch.setattr(DownloadNode, "run", lambda self, state: {})
    monkeypatch.setattr(ParseNode, "run", lambda self, state: {})
    monkeypatch.setattr(TransformNode, "run", lambda self, state: {"patent_data": {}})
    monkeypatch.setattr(ExtractNode, "run", lambda self, state: {"parts_db": {}})
    monkeypatch.setattr(
        VisionExtractNode,
        "run",
        lambda self, state: {"image_parts": {}, "image_labels": {}},
    )
    monkeypatch.setattr(VisionAnnotateNode, "run", lambda self, state: {"image_labels": {}})

    def _raise_generate_core(self, state):
        raise RuntimeError("generate_core failed")

    monkeypatch.setattr(GenerateCoreNode, "run", _raise_generate_core)
    monkeypatch.setattr(
        GenerateFiguresNode,
        "run",
        lambda self, state: {"analysis_json": {"ai_title": "ok"}},
    )

    def _fake_search_matrix(self, state):
        flags["search_matrix_called"] = True
        return {"search_matrix": []}

    def _fake_search_semantic(self, state):
        flags["search_semantic_called"] = True
        return {"search_semantic_strategy": {"content": "q"}}

    def _fake_search_join(self, state):
        flags["search_join_called"] = True
        return {"search_json": {"ok": True}}

    def _fake_render(self, state):
        flags["render_called"] = True
        return {"status": "completed", "final_output_pdf": str(tmp_path / "out.pdf")}

    monkeypatch.setattr(SearchMatrixNode, "run", _fake_search_matrix)
    monkeypatch.setattr(SearchSemanticNode, "run", _fake_search_semantic)
    monkeypatch.setattr(SearchJoinNode, "run", _fake_search_join)
    monkeypatch.setattr(RenderNode, "run", _fake_render)

    workflow = create_workflow(WorkflowConfig(max_retries=1))
    result = workflow.invoke(_build_state(tmp_path))
    result_dict = result if isinstance(result, dict) else result.model_dump()

    assert result_dict["status"] == "failed"
    assert flags["search_matrix_called"] is False
    assert flags["search_semantic_called"] is False
    assert flags["search_join_called"] is False
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
    monkeypatch.setattr(
        VisionExtractNode,
        "run",
        lambda self, state: {"image_parts": {}, "image_labels": {}},
    )
    monkeypatch.setattr(VisionAnnotateNode, "run", lambda self, state: {"image_labels": {}})
    monkeypatch.setattr(
        GenerateCoreNode,
        "run",
        lambda self, state: {"report_core_json": {"ok": True}},
    )
    monkeypatch.setattr(
        GenerateFiguresNode,
        "run",
        lambda self, state: {"analysis_json": {"ok": True}},
    )
    monkeypatch.setattr(SearchMatrixNode, "run", lambda self, state: {"search_matrix": []})
    monkeypatch.setattr(
        SearchSemanticNode,
        "run",
        lambda self, state: {"search_semantic_strategy": {"content": "q"}},
    )
    monkeypatch.setattr(SearchJoinNode, "run", lambda self, state: {"search_json": {"ok": True}})
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


def test_generate_core_node_uses_cache_dir_and_not_legacy_intermediate(tmp_path: Path, monkeypatch) -> None:
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

        def generate_core_report_json(self):
            return {"ai_title": "ok"}

    monkeypatch.setattr(
        "agents.patent_analysis.src.nodes.generate_core_node.ContentGenerator",
        _FakeGenerator,
    )

    node = GenerateCoreNode(WorkflowConfig(cache_dir=str(cache_dir)))
    updates = node(state)

    assert updates["report_core_json"] == {"ai_title": "ok"}
    assert Path(str(captured["cache_file"])) == cache_dir / "generate_core_cache.json"
    assert not (Path(updates["paths"]["root"]) / "report_intermediate.json").exists()


def test_search_nodes_use_cache_dir_and_join_output(tmp_path: Path, monkeypatch) -> None:
    state = WorkflowState(
        pn="CNTEST",
        task_id="task4",
        output_dir=str(tmp_path / "output"),
        current_node="start",
        status="pending",
        progress=0.0,
        patent_data={"bibliographic_data": {}, "claims": [], "description": {}, "drawings": []},
        analysis_json={"ai_title": "ok", "technical_features": [], "technical_effects": []},
    )

    cache_dir = tmp_path / "cache"

    class _FakeMatrixGenerator:
        def __init__(self, patent_data, report_data):
            pass

        def build_search_matrix(self):
            return []

    class _FakeSemanticGenerator:
        def __init__(self, patent_data, report_data):
            pass

        def build_semantic_strategy(self):
            return {"content": "q"}

    monkeypatch.setattr(
        "agents.patent_analysis.src.nodes.search_matrix_node.SearchStrategyGenerator",
        _FakeMatrixGenerator,
    )
    monkeypatch.setattr(
        "agents.patent_analysis.src.nodes.search_semantic_node.SearchStrategyGenerator",
        _FakeSemanticGenerator,
    )

    matrix_node = SearchMatrixNode(WorkflowConfig(cache_dir=str(cache_dir)))
    semantic_node = SearchSemanticNode(WorkflowConfig(cache_dir=str(cache_dir)))
    join_node = SearchJoinNode(WorkflowConfig(cache_dir=str(cache_dir)))

    matrix_updates = matrix_node(state)
    semantic_updates = semantic_node(state)

    join_updates = join_node.run(
        {
            "paths": matrix_updates["paths"],
            "search_matrix": matrix_updates["search_matrix"],
            "search_semantic_strategy": semantic_updates["search_semantic_strategy"],
        }
    )

    assert join_updates["search_json"] == {"search_matrix": [], "semantic_strategy": {"content": "q"}}
    assert (cache_dir / "search_matrix_cache.json").exists()
    assert (cache_dir / "search_semantic_cache.json").exists()
    assert not (Path(matrix_updates["paths"]["root"]) / "search_strategy_intermediate.json").exists()
