from agents.ai_reply.src.nodes.dispute_extraction import DisputeExtractionNode
from agents.ai_reply.src.nodes.report_generation import ReportGenerationNode


def test_dispute_extraction_normalize_claim_ids() -> None:
    node = DisputeExtractionNode.__new__(DisputeExtractionNode)
    assert node._normalize_claim_ids(["1", "2", "2", " 3 "]) == ["1", "2", "3"]
    assert node._normalize_claim_ids("1,2，3") == ["1", "2", "3"]
    assert node._normalize_claim_ids(["权利要求1", "", None, "4"]) == ["1", "4"]
    assert node._normalize_claim_ids("权利要求2-4及权利要求6") == ["2", "3", "4", "6"]


def test_report_generation_collect_second_notice_with_claim_ids() -> None:
    node = ReportGenerationNode()
    disputes = [
        {
            "dispute_id": "DSP_A",
            "claim_ids": ["1", "3"],
            "feature_text": "特征A",
            "evidence_assessment": {
                "assessment": {
                    "verdict": "APPLICANT_CORRECT",
                    "examiner_rejection_reason": "经审查，仍不具备创造性。",
                }
            },
        }
    ]
    items = node._collect_second_office_action_items(disputes)
    assert len(items) == 1
    assert items[0]["claim_ids"] == ["1", "3"]


def test_report_generation_raise_when_missing_rejection_reason() -> None:
    node = ReportGenerationNode()
    disputes = [
        {
            "dispute_id": "DSP_B",
            "claim_ids": ["2"],
            "feature_text": "特征B",
            "evidence_assessment": {
                "assessment": {
                    "verdict": "APPLICANT_CORRECT",
                    "examiner_rejection_reason": "",
                }
            },
        }
    ]
    try:
        node._collect_second_office_action_items(disputes)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "examiner_rejection_reason" in str(exc)
