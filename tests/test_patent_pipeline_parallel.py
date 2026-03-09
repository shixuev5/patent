from pathlib import Path
from threading import Event

import pytest

from config import settings
from agents.patent_analysis.main import PatentPipeline


def _build_pipeline(tmp_path: Path, monkeypatch) -> PatentPipeline:
    def _fake_paths(workspace_id: str, artifact_name: str):
        root = tmp_path / "output"
        mineru_dir = root / "mineru_raw"
        return {
            "root": root,
            "annotated_dir": root / "annotated_images",
            "raw_pdf": root / "raw.pdf",
            "raw_md": mineru_dir / "raw.md",
            "mineru_dir": mineru_dir,
            "patent_json": root / "patent.json",
            "parts_json": root / "parts.json",
            "raw_images_dir": mineru_dir / "images",
            "image_parts_json": root / "image_parts.json",
            "check_json": root / "check.json",
            "report_json": root / "report.json",
            "search_strategy_json": root / "search_strategy.json",
            "final_md": root / "final.md",
            "final_pdf": root / "final.pdf",
        }

    monkeypatch.setattr(settings, "get_project_paths", _fake_paths)
    return PatentPipeline("CNTEST")


def test_check_and_generate_run_in_parallel(tmp_path: Path, monkeypatch) -> None:
    pipeline = _build_pipeline(tmp_path, monkeypatch)
    check_started = Event()
    generate_started = Event()

    def _fake_check(parts_db, image_parts):
        check_started.set()
        assert generate_started.wait(timeout=1.0)
        return {"consistency": "ok"}

    def _fake_generate(patent_data, parts_db, image_parts):
        generate_started.set()
        assert check_started.wait(timeout=1.0)
        return {"ai_title": "ok"}

    monkeypatch.setattr(pipeline, "_run_check_step", _fake_check)
    monkeypatch.setattr(pipeline, "_run_generate_step", _fake_generate)

    check_result, report_json = pipeline._run_check_and_generate_parallel(
        patent_data={},
        parts_db={},
        image_parts={},
    )

    assert check_result == {"consistency": "ok"}
    assert report_json == {"ai_title": "ok"}


def test_check_and_generate_fails_fast_on_branch_error(tmp_path: Path, monkeypatch) -> None:
    pipeline = _build_pipeline(tmp_path, monkeypatch)

    monkeypatch.setattr(
        pipeline,
        "_run_check_step",
        lambda parts_db, image_parts: (_ for _ in ()).throw(RuntimeError("check failed")),
    )
    monkeypatch.setattr(
        pipeline,
        "_run_generate_step",
        lambda patent_data, parts_db, image_parts: {"ai_title": "ok"},
    )

    with pytest.raises(RuntimeError, match="check failed"):
        pipeline._run_check_and_generate_parallel(
            patent_data={},
            parts_db={},
            image_parts={},
        )
