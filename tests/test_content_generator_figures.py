from pathlib import Path

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
    parts_db = {"10": {"name": "壳体", "function": "用于支撑"}}
    image_parts = {"fig1.png": ["10"]}

    generator = ContentGenerator(
        patent_data=patent_data,
        parts_db=parts_db,
        image_parts=image_parts,
        annotated_dir=annotated_dir,
        cache_file=tmp_path / "cache.json",
    )

    captured = {}

    def _fake_caption(self, label, caption, local_parts, global_parts, global_context, image_paths):
        captured["label"] = label
        captured["caption"] = caption
        captured["local_parts"] = local_parts
        captured["global_parts"] = global_parts
        captured["global_context"] = global_context
        captured["image_paths"] = image_paths
        return "stub explanation"

    monkeypatch.setattr(ContentGenerator, "_generate_single_figure_caption", _fake_caption)

    results = generator._generate_figures_analysis(
        {"title": "T", "problem": "P", "effects": [{"effect": "E", "contributing_features": []}]}
    )

    assert len(results) == 1
    assert results[0]["image_explanation"] == "stub explanation"
    assert results[0]["parts_info"] == [{"id": "10", "name": "壳体", "function": "用于支撑"}]
    assert captured["label"] == "图1"
    assert captured["caption"] == "结构示意图"
    assert "标号 10 (壳体)" in captured["local_parts"]
    assert "10: 壳体" in captured["global_parts"]
    assert captured["image_paths"] == [str(annotated_dir / "fig1.png")]
