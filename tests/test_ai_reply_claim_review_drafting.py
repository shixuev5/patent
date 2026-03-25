from agents.ai_reply.src.nodes.claim_review_drafting import ClaimReviewDraftingNode
from agents.ai_reply.src.nodes.topup_search_verification import TopupSearchVerificationNode


def test_claim_review_drafting_builds_materials_for_all_review_modes(monkeypatch) -> None:
    node = ClaimReviewDraftingNode()

    def _fake_invoke_text_json(messages, task_kind, temperature):
        assert task_kind == "oar_claim_review_drafting"
        user_prompt = messages[1]["content"]
        assert '"claim_id": "1"' in user_prompt
        assert '"oa_materials": [' in user_prompt
        assert '"claim_id": "2"' in user_prompt
        assert '"response_materials": [' in user_prompt
        assert '"claim_id": "3"' in user_prompt
        assert '"amendment_materials": [' in user_prompt
        assert '"claim_id": "4"' in user_prompt
        assert '"review_mode": "mixed"' in user_prompt
        return {
            "items": [
                {"claim_id": "1", "review_text": "权利要求1评述"},
                {"claim_id": "2", "review_text": "权利要求2评述"},
                {"claim_id": "3", "review_text": "权利要求3评述"},
                {"claim_id": "4", "review_text": "权利要求4评述"},
            ]
        }

    monkeypatch.setattr(node.llm_service, "invoke_text_json", _fake_invoke_text_json)

    result = node._draft_claim_reviews(
        claims_effective_structured=[
            {"claim_id": "1", "claim_text": "权利要求1文本"},
            {"claim_id": "2", "claim_text": "权利要求2文本"},
            {"claim_id": "3", "claim_text": "权利要求3文本"},
            {"claim_id": "4", "claim_text": "权利要求4文本"},
        ],
        prepared_materials={
            "office_action": {
                "paragraphs": [
                    {
                        "paragraph_id": "P1",
                        "claim_ids": ["1"],
                        "evaluation": "negative",
                        "content": "OA 对权利要求1的原文评述",
                    }
                ]
            }
        },
        added_features=[
            {
                "feature_id": "F3",
                "feature_text": "新增特征3",
                "target_claim_ids": ["3"],
                "source_type": "spec",
                "source_claim_ids": [],
            },
            {
                "feature_id": "F4",
                "feature_text": "新增特征4",
                "target_claim_ids": ["4"],
                "source_type": "claim",
                "source_claim_ids": ["2"],
            },
        ],
        disputes=[
            {
                "dispute_id": "DSP_2",
                "origin": "response_dispute",
                "claim_ids": ["2"],
                "feature_text": "争议特征2",
                "applicant_opinion": {"reasoning": "申请人对权利要求2提出意见"},
            },
            {
                "dispute_id": "TOPUP_F3",
                "origin": "amendment_review",
                "source_feature_id": "F3",
                "claim_ids": ["3"],
                "feature_text": "新增特征3",
            },
            {
                "dispute_id": "DSP_4",
                "origin": "response_dispute",
                "claim_ids": ["4"],
                "feature_text": "争议特征4",
                "applicant_opinion": {"reasoning": "申请人对权利要求4提出意见"},
            },
            {
                "dispute_id": "TOPUP_F4",
                "origin": "amendment_review",
                "source_feature_id": "F4",
                "claim_ids": ["4"],
                "feature_text": "新增特征4",
            },
        ],
        evidence_assessments=[
            {
                "dispute_id": "DSP_2",
                "origin": "response_dispute",
                "assessment": {"verdict": "EXAMINER_CORRECT", "reasoning": "权利要求2维持驳回", "examiner_rejection_rationale": ""},
            },
            {
                "dispute_id": "TOPUP_F3",
                "origin": "amendment_review",
                "source_feature_id": "F3",
                "assessment": {"verdict": "EXAMINER_CORRECT", "reasoning": "新增特征3不足以授权", "examiner_rejection_rationale": ""},
                "evidence": [{"doc_id": "D1", "quote": "q3", "analysis": "a3"}],
            },
            {
                "dispute_id": "DSP_4",
                "origin": "response_dispute",
                "assessment": {"verdict": "EXAMINER_CORRECT", "reasoning": "权利要求4答复不能成立", "examiner_rejection_rationale": ""},
            },
            {
                "dispute_id": "TOPUP_F4",
                "origin": "amendment_review",
                "source_feature_id": "F4",
                "assessment": {"verdict": "APPLICANT_CORRECT", "reasoning": "新增特征4提升有限", "examiner_rejection_rationale": "结合D2仍可维持驳回"},
                "evidence": [{"doc_id": "D2", "quote": "q4", "analysis": "a4"}],
            },
        ],
        drafted_rejection_reasons={
            "DSP_2": "权利要求2正式答复",
            "DSP_4": "权利要求4正式答复",
        },
    )

    assert [item["claim_id"] for item in result] == ["1", "2", "3", "4"]
    assert [item["review_mode"] for item in result] == [
        "reused_oa",
        "response_based",
        "amendment_based",
        "mixed",
    ]
    assert result[0]["source_summary"]["oa_paragraph_ids"] == ["P1"]
    assert result[1]["source_summary"]["response_dispute_ids"] == ["DSP_2"]
    assert result[2]["source_summary"]["amendment_feature_ids"] == ["F3"]
    assert result[3]["source_summary"]["response_dispute_ids"] == ["DSP_4"]
    assert result[3]["source_summary"]["amendment_feature_ids"] == ["F4"]


