from pathlib import Path

from agents.ai_review.src.nodes.check_node import CheckNode
from agents.patent_analysis.src.nodes.generate_core_node import GenerateCoreNode
from agents.patent_analysis.src.nodes.generate_figures_node import GenerateFiguresNode
from agents.patent_analysis.src.nodes.vision_extract_node import VisionExtractNode
from agents.patent_analysis.src.state import WorkflowConfig, WorkflowState


def _build_state(tmp_path: Path, **kwargs) -> WorkflowState:
    payload = {
        "pn": "CNTEST",
        "task_id": "task-empty-parts",
        "output_dir": str(tmp_path / "output"),
        "current_node": "start",
        "status": "pending",
        "progress": 0.0,
    }
    payload.update(kwargs)
    return WorkflowState(**payload)


def test_vision_extract_node_accepts_empty_parts_db(tmp_path: Path, monkeypatch) -> None:
    class _FakeVisualProcessor:
        def __init__(self, patent_data, parts_db, raw_img_dir, out_dir):
            self.patent_data = patent_data
            self.parts_db = parts_db
            self.raw_img_dir = raw_img_dir
            self.out_dir = out_dir

        def extract_image_labels(self):
            return {}, {}

    monkeypatch.setattr(
        "agents.patent_analysis.src.nodes.vision_extract_node.VisualProcessor",
        _FakeVisualProcessor,
    )

    state = _build_state(
        tmp_path,
        patent_data={"bibliographic_data": {}, "drawings": []},
        parts_db={},
    )
    node = VisionExtractNode(WorkflowConfig())
    updates = node(state)

    assert updates["status"] == "running"
    assert updates["image_parts"] == {}
    assert updates["image_labels"] == {}


def test_check_node_accepts_empty_parts_db(tmp_path: Path, monkeypatch) -> None:
    class _FakeFormalExaminer:
        def __init__(self, parts_db, image_parts):
            self.parts_db = parts_db
            self.image_parts = image_parts

        def check(self):
            return {"consistency": "ok"}

    monkeypatch.setattr(
        "agents.ai_review.src.nodes.check_node.FormalExaminer",
        _FakeFormalExaminer,
    )

    state = _build_state(tmp_path, parts_db={}, image_parts={})
    node = CheckNode(WorkflowConfig())
    updates = node(state)

    assert updates["status"] == "running"
    assert updates["check_result"] == {"consistency": "ok"}


def test_generate_core_node_accepts_empty_parts_db(tmp_path: Path, monkeypatch) -> None:
    class _FakeGenerator:
        def __init__(self, patent_data, parts_db, image_parts, annotated_dir, cache_file=None):
            self.patent_data = patent_data
            self.parts_db = parts_db
            self.image_parts = image_parts
            self.annotated_dir = annotated_dir
            self.cache_file = cache_file

        def generate_core_report_json(self):
            return {"ai_title": "ok"}

    monkeypatch.setattr(
        "agents.patent_analysis.src.nodes.generate_core_node.ContentGenerator",
        _FakeGenerator,
    )

    state = _build_state(
        tmp_path,
        patent_data={"bibliographic_data": {}, "claims": [], "description": {}, "drawings": []},
        parts_db={},
        image_parts={},
    )
    node = GenerateCoreNode(WorkflowConfig(cache_dir=str(tmp_path / "cache")))
    updates = node(state)

    assert updates["status"] == "running"
    assert updates["report_core_json"] == {"ai_title": "ok"}


def test_generate_figures_node_accepts_empty_parts_db(tmp_path: Path, monkeypatch) -> None:
    class _FakeGenerator:
        def __init__(self, patent_data, parts_db, image_parts, annotated_dir, cache_file=None):
            self.patent_data = patent_data
            self.parts_db = parts_db
            self.image_parts = image_parts
            self.annotated_dir = annotated_dir
            self.cache_file = cache_file

        def generate_figure_explanations(self, report_core_json):
            return [{"figure": "fig1"}]

    monkeypatch.setattr(
        "agents.patent_analysis.src.nodes.generate_figures_node.ContentGenerator",
        _FakeGenerator,
    )

    state = _build_state(
        tmp_path,
        patent_data={"bibliographic_data": {}, "claims": [], "description": {}, "drawings": []},
        parts_db={},
        image_parts={},
        report_core_json={"ai_title": "ok"},
    )
    node = GenerateFiguresNode(WorkflowConfig(cache_dir=str(tmp_path / "cache")))
    updates = node(state)

    assert updates["status"] == "running"
    assert updates["analysis_json"]["ai_title"] == "ok"
    assert updates["analysis_json"]["figure_explanations"] == [{"figure": "fig1"}]
