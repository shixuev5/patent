import importlib
import sys
import types
from pathlib import Path

import numpy as np


def _import_vision_module():
    if "paddleocr" not in sys.modules:
        fake_module = types.ModuleType("paddleocr")

        class DummyPaddleOCR:
            def __init__(self, *args, **kwargs):
                pass

            def predict(self, *args, **kwargs):
                return []

        fake_module.PaddleOCR = DummyPaddleOCR
        sys.modules["paddleocr"] = fake_module

    return importlib.import_module("agents.patent_analysis.src.engines.vision")


def _build_processor(monkeypatch, tmp_path: Path, engine: str = "local"):
    vision = _import_vision_module()
    monkeypatch.setenv("OCR_ENGINE", engine)
    monkeypatch.setattr(vision, "PaddleOCR", lambda *args, **kwargs: object())
    monkeypatch.setattr(vision, "get_llm_service", lambda: object())
    parts_db = {
        "10": {
            "name": "壳体",
            "function": "用于容纳内部组件",
            "hierarchy": "100",
            "spatial_connections": "位于装置外周并与底座连接",
            "motion_state": "保持静止",
            "attributes": "金属外壳",
        }
    }
    return vision, vision.VisualProcessor({}, parts_db, tmp_path, tmp_path)


def test_engine_fallback_to_local(monkeypatch, tmp_path: Path):
    _, processor = _build_processor(monkeypatch, tmp_path, engine="vlm")
    assert processor.engine_type == "local"


def test_resolve_max_workers_uses_unified_setting(monkeypatch, tmp_path: Path):
    vision, _ = _build_processor(monkeypatch, tmp_path)
    monkeypatch.setattr(vision.settings, "VISION_MAX_WORKERS", 4, raising=False)

    assert vision.VisualProcessor._resolve_max_workers(12) == 4
    assert vision.VisualProcessor._resolve_max_workers(2) == 2


def test_vlm_pipeline_success(monkeypatch, tmp_path: Path):
    vision, processor = _build_processor(monkeypatch, tmp_path)
    monkeypatch.setattr(vision.cv2, "imread", lambda _: np.zeros((100, 200, 3), dtype=np.uint8))
    monkeypatch.setattr(
        processor,
        "_safe_invoke_vlm",
        lambda *_: '{"reasoning":"ok","image_type":"structure","marks":[{"text":"10","box":[1,2,30,40]}]}',
    )

    static_prompt = processor._build_static_system_prompt()
    raw_ocr = [{"text": "1O", "box": [1, 2, 30, 40]}]
    part_ids, labels = processor._run_vlm_pipeline("fake.png", raw_ocr, static_prompt)

    assert part_ids == ["10"]
    assert labels == [{"text": "壳体", "box": [1, 2, 30, 40]}]


def test_vlm_pipeline_invalid_marks_filtered(monkeypatch, tmp_path: Path):
    vision, processor = _build_processor(monkeypatch, tmp_path)
    monkeypatch.setattr(vision.cv2, "imread", lambda _: np.zeros((100, 200, 3), dtype=np.uint8))
    monkeypatch.setattr(
        processor,
        "_safe_invoke_vlm",
        lambda *_: (
            '{"reasoning":"ok","image_type":"structure","marks":['
            '{"text":"","box":[1,2,30,40]},'
            '{"text":"10","box":[5,5,205,120]},'
            '{"text":"30","box":[10,10,10,20]}'
            "]}"
        ),
    )

    static_prompt = processor._build_static_system_prompt()
    part_ids, labels = processor._run_vlm_pipeline("fake.png", [], static_prompt)

    assert part_ids == ["10"]
    assert labels == [{"text": "壳体", "box": [5, 5, 200, 100]}]


def test_vlm_pipeline_exception_returns_empty(monkeypatch, tmp_path: Path):
    vision, processor = _build_processor(monkeypatch, tmp_path)
    monkeypatch.setattr(vision.cv2, "imread", lambda _: np.zeros((100, 200, 3), dtype=np.uint8))

    def _raise(*_args, **_kwargs):
        raise RuntimeError("mock error")

    monkeypatch.setattr(processor, "_safe_invoke_vlm", _raise)
    static_prompt = processor._build_static_system_prompt()
    part_ids, labels = processor._run_vlm_pipeline("fake.png", [], static_prompt)

    assert part_ids == []
    assert labels == []


def test_vlm_pipeline_read_image_failed(monkeypatch, tmp_path: Path):
    vision, processor = _build_processor(monkeypatch, tmp_path)
    monkeypatch.setattr(vision.cv2, "imread", lambda _: None)
    static_prompt = processor._build_static_system_prompt()
    part_ids, labels = processor._run_vlm_pipeline("fake.png", [], static_prompt)

    assert part_ids == []
    assert labels == []


def test_vlm_pipeline_can_use_empty_raw_ocr(monkeypatch, tmp_path: Path):
    vision, processor = _build_processor(monkeypatch, tmp_path)
    monkeypatch.setattr(vision.cv2, "imread", lambda _: np.zeros((100, 200, 3), dtype=np.uint8))
    monkeypatch.setattr(
        processor,
        "_safe_invoke_vlm",
        lambda *_: '{"reasoning":"ok","image_type":"structure","marks":[{"text":"10","box":[3,4,20,25]}]}',
    )

    static_prompt = processor._build_static_system_prompt()
    part_ids, labels = processor._run_vlm_pipeline("fake.png", [], static_prompt)

    assert part_ids == ["10"]
    assert labels == [{"text": "壳体", "box": [3, 4, 20, 25]}]


def test_static_prompt_contains_spatial_and_motion_context(monkeypatch, tmp_path: Path):
    _, processor = _build_processor(monkeypatch, tmp_path)
    prompt = processor._build_static_system_prompt()

    assert "空间连接" in prompt
    assert "运动状态" in prompt
    assert "位于装置外周并与底座连接" in prompt
    assert "保持静止" in prompt


def test_vlm_pipeline_keeps_unknown_pid_for_ai_review(monkeypatch, tmp_path: Path):
    vision, processor = _build_processor(monkeypatch, tmp_path)
    monkeypatch.setattr(vision.cv2, "imread", lambda _: np.zeros((100, 200, 3), dtype=np.uint8))
    monkeypatch.setattr(
        processor,
        "_safe_invoke_vlm",
        lambda *_: '{"reasoning":"ok","image_type":"structure","marks":[{"text":"11-A","box":[1,2,30,40]}]}',
    )

    static_prompt = processor._build_static_system_prompt()
    part_ids, labels = processor._run_vlm_pipeline("fake.png", [], static_prompt)

    assert part_ids == ["11a"]
    assert labels == []
