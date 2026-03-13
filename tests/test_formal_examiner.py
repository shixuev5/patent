from agents.ai_review.src.engines.checker import FormalExaminer


def test_formal_examiner_pass_when_no_issues() -> None:
    examiner = FormalExaminer(
        parts_db={"10": {"name": "壳体"}},
        image_parts={"fig1.png": ["10"]},
    )

    result = examiner.check()

    assert set(result.keys()) == {"consistency"}
    assert "检查通过" in result["consistency"]


def test_formal_examiner_reports_missing_in_images() -> None:
    examiner = FormalExaminer(
        parts_db={"10": {"name": "壳体"}, "12": {"name": "连接件"}},
        image_parts={"fig1.png": ["10"]},
    )

    result = examiner.check()
    consistency = result["consistency"]

    assert "说明书文字部分存在，但附图中未标记" in consistency
    assert "12-连接件" in consistency
    assert "请人工核查确认" in consistency


def test_formal_examiner_reports_undefined_in_text() -> None:
    examiner = FormalExaminer(
        parts_db={"10": {"name": "壳体"}},
        image_parts={"fig1.png": ["10", "11"]},
    )

    result = examiner.check()
    consistency = result["consistency"]

    assert "附图存在标记，但说明书文字部分未定义" in consistency
    assert "说明书附图标记 11 在说明书文字部分未定义" in consistency


def test_formal_examiner_reports_both_issue_types() -> None:
    examiner = FormalExaminer(
        parts_db={"10": {"name": "壳体"}, "12": {"name": "连接件"}},
        image_parts={"fig1.png": ["10", "13"]},
    )

    result = examiner.check()
    consistency = result["consistency"]

    assert "说明书文字部分存在，但附图中未标记" in consistency
    assert "12-连接件" in consistency
    assert "附图存在标记，但说明书文字部分未定义" in consistency
    assert "说明书附图标记 13 在说明书文字部分未定义" in consistency


def test_formal_examiner_normalizes_part_ids() -> None:
    examiner = FormalExaminer(
        parts_db={"10A": {"name": "定位件"}},
        image_parts={"fig1.png": ["10a"]},
    )

    result = examiner.check()
    assert "检查通过" in result["consistency"]


def test_formal_examiner_normalizes_mixed_symbol_ids() -> None:
    examiner = FormalExaminer(
        parts_db={"(11-A)": {"name": "导向件"}},
        image_parts={"fig1.png": ["11a"]},
    )

    result = examiner.check()
    assert "检查通过" in result["consistency"]
