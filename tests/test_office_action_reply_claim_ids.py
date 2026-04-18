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
        added_matter_risk_summary="",
        has_claim_amendment=False,
        added_matter_risk=False,
    )

    assert summary["total_disputes"] == 1
    assert summary["assessed_disputes"] == 1
    assert summary["response_reply_points"] == 1
    assert summary["verdict_distribution"]["examiner_correct"] == 1


def test_report_generation_builds_claim_change_groups_by_claim_id() -> None:
    node = ReportGenerationNode()

    groups = node._build_claim_change_groups(
        substantive_amendments=[
            {
                "amendment_id": "F1",
                "feature_text": "新增特征A",
                "feature_before_text": "旧特征A",
                "feature_after_text": "新增特征A",
                "target_claim_ids": ["2"],
                "amendment_kind": "spec_feature_addition",
                "content_origin": "specification",
                "source_claim_ids": [],
            }
        ],
        support_findings=[
            {
                "amendment_id": "F1",
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
        effective_claims=[{"claim_id": "2", "claim_type": "dependent"}],
    )

    assert len(groups) == 1
    assert groups[0]["claim_id"] == "2"
    assert groups[0]["claim_type"] == "dependent"
    assert len(groups[0]["items"]) == 1
    item = groups[0]["items"][0]
    assert item["amendment_id"] == "F1"
    assert item["feature_before_text"] == "旧特征A"
    assert item["feature_after_text"] == "新增特征A"
    assert item["contains_added_text"] is True
    assert item["source_claim_ids"] == []
    assert item["support_finding"]["support_basis"] == "说明书第3页"
    assert item["assessment"]["verdict"] == "APPLICANT_CORRECT"
    assert item["has_ai_assessment"] is True
    assert item["final_review_reason"] == "结合D1仍可维持驳回。"


def test_report_generation_splits_same_feature_into_multiple_claim_groups() -> None:
    node = ReportGenerationNode()

    groups = node._build_claim_change_groups(
        substantive_amendments=[
            {
                "amendment_id": "F2",
                "feature_text": "多个权利要求共享的新特征",
                "feature_before_text": "旧片段",
                "feature_after_text": "多个权利要求共享的新特征",
                "target_claim_ids": ["1", "3"],
                "amendment_kind": "claim_feature_merge",
                "content_origin": "old_claim",
                "source_claim_ids": ["9"],
            }
        ],
        support_findings=[],
        amendment_disputes=[],
        effective_claims=[
            {"claim_id": "1", "claim_type": "independent"},
            {"claim_id": "3", "claim_type": "dependent"},
        ],
    )

    assert [group["claim_id"] for group in groups] == ["1", "3"]
    assert [group["claim_type"] for group in groups] == ["independent", "dependent"]
    assert groups[0]["items"][0]["amendment_id"] == "F2"
    assert groups[1]["items"][0]["amendment_id"] == "F2"
    assert groups[0]["items"][0]["amendment_kind"] == "claim_feature_merge"
    assert groups[1]["items"][0]["source_claim_ids"] == ["9"]


def test_report_generation_keeps_all_change_items_under_same_claim_group() -> None:
    node = ReportGenerationNode()

    groups = node._build_claim_change_groups(
        substantive_amendments=[
            {
                "amendment_id": "F2",
                "feature_text": "从旧权1上提的特征",
                "feature_before_text": "旧权1中的特征",
                "feature_after_text": "从旧权1上提的特征",
                "target_claim_ids": ["3"],
                "amendment_kind": "claim_feature_merge",
                "content_origin": "old_claim",
                "source_claim_ids": ["1"],
            },
            {
                "amendment_id": "F3",
                "feature_text": "从说明书补入的特征",
                "feature_before_text": "",
                "feature_after_text": "从说明书补入的特征",
                "target_claim_ids": ["3"],
                "amendment_kind": "spec_feature_addition",
                "content_origin": "specification",
                "source_claim_ids": [],
            },
        ],
        support_findings=[],
        amendment_disputes=[],
        effective_claims=[{"claim_id": "3", "claim_type": "dependent"}],
    )

    assert len(groups) == 1
    assert groups[0]["claim_id"] == "3"
    assert [item["amendment_id"] for item in groups[0]["items"]] == ["F2", "F3"]
    assert [item["amendment_kind"] for item in groups[0]["items"]] == ["claim_feature_merge", "spec_feature_addition"]


def test_report_generation_places_spec_items_last_within_claim_group() -> None:
    node = ReportGenerationNode()

    groups = node._build_claim_change_groups(
        substantive_amendments=[
            {
                "amendment_id": "F3",
                "feature_text": "说明书补入特征",
                "feature_before_text": "",
                "feature_after_text": "说明书补入特征",
                "target_claim_ids": ["4"],
                "amendment_kind": "spec_feature_addition",
                "content_origin": "specification",
                "source_claim_ids": [],
            },
            {
                "amendment_id": "F1",
                "feature_text": "从权并入特征A",
                "feature_before_text": "旧特征A",
                "feature_after_text": "从权并入特征A",
                "target_claim_ids": ["4"],
                "amendment_kind": "claim_feature_merge",
                "content_origin": "old_claim",
                "source_claim_ids": ["3"],
            },
            {
                "amendment_id": "F2",
                "feature_text": "从权并入特征B",
                "feature_before_text": "旧特征B",
                "feature_after_text": "从权并入特征B",
                "target_claim_ids": ["4"],
                "amendment_kind": "claim_feature_merge",
                "content_origin": "old_claim",
                "source_claim_ids": ["2"],
            },
        ],
        support_findings=[],
        amendment_disputes=[],
        effective_claims=[{"claim_id": "4", "claim_type": "dependent"}],
    )

    assert [item["amendment_id"] for item in groups[0]["items"]] == ["F1", "F2", "F3"]


def test_report_generation_marks_ai_assessment_presence_per_change_item() -> None:
    node = ReportGenerationNode()

    groups = node._build_claim_change_groups(
        substantive_amendments=[
            {
                "amendment_id": "F2",
                "feature_text": "需AI判断的特征",
                "feature_before_text": "旧特征",
                "feature_after_text": "需AI判断的特征",
                "target_claim_ids": ["3"],
                "amendment_kind": "claim_feature_merge",
                "content_origin": "old_claim",
                "source_claim_ids": ["1"],
            },
            {
                "amendment_id": "F3",
                "feature_text": "无需AI判断的特征",
                "feature_before_text": "无需AI判断的特征",
                "feature_after_text": "无需AI判断的特征",
                "target_claim_ids": ["3"],
                "amendment_kind": "spec_feature_addition",
                "content_origin": "specification",
                "source_claim_ids": [],
            },
        ],
        support_findings=[],
        amendment_disputes=[
            {
                "source_feature_id": "F2",
                "evidence_assessment": {
                    "assessment": {"verdict": "EXAMINER_CORRECT", "reasoning": "已有公开。"},
                    "evidence": [],
                },
            }
        ],
        effective_claims=[{"claim_id": "3", "claim_type": "dependent"}],
    )

    assert len(groups) == 1
    assert [item["has_ai_assessment"] for item in groups[0]["items"]] == [True, False]


def test_report_generation_includes_search_followup_section() -> None:
    node = ReportGenerationNode()

    report = node._generate_report(
        {
            "task_id": "task-1",
            "prepared_materials": {
                "office_action": {
                    "application_number": "202310001234.5",
                    "current_notice_round": 2,
                }
            },
            "disputes": [],
            "evidence_assessments": [],
            "drafted_rejection_reasons": {},
            "review_units": [],
            "search_followup_section": {
                "needed": True,
                "status": "complete",
                "objective": "围绕新增特征继续补检。",
                "trigger_reasons": ["现有核查结论暂不确定"],
                "gap_summaries": [],
                "search_elements": [{"element_name": "新增特征A", "keywords_zh": ["新增特征A"], "keywords_en": []}],
                "suggested_constraints": {"priority_date": "2022-06-01"},
                "source_dispute_ids": ["TOPUP_A1"],
                "source_feature_ids": ["A1"],
                "missing_items": [],
            },
        }
    )

    assert report["search_followup_section"]["needed"] is True
    assert report["search_followup_section"]["source_feature_ids"] == ["A1"]
