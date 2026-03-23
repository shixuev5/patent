from agents.ai_reply.src.nodes.rejection_drafting import RejectionDraftingNode


def test_rejection_drafting_skips_when_no_applicant_correct() -> None:
    node = RejectionDraftingNode()
    result = node._draft_rejection_reasons(
        disputes=[
            {
                "dispute_id": "DSP_1",
                "claim_ids": ["1"],
                "feature_text": "特征A",
                "examiner_opinion": {},
                "applicant_opinion": {},
            }
        ],
        evidence_assessments=[
            {
                "dispute_id": "DSP_1",
                "claim_text": "权利要求1",
                "assessment": {
                    "verdict": "INCONCLUSIVE",
                    "examiner_rejection_rationale": "",
                },
                "evidence": [],
            }
        ],
    )

    assert result == {}


def test_rejection_drafting_returns_mapping_by_dispute_id(monkeypatch) -> None:
    node = RejectionDraftingNode()

    def _fake_invoke_text_json(messages, task_kind, temperature):
        assert task_kind == "oar_rejection_drafting"
        return {
            "items": [
                {
                    "dispute_id": "DSP_1",
                    "final_examiner_rejection_reason": "经审查，权利要求1仍不具备创造性。",
                },
                {
                    "dispute_id": "DSP_2",
                    "final_examiner_rejection_reason": "经审查，权利要求2仍不具备创造性。",
                },
            ]
        }

    monkeypatch.setattr(node.llm_service, "invoke_text_json", _fake_invoke_text_json)

    result = node._draft_rejection_reasons(
        disputes=[
            {
                "dispute_id": "DSP_1",
                "claim_ids": ["1"],
                "feature_text": "特征A",
                "examiner_opinion": {},
                "applicant_opinion": {},
            },
            {
                "dispute_id": "DSP_2",
                "claim_ids": ["2"],
                "feature_text": "特征B",
                "examiner_opinion": {},
                "applicant_opinion": {},
            },
        ],
        evidence_assessments=[
            {
                "dispute_id": "DSP_1",
                "claim_text": "权利要求1",
                "assessment": {
                    "verdict": "APPLICANT_CORRECT",
                    "examiner_rejection_rationale": "结合D1公开内容仍可维持驳回。",
                },
                "evidence": [{"doc_id": "D1", "quote": "q1", "analysis": "a1"}],
            },
            {
                "dispute_id": "DSP_2",
                "claim_text": "权利要求2",
                "assessment": {
                    "verdict": "APPLICANT_CORRECT",
                    "examiner_rejection_rationale": "结合D2公开内容仍可维持驳回。",
                },
                "evidence": [{"doc_id": "D2", "quote": "q2", "analysis": "a2"}],
            },
        ],
    )

    assert result == {
        "DSP_1": "经审查，权利要求1仍不具备创造性。",
        "DSP_2": "经审查，权利要求2仍不具备创造性。",
    }


def test_rejection_drafting_raises_when_missing_output_item() -> None:
    node = RejectionDraftingNode()
    try:
        node._normalize_llm_output(
            {"items": [{"dispute_id": "DSP_1", "final_examiner_rejection_reason": "经审查，仍不具备创造性。"}]},
            [
                {"dispute_id": "DSP_1"},
                {"dispute_id": "DSP_2"},
            ],
        )
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "DSP_2" in str(exc)


def test_rejection_drafting_raises_when_duplicate_output_item() -> None:
    node = RejectionDraftingNode()
    try:
        node._normalize_llm_output(
            {
                "items": [
                    {"dispute_id": "DSP_1", "final_examiner_rejection_reason": "理由1"},
                    {"dispute_id": "DSP_1", "final_examiner_rejection_reason": "理由2"},
                ]
            },
            [{"dispute_id": "DSP_1"}],
        )
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "重复" in str(exc)
