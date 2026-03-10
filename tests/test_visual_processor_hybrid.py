import importlib
import json
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


class _StubLLM:
    def __init__(self, content: str = "", error: Exception = None):
        self._content = content
        self._error = error
        self.system_prompt = ""
        self.user_prompt = ""

    def invoke_vision_image(self, img_path, system_prompt, user_prompt, *args, **kwargs):
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        if self._error is not None:
            raise self._error
        return self._content


def _build_processor(monkeypatch, tmp_path: Path, engine: str = "local"):
    vision = _import_vision_module()
    monkeypatch.setenv("OCR_ENGINE", engine)
    monkeypatch.setattr(vision, "PaddleOCR", lambda *args, **kwargs: object())
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
    monkeypatch.setattr(vision.settings, "VISION_MAX_WORKERS", 6, raising=False)

    assert vision.VisualProcessor._resolve_max_workers(12) == 6
    assert vision.VisualProcessor._resolve_max_workers(2) == 2


def test_hybrid_correction_success(monkeypatch, tmp_path: Path):
    vision, processor = _build_processor(monkeypatch, tmp_path)
    monkeypatch.setattr(vision.cv2, "imread", lambda _: np.zeros((100, 200, 3), dtype=np.uint8))
    content = json.dumps([{"text": "10", "box": [1, 2, 30, 40]}], ensure_ascii=False)
    monkeypatch.setattr(vision, "get_llm_service", lambda: _StubLLM(content=content))

    raw_ocr = [{"text": "1O", "box": [1, 2, 30, 40]}]
    result = processor._run_hybrid_vlm_correction("fake.png", raw_ocr)

    assert result == [{"text": "10", "box": [1, 2, 30, 40]}]


def test_hybrid_correction_invalid_json_fallback_raw(monkeypatch, tmp_path: Path):
    vision, processor = _build_processor(monkeypatch, tmp_path)
    monkeypatch.setattr(vision.cv2, "imread", lambda _: np.zeros((100, 200, 3), dtype=np.uint8))
    monkeypatch.setattr(vision, "get_llm_service", lambda: _StubLLM(content="not-json"))

    raw_ocr = [{"text": "10", "box": [1, 2, 30, 40]}]
    result = processor._run_hybrid_vlm_correction("fake.png", raw_ocr)

    assert result == raw_ocr


def test_hybrid_correction_exception_fallback_raw(monkeypatch, tmp_path: Path):
    vision, processor = _build_processor(monkeypatch, tmp_path)
    monkeypatch.setattr(vision.cv2, "imread", lambda _: np.zeros((100, 200, 3), dtype=np.uint8))
    monkeypatch.setattr(
        vision, "get_llm_service", lambda: _StubLLM(error=RuntimeError("mock error"))
    )

    raw_ocr = [{"text": "10", "box": [1, 2, 30, 40]}]
    result = processor._run_hybrid_vlm_correction("fake.png", raw_ocr)

    assert result == raw_ocr


def test_hybrid_correction_empty_result_fallback_raw(monkeypatch, tmp_path: Path):
    vision, processor = _build_processor(monkeypatch, tmp_path)
    monkeypatch.setattr(vision.cv2, "imread", lambda _: np.zeros((100, 200, 3), dtype=np.uint8))
    monkeypatch.setattr(vision, "get_llm_service", lambda: _StubLLM(content="[]"))

    raw_ocr = [{"text": "10", "box": [1, 2, 30, 40]}]
    result = processor._run_hybrid_vlm_correction("fake.png", raw_ocr)

    assert result == raw_ocr


def test_hybrid_correction_can_supplement_missing_raw(monkeypatch, tmp_path: Path):
    vision, processor = _build_processor(monkeypatch, tmp_path)
    monkeypatch.setattr(vision.cv2, "imread", lambda _: np.zeros((100, 200, 3), dtype=np.uint8))
    content = json.dumps([{"text": "10", "box": [3, 4, 20, 25]}], ensure_ascii=False)
    monkeypatch.setattr(vision, "get_llm_service", lambda: _StubLLM(content=content))

    result = processor._run_hybrid_vlm_correction("fake.png", [])

    assert result == [{"text": "10", "box": [3, 4, 20, 25]}]


def test_hybrid_prompt_contains_spatial_and_motion_context(monkeypatch, tmp_path: Path):
    vision, processor = _build_processor(monkeypatch, tmp_path)
    monkeypatch.setattr(vision.cv2, "imread", lambda _: np.zeros((100, 200, 3), dtype=np.uint8))
    llm = _StubLLM(content="[]")
    monkeypatch.setattr(vision, "get_llm_service", lambda: llm)

    _ = processor._run_hybrid_vlm_correction("fake.png", [])

    assert "空间连接" in llm.user_prompt
    assert "运动状态" in llm.user_prompt
    assert "位于装置外周并与底座连接" in llm.user_prompt
    assert "保持静止" in llm.user_prompt


def test_process_single_image_keeps_unknown_pid_for_formal_examiner(monkeypatch, tmp_path: Path):
    _, processor = _build_processor(monkeypatch, tmp_path)

    img_path = tmp_path / "fig1.png"
    out_path = tmp_path / "out.png"
    img_path.write_bytes(b"fake")

    monkeypatch.setattr(processor, "_run_local_ocr", lambda _: [])
    monkeypatch.setattr(processor, "_expand_merged_ocr_results", lambda x: x)
    monkeypatch.setattr(
        processor,
        "_run_hybrid_vlm_correction",
        lambda *_: [{"text": "11-A", "box": [1, 2, 30, 40]}],
    )

    annotate_called = {"called": False}

    def _mark_annotate(*args, **kwargs):
        annotate_called["called"] = True

    monkeypatch.setattr(processor, "_annotate_image", _mark_annotate)

    found = processor._process_single_image(img_path, out_path)

    assert found == ["11a"]
    assert annotate_called["called"] is False
    assert out_path.exists()