def test_claim_review_drafting_returns_placeholder_without_materials(monkeypatch) -> None:
    node = ClaimReviewDraftingNode()

    def _fail(*args, **kwargs):
        raise AssertionError("LLM should not be called when no drafting materials exist")

    monkeypatch.setattr(node.llm_service, "invoke_text_json", _fail)

    result = node._draft_claim_reviews(
        claims_effective_structured=[{"claim_id": "1", "claim_text": "权利要求1文本"}],
        prepared_materials={"office_action": {"paragraphs": []}},
        added_features=[],
        disputes=[],
        evidence_assessments=[],
        drafted_rejection_reasons={},
    )

    assert result == [
        {
            "claim_id": "1",
            "claim_text": "权利要求1文本",
            "review_mode": "reused_oa",
            "review_text": "当前未提取到可复用的权利要求评述。",
            "source_summary": {
                "oa_paragraph_ids": [],
                "response_dispute_ids": [],
                "amendment_feature_ids": [],
            },
        }
    ]


def test_topup_search_verification_sets_amendment_origin_fields(monkeypatch) -> None:
    node = TopupSearchVerificationNode()

    monkeypatch.setattr(node, "_search_local_evidence", lambda **kwargs: ([], {}))
    monkeypatch.setattr(node, "_build_engine_queries", lambda *args, **kwargs: {"openalex": [], "tavily": []})
    monkeypatch.setattr(node.external_evidence_aggregator, "search_evidence", lambda **kwargs: ([], [], {}))
    monkeypatch.setattr(node, "_to_external_evidence_items", lambda candidates: [])
    monkeypatch.setattr(
        node.llm_service,
        "invoke_text_json",
        lambda **kwargs: {
            "examiner_opinion": {"type": "document_based", "supporting_docs": [{"doc_id": "MODEL", "cited_text": ""}], "reasoning": "审查理由"},
            "applicant_opinion": {"type": "fact_dispute", "reasoning": "申请人理由", "core_conflict": "核心冲突"},
            "assessment": {"verdict": "INCONCLUSIVE", "reasoning": "无法定论", "confidence": 0.4, "examiner_rejection_rationale": ""},
            "evidence": [],
        },
    )
    monkeypatch.setattr(
        node,
        "_normalize_llm_output",
        lambda response, allowed_doc_ids, evidence_map: {
            "examiner_opinion": {"type": "document_based", "supporting_docs": [{"doc_id": "MODEL", "cited_text": ""}], "reasoning": "审查理由"},
            "applicant_opinion": {"type": "fact_dispute", "reasoning": "申请人理由", "core_conflict": "核心冲突"},
            "assessment": {"verdict": "INCONCLUSIVE", "reasoning": "无法定论", "confidence": 0.4, "examiner_rejection_rationale": ""},
            "evidence": [],
            "used_doc_ids": [],
        },
    )

    dispute, assessment = node._evaluate_task(
        task={"task_id": "F1", "claim_ids": ["2"], "feature_text": "新增特征"},
        claims=[{"claim_id": "2", "claim_text": "权利要求2文本"}],
        comparison_docs={},
        priority_date="2020-01-01",
        local_retriever=None,
    )

    assert dispute["origin"] == "amendment_review"
    assert dispute["source_feature_id"] == "F1"
    assert assessment["origin"] == "amendment_review"
    assert assessment["source_feature_id"] == "F1"
