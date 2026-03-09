from pathlib import Path
import time

from agents.patent_analysis.src.generator import ContentGenerator


class _NoSplitText:
    def split(self, *args, **kwargs):
        raise AssertionError("text_details should not be read in _generate_figures_analysis")

    def __str__(self):
        return "NO_TEXT"


def test_generate_figures_analysis_no_paragraph_dependency(tmp_path: Path, monkeypatch) -> None:
    class StubLLMService:
        pass

    monkeypatch.setattr(
        "agents.patent_analysis.src.generator.get_llm_service", lambda: StubLLMService()
    )

    annotated_dir = tmp_path / "annotated_images"
    annotated_dir.mkdir(parents=True)
    (annotated_dir / "fig1.png").write_bytes(b"fake")

    patent_data = {
        "bibliographic_data": {},
        "claims": [],
        "description": {
            "technical_field": "",
            "background_art": "",
            "technical_effect": "",
            "summary_of_invention": "",
            "detailed_description": _NoSplitText(),
        },
        "drawings": [
            {"figure_label": "图1", "caption": "结构示意图", "file_path": "images/fig1.png"}
        ],
    }
    parts_db = {
        "100": {
            "name": "机体总成",
            "function": "提供安装基础",
            "hierarchy": None,
            "spatial_connections": "位于整体外部框架",
            "motion_state": "保持静止",
            "attributes": "框架结构",
        },
        "10": {
            "name": "壳体",
            "function": "用于支撑",
            "hierarchy": "100",
            "spatial_connections": "位于底座上方",
            "motion_state": "保持静止",
            "attributes": "金属件",
        },
        "10a": {
            "name": "定位肋",
            "function": "限制安装位置",
            "hierarchy": "10",
            "spatial_connections": "位于底座上方",
            "motion_state": "保持静止",
            "attributes": "金属件",
        },
        "99": {
            "name": "无关件",
            "function": "无关",
            "hierarchy": None,
            "spatial_connections": "远离当前图",
            "motion_state": "保持静止",
            "attributes": "无关",
        }
    }
    image_parts = {"fig1.png": ["10"]}

    generator = ContentGenerator(
        patent_data=patent_data,
        parts_db=parts_db,
        image_parts=image_parts,
        annotated_dir=annotated_dir,
        cache_file=tmp_path / "cache.json",
    )

    captured = {}

    def _fake_caption(
        self, label, caption, local_parts, related_parts_context, global_context, image_paths
    ):
        captured["label"] = label
        captured["caption"] = caption
        captured["local_parts"] = local_parts
        captured["related_parts_context"] = related_parts_context
        captured["global_context"] = global_context
        captured["image_paths"] = image_paths
        return "stub explanation"

    monkeypatch.setattr(ContentGenerator, "_generate_single_figure_caption", _fake_caption)

    results = generator._generate_figures_analysis(
        {"title": "T", "problem": "P", "effects": [{"effect": "E", "contributing_features": []}]}
    )

    assert len(results) == 1
    assert results[0]["image_explanation"] == "stub explanation"
    assert results[0]["parts_info"] == [
        {
            "id": "10",
            "name": "壳体",
            "function": "用于支撑",
            "hierarchy": "100",
            "spatial_connections": "位于底座上方",
            "motion_state": "保持静止",
            "attributes": "金属件",
        }
    ]
    assert captured["label"] == "图1"
    assert captured["caption"] == "结构示意图"
    assert "标号 10 (壳体)" in captured["local_parts"]
    assert "空间连接=位于底座上方" in captured["local_parts"]
    assert "运动状态=保持静止" in captured["local_parts"]
    assert "标号 10 (壳体)" in captured["related_parts_context"]
    assert "标号 100 (机体总成)" in captured["related_parts_context"]
    assert "标号 10a (定位肋)" in captured["related_parts_context"]
    assert "标号 99 (无关件)" not in captured["related_parts_context"]
    assert captured["image_paths"] == [str(annotated_dir / "fig1.png")]


def test_generate_figures_analysis_keeps_input_order_when_parallel(tmp_path: Path, monkeypatch) -> None:
    class StubLLMService:
        pass

    monkeypatch.setattr(
        "agents.patent_analysis.src.generator.get_llm_service", lambda: StubLLMService()
    )

    annotated_dir = tmp_path / "annotated_images"
    annotated_dir.mkdir(parents=True)
    (annotated_dir / "fig1.png").write_bytes(b"fake")
    (annotated_dir / "fig2.png").write_bytes(b"fake")

    patent_data = {
        "bibliographic_data": {},
        "claims": [],
        "description": {
            "technical_field": "",
            "background_art": "",
            "technical_effect": "",
            "summary_of_invention": "",
            "detailed_description": "",
        },
        "drawings": [
            {"figure_label": "图1", "caption": "第一图", "file_path": "images/fig1.png"},
            {"figure_label": "图2", "caption": "第二图", "file_path": "images/fig2.png"},
        ],
    }

    generator = ContentGenerator(
        patent_data=patent_data,
        parts_db={},
        image_parts={},
        annotated_dir=annotated_dir,
        cache_file=tmp_path / "cache_order.json",
    )

    def _fake_caption(
        self, label, caption, local_parts, related_parts_context, global_context, image_paths
    ):
        if label == "图1":
            time.sleep(0.1)
        return f"{label}-解说"

    monkeypatch.setattr(ContentGenerator, "_generate_single_figure_caption", _fake_caption)

    results = generator._generate_figures_analysis(
        {"title": "T", "problem": "P", "effects": []}
    )

    assert len(results) == 2
    assert results[0]["image_title"].startswith("图1")
    assert results[1]["image_title"].startswith("图2")
    assert results[0]["image_explanation"] == "图1-解说"
    assert results[1]["image_explanation"] == "图2-解说"
