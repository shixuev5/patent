import importlib
import sys
import types
from pathlib import Path

from backend import task_usage_tracking


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

    return importlib.import_module("agents.common.patent_engines.vision")


def test_extract_image_labels_workers_keep_task_usage_context(monkeypatch, tmp_path: Path):
    vision = _import_vision_module()
    monkeypatch.setenv("OCR_ENGINE", "online")
    monkeypatch.setattr(vision, "PaddleOCR", lambda *args, **kwargs: object())

    image_names = ["fig1.png", "fig2.png"]
    for name in image_names:
        (tmp_path / name).write_bytes(b"fake")

    processor = vision.VisualProcessor(
        patent_data={
            "bibliographic_data": {},
            "drawings": [{"file_path": name} for name in image_names],
        },
        parts_db={"10": {"name": "壳体"}},
        raw_img_dir=tmp_path,
        out_dir=tmp_path / "out",
    )

    ocr_contexts = []
    vlm_contexts = []

    def _fake_ocr_pipeline(img_path: str):
        ocr_contexts.append(task_usage_tracking.get_current_task_usage_context())
        return [{"text": "10", "box": [1, 2, 30, 40]}]

    def _fake_vlm_pipeline(img_path: str, raw_ocr, static_system_prompt: str):
        vlm_contexts.append(task_usage_tracking.get_current_task_usage_context())
        return ["10"], [{"text": "壳体", "box": [1, 2, 30, 40]}]

    monkeypatch.setattr(processor, "_run_ocr_pipeline", _fake_ocr_pipeline)
    monkeypatch.setattr(processor, "_run_vlm_pipeline", _fake_vlm_pipeline)
    monkeypatch.setattr(processor, "_build_static_system_prompt", lambda: "sys")

    collector = task_usage_tracking.create_task_usage_collector(
        task_id="task-vision-context",
        owner_id="authing:user-vision",
        task_type="patent_analysis",
    )
    with task_usage_tracking.task_usage_collection(collector):
        image_parts, image_labels = processor.extract_image_labels()

    assert image_parts
    assert image_labels
    assert len(ocr_contexts) == len(image_names)
    assert len(vlm_contexts) == len(image_names)
    assert all(ctx.get("task_id") == "task-vision-context" for ctx in ocr_contexts)
    assert all(ctx.get("task_type") == "patent_analysis" for ctx in ocr_contexts)
    assert all(ctx.get("task_id") == "task-vision-context" for ctx in vlm_contexts)
    assert all(ctx.get("task_type") == "patent_analysis" for ctx in vlm_contexts)
