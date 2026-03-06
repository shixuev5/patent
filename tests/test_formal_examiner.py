from pathlib import Path

from agents.patent_analysis.src.checker import FormalExaminer


class StubLLMService:
    def analyze_images_json_with_thinking(self, **kwargs):
        return {
            "model_verdict": "likely_ocr_error",
            "confidence": "high",
            "reason": "附图中标号边缘模糊，疑似OCR误识别。",
        }


class BatchProbeExaminer(FormalExaminer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.batch_call_count = 0
        self.batch_issue_sizes = []

    def _review_issue_batch(self, issues, image_paths):
        self.batch_call_count += 1
        self.batch_issue_sizes.append(len(issues))
        return {
            str(issue.get("issue_id", "")).strip(): {
                "model_verdict": "likely_ocr_error",
                "confidence": "high",
                "reason": "批量复核测试",
            }
            for issue in issues
        }


def _touch_image(path: Path) -> None:
    path.write_bytes(b"fake-image-content")


def test_formal_examiner_skip_review_when_no_issues(tmp_path: Path) -> None:
    drawings_dir = tmp_path / "images"
    drawings_dir.mkdir(parents=True)
    _touch_image(drawings_dir / "fig1.png")

    examiner = FormalExaminer(
        parts_db={"10": {"name": "壳体"}},
        image_parts={"fig1.png": ["10"]},
        drawings_dir=drawings_dir,
        llm_service=StubLLMService(),
        review_model="glm-4.6v-mini",
    )

    result = examiner.check()
    assert result["secondary_review"]["status"] == "skipped"
    assert result["secondary_review"]["reason"] == "no_issues"


def test_formal_examiner_secondary_review_with_model(tmp_path: Path) -> None:
    drawings_dir = tmp_path / "images"
    drawings_dir.mkdir(parents=True)
    _touch_image(drawings_dir / "fig1.png")

    examiner = FormalExaminer(
        parts_db={"10": {"name": "壳体"}},
        image_parts={"fig1.png": ["10", "11"]},
        drawings_dir=drawings_dir,
        llm_service=StubLLMService(),
        review_model="glm-4.6v-mini",
    )

    result = examiner.check()
    secondary = result["secondary_review"]

    assert secondary["status"] == "completed"
    assert len(secondary["items"]) == 1
    assert secondary["items"][0]["part_id"] == "11"
    assert secondary["items"][0]["model_verdict"] == "likely_ocr_error"
    assert secondary["items"][0]["confidence"] == "high"


def test_formal_examiner_skip_when_model_not_configured(tmp_path: Path) -> None:
    drawings_dir = tmp_path / "images"
    drawings_dir.mkdir(parents=True)
    _touch_image(drawings_dir / "fig1.png")

    examiner = FormalExaminer(
        parts_db={"10": {"name": "壳体"}},
        image_parts={"fig1.png": ["10", "11"]},
        drawings_dir=drawings_dir,
        llm_service=StubLLMService(),
        review_model="",
    )

    result = examiner.check()
    assert result["secondary_review"]["status"] == "skipped"
    assert result["secondary_review"]["reason"] == "not_configured"


def test_formal_examiner_only_uses_official_drawings(tmp_path: Path) -> None:
    drawings_dir = tmp_path / "images"
    drawings_dir.mkdir(parents=True)
    _touch_image(drawings_dir / "fig1.png")
    _touch_image(drawings_dir / "noise.png")

    examiner = FormalExaminer(
        parts_db={"10": {"name": "壳体"}, "12": {"name": "连接件"}},
        image_parts={"fig1.png": ["10"]},
        drawings_dir=drawings_dir,
        official_image_names={"fig1.png"},
        llm_service=StubLLMService(),
        review_model="glm-4.6v-mini",
    )

    result = examiner.check()
    items = result["secondary_review"]["items"]
    assert len(items) == 1
    assert items[0]["part_id"] == "12"
    assert items[0]["reviewed_images"] == ["fig1.png"]


def test_formal_examiner_batches_issues_by_same_image_group(tmp_path: Path) -> None:
    drawings_dir = tmp_path / "images"
    drawings_dir.mkdir(parents=True)
    _touch_image(drawings_dir / "fig1.png")
    _touch_image(drawings_dir / "fig2.png")

    examiner = BatchProbeExaminer(
        parts_db={
            "10": {"name": "壳体"},
            "12": {"name": "连接件A"},
            "13": {"name": "连接件B"},
        },
        image_parts={"fig1.png": ["10"]},
        drawings_dir=drawings_dir,
        official_image_names={"fig1.png", "fig2.png"},
        llm_service=StubLLMService(),
        review_model="glm-4.6v-mini",
    )

    result = examiner.check()
    secondary = result["secondary_review"]

    assert secondary["status"] == "completed"
    assert examiner.batch_call_count == 1
    assert examiner.batch_issue_sizes == [2]
    assert len(secondary["items"]) == 2
