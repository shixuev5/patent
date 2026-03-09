from pathlib import Path

from agents.patent_analysis.src.checker import FormalExaminer


class StubLLMService:
    def analyze_images_json_with_thinking(self, **kwargs):
        return {
            "user_verdict": "false_alarm",
            "confidence": "high",
            "reason": "附图中可见标号，规则疑点为机器误报。",
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
                "user_verdict": "false_alarm",
                "confidence": "high",
                "reason": "批量复核测试",
            }
            for issue in issues
        }


class ActionableProbeExaminer(FormalExaminer):
    def _review_issue_batch(self, issues, image_paths):
        results = {}
        for issue in issues:
            issue_id = str(issue.get("issue_id", "")).strip()
            issue_type = str(issue.get("issue_type", "")).strip()
            if issue_type == "missing_in_images":
                results[issue_id] = {
                    "user_verdict": "defect_confirmed",
                    "confidence": "high",
                    "reason": "附图中未找到该标号。",
                }
            else:
                results[issue_id] = {
                    "user_verdict": "uncertain",
                    "confidence": "low",
                    "reason": "图像模糊，需人工核实。",
                }
        return results


class ErrorSecondaryExaminer(FormalExaminer):
    def _run_secondary_review(self, issues):
        return {
            "status": "error",
            "reason": "partial_error",
            "model": "mock-model",
            "summary": "mock error",
            "items": [
                {
                    "issue_id": "undefined_in_text:11",
                    "issue_type": "undefined_in_text",
                    "part_id": "11",
                    "part_name": "未定义",
                    "rule_message": "mock",
                    "preliminary_result": "potential_issue",
                    "user_verdict": "false_alarm",
                    "confidence": "high",
                    "reason": "mock preserved",
                    "reviewed_images": ["fig1.png"],
                }
            ],
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
    assert result["user_actionable_issues"] == []
    assert "检查通过" in result["consistency"]


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
    assert secondary["items"][0]["user_verdict"] == "false_alarm"
    assert secondary["items"][0]["confidence"] == "high"
    assert result["user_actionable_issues"] == []
    assert "检查通过" in result["consistency"]


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
    assert len(result["secondary_review"]["items"]) == 1
    assert result["secondary_review"]["items"][0]["user_verdict"] == "uncertain"
    assert len(result["user_actionable_issues"]) == 1
    assert result["user_actionable_issues"][0]["part_id"] == "11"
    assert "需人工核实" in result["consistency"]


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
    assert items[0]["user_verdict"] == "false_alarm"


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


def test_formal_examiner_user_actionable_markdown_only_contains_actionable_items(tmp_path: Path) -> None:
    drawings_dir = tmp_path / "images"
    drawings_dir.mkdir(parents=True)
    _touch_image(drawings_dir / "fig1.png")

    examiner = ActionableProbeExaminer(
        parts_db={"10": {"name": "壳体"}, "12": {"name": "连接件"}},
        image_parts={"fig1.png": ["10", "13"]},
        drawings_dir=drawings_dir,
        official_image_names={"fig1.png"},
        llm_service=StubLLMService(),
        review_model="glm-4.6v-mini",
    )

    result = examiner.check()
    secondary = result["secondary_review"]
    actionable = result["user_actionable_issues"]

    assert secondary["status"] == "completed"
    assert len(secondary["items"]) == 2
    assert len(actionable) == 2
    assert {item["user_verdict"] for item in actionable} == {"defect_confirmed", "uncertain"}
    assert "[确认缺陷]" in result["consistency"]
    assert "[需人工核实]" in result["consistency"]
    assert "false_alarm" not in result["consistency"]


def test_formal_examiner_error_status_keeps_existing_review_items(tmp_path: Path) -> None:
    drawings_dir = tmp_path / "images"
    drawings_dir.mkdir(parents=True)
    _touch_image(drawings_dir / "fig1.png")

    examiner = ErrorSecondaryExaminer(
        parts_db={"10": {"name": "壳体"}},
        image_parts={"fig1.png": ["10", "11"]},
        drawings_dir=drawings_dir,
        llm_service=StubLLMService(),
        review_model="glm-4.6v-mini",
    )

    result = examiner.check()
    secondary = result["secondary_review"]

    assert secondary["status"] == "error"
    assert len(secondary["items"]) == 1
    assert secondary["items"][0]["user_verdict"] == "false_alarm"
    assert result["user_actionable_issues"] == []
