from agents.ai_reply.src.nodes.dispute_extraction import DisputeExtractionNode
from agents.ai_reply.src.nodes.report_generation import ReportGenerationNode


def test_dispute_extraction_normalize_claim_ids() -> None:
    node = DisputeExtractionNode.__new__(DisputeExtractionNode)
    assert node._normalize_claim_ids(["1", "2", "2", " 3 "]) == ["1", "2", "3"]
    assert node._normalize_claim_ids("1,2，3") == ["1", "2", "3"]
    assert node._normalize_claim_ids(["权利要求1", "", None, "4"]) == ["1", "4"]
    assert node._normalize_claim_ids("权利要求2-4及权利要求6") == ["2", "3", "4", "6"]


def test_report_generation_builds_response_reply_items_with_claim_ids() -> None:
    node = ReportGenerationNode()
    disputes = [
        {
            "dispute_id": "DSP_A",
            "origin": "response_dispute",
            "claim_ids": ["1", "3"],
            "feature_text": "特征A",
            "applicant_opinion": {"reasoning": "申请人认为 D1 未公开特征A。"},
        }
    ]

    items = node._build_response_reply_items(
        disputes,
        {"DSP_A": "经审查，相关权利要求仍不具备创造性。"},
    )

    assert len(items) == 1
    assert items[0]["claim_ids"] == ["1", "3"]
    assert items[0]["final_examiner_rejection_reason"] == "经审查，相关权利要求仍不具备创造性。"


def test_report_generation_summary_excludes_amendment_reviews() -> None:
    node = ReportGenerationNode()
    response_disputes = [
        {
            "dispute_id": "DSP_R",
            "origin": "response_dispute",
            "applicant_opinion": {"type": "fact_dispute"},
            "evidence_assessment": {"assessment": {"verdict": "EXAMINER_CORRECT"}},
        }
    ]
    response_reply_items = [{"dispute_id": "DSP_R", "final_examiner_rejection_reason": "维持驳回。"}]

    summary = node._generate_summary(
        response_disputes=response_disputes,
        response_reply_items=response_reply_items,
        application_number="",
        current_notice_round=1,
        early_rejection_reason="",
        has_claim_amendment=False,
        added_matter_risk=False,
    )

    assert summary["total_disputes"] == 1
    assert summary["assessed_disputes"] == 1
    assert summary["response_reply_points"] == 1
    assert summary["verdict_distribution"]["examiner_correct"] == 1


def test_report_generation_builds_change_items_by_feature_id() -> None:
    node = ReportGenerationNode()

    items = node._build_change_items(
        added_features=[
            {
                "feature_id": "F1",
                "feature_text": "新增特征A",
                "feature_before_text": "旧特征A",
                "feature_after_text": "新增特征A",
                "target_claim_ids": ["2"],
                "source_type": "spec",
                "source_claim_ids": [],
            }
        ],
        support_findings=[
            {
                "feature_id": "F1",
                "support_found": True,
                "support_basis": "说明书第3页",
            }
        ],
        amendment_disputes=[
            {
                "dispute_id": "TOPUP_F1",
                "origin": "amendment_review",
                "source_feature_id": "F1",
                "evidence_assessment": {
                    "assessment": {
                        "verdict": "APPLICANT_CORRECT",
                        "reasoning": "证据不足。",
                        "examiner_rejection_rationale": "结合D1仍可维持驳回。",
                    },
                    "evidence": [
                        {"doc_id": "D1", "quote": "q1", "analysis": "a1"},
                    ],
                },
            }
        ],
    )

    assert len(items) == 1
    assert items[0]["feature_id"] == "F1"
    assert items[0]["feature_before_text"] == "旧特征A"
    assert items[0]["feature_after_text"] == "新增特征A"
    assert items[0]["contains_added_text"] is True
    assert items[0]["target_claim_ids"] == ["2"]
    assert items[0]["source_claim_ids"] == []
    assert items[0]["support_finding"]["support_basis"] == "说明书第3页"
    assert items[0]["assessment"]["verdict"] == "APPLICANT_CORRECT"
    assert items[0]["final_review_reason"] == "结合D1仍可维持驳回。"


def test_report_generation_excludes_claim_source_change_items_from_section3() -> None:
    node = ReportGenerationNode()

    items = node._build_change_items(
        added_features=[
            {
                "feature_id": "F2",
                "feature_text": "保持不变的特征",
                "feature_before_text": "保持不变的特征",
                "feature_after_text": "保持不变的特征",
                "target_claim_ids": ["3"],
                "source_type": "claim",
                "source_claim_ids": ["1"],
            }
        ],
        support_findings=[],
        amendment_disputes=[],
    )

    assert items == []
