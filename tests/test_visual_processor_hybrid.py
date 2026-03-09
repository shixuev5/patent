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

    return importlib.import_module("agents.patent_analysis.src.vision")


class _StubLLM:
    def __init__(self, content: str = "", error: Exception = None):
        self._content = content
        self._error = error

    def analyze_image_with_thinking(self, *args, **kwargs):
        if self._error is not None:
            raise self._error
        return self._content


def _build_processor(monkeypatch, tmp_path: Path, engine: str = "local"):
    vision = _import_vision_module()
    monkeypatch.setenv("OCR_ENGINE", engine)
    monkeypatch.setattr(vision, "PaddleOCR", lambda *args, **kwargs: object())
    return vision, vision.VisualProcessor({}, {"10": {"name": "壳体"}}, tmp_path, tmp_path)


def test_engine_fallback_to_local(monkeypatch, tmp_path: Path):
    _, processor = _build_processor(monkeypatch, tmp_path, engine="vlm")
    assert processor.engine_type == "local"


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
