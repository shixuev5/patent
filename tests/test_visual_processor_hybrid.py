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


class _StubLLM:
    def __init__(self, response=None, error: Exception = None):
        self._response = response if response is not None else {}
        self._error = error
        self.system_prompt = ""
        self.user_prompt = ""
        self.image_paths = []

    def invoke_vision_images_json(self, image_paths, system_prompt, user_prompt, *args, **kwargs):
        self.image_paths = image_paths
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        if self._error is not None:
            raise self._error
        return self._response


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
    monkeypatch.setattr(vision.settings, "VISION_MAX_WORKERS", 4, raising=False)

    assert vision.VisualProcessor._resolve_max_workers(12) == 4
    assert vision.VisualProcessor._resolve_max_workers(2) == 2


def test_hybrid_correction_success(monkeypatch, tmp_path: Path):
    vision, processor = _build_processor(monkeypatch, tmp_path)
    monkeypatch.setattr(vision.cv2, "imread", lambda _: np.zeros((100, 200, 3), dtype=np.uint8))
    llm = _StubLLM(
        response={
            "reasoning": "ok",
            "image_type": "structure",
            "marks": [{"text": "10", "box": [1, 2, 30, 40]}],
        }
    )
    monkeypatch.setattr(vision, "get_llm_service", lambda: llm)

    raw_ocr = [{"text": "1O", "box": [1, 2, 30, 40]}]
    result = processor._run_hybrid_vlm_correction("fake.png", raw_ocr)

    assert llm.image_paths == ["fake.png"]
    assert result["image_type"] == "structure"
    assert result["marks"] == [{"text": "10", "box": [1, 2, 30, 40]}]


def test_hybrid_correction_invalid_marks_filtered(monkeypatch, tmp_path: Path):
    vision, processor = _build_processor(monkeypatch, tmp_path)
    monkeypatch.setattr(vision.cv2, "imread", lambda _: np.zeros((100, 200, 3), dtype=np.uint8))
    monkeypatch.setattr(
        vision,
        "get_llm_service",
        lambda: _StubLLM(
            response={
                "reasoning": "ok",
                "image_type": "structure",
                "marks": [
                    {"text": "", "box": [1, 2, 30, 40]},
                    {"text": "20", "box": [5, 5, 205, 120]},  # 会被裁剪到图像边界
                    {"text": "30", "box": [10, 10, 10, 20]},  # 无效框
                ],
            }
        ),
    )

    result = processor._run_hybrid_vlm_correction("fake.png", [])

    assert result["marks"] == [{"text": "20", "box": [5, 5, 200, 100]}]


def test_hybrid_correction_exception_fallback_raw(monkeypatch, tmp_path: Path):
    vision, processor = _build_processor(monkeypatch, tmp_path)
    monkeypatch.setattr(vision.cv2, "imread", lambda _: np.zeros((100, 200, 3), dtype=np.uint8))
    monkeypatch.setattr(
        vision, "get_llm_service", lambda: _StubLLM(error=RuntimeError("mock error"))
    )

    result = processor._run_hybrid_vlm_correction("fake.png", [])

    assert result == {"image_type": "other", "reasoning": "VLM调用异常", "marks": []}


def test_hybrid_correction_read_image_failed(monkeypatch, tmp_path: Path):
    vision, processor = _build_processor(monkeypatch, tmp_path)
    monkeypatch.setattr(vision.cv2, "imread", lambda _: None)
    monkeypatch.setattr(vision, "get_llm_service", lambda: _StubLLM(response={}))

    result = processor._run_hybrid_vlm_correction("fake.png", [])

    assert result == {}


def test_hybrid_correction_can_supplement_missing_raw(monkeypatch, tmp_path: Path):
    vision, processor = _build_processor(monkeypatch, tmp_path)
    monkeypatch.setattr(vision.cv2, "imread", lambda _: np.zeros((100, 200, 3), dtype=np.uint8))
    monkeypatch.setattr(
        vision,
        "get_llm_service",
        lambda: _StubLLM(
            response={
                "reasoning": "ok",
                "image_type": "structure",
                "marks": [{"text": "10", "box": [3, 4, 20, 25]}],
            }
        ),
    )

    result = processor._run_hybrid_vlm_correction("fake.png", [])

    assert result["marks"] == [{"text": "10", "box": [3, 4, 20, 25]}]


def test_hybrid_prompt_contains_spatial_and_motion_context(monkeypatch, tmp_path: Path):
    vision, processor = _build_processor(monkeypatch, tmp_path)
    monkeypatch.setattr(vision.cv2, "imread", lambda _: np.zeros((100, 200, 3), dtype=np.uint8))
    llm = _StubLLM(response={"image_type": "structure", "marks": []})
    monkeypatch.setattr(vision, "get_llm_service", lambda: llm)

    _ = processor._run_hybrid_vlm_correction("fake.png", [])

    assert "空间连接" in llm.user_prompt
    assert "运动状态" in llm.user_prompt
    assert "位于装置外周并与底座连接" in llm.user_prompt
    assert "保持静止" in llm.user_prompt


def test_extract_single_image_keeps_unknown_pid_for_formal_examiner(monkeypatch, tmp_path: Path):
    _, processor = _build_processor(monkeypatch, tmp_path)

    img_path = tmp_path / "fig1.png"
    img_path.write_bytes(b"fake")

    monkeypatch.setattr(processor, "_run_local_ocr", lambda _: [])
    monkeypatch.setattr(processor, "_expand_merged_ocr_results", lambda x: x)
    monkeypatch.setattr(
        processor,
        "_run_hybrid_vlm_correction",
        lambda *_: {
            "reasoning": "ok",
            "image_type": "structure",
            "marks": [{"text": "11-A", "box": [1, 2, 30, 40]}],
        },
    )
    result = processor._extract_single_image(img_path)
    assert result["found_pids"] == ["11a"]
    assert result["labels"] == []
