from agents.ai_reply.src.nodes.claim_review_drafting import ClaimReviewDraftingNode
from agents.ai_reply.src.nodes.topup_search_verification import TopupSearchVerificationNode


def test_claim_review_drafting_restructures_units_by_previous_oa(monkeypatch) -> None:
    node = ClaimReviewDraftingNode()

    def _fake_invoke_text_json(messages, task_kind, temperature):
        assert task_kind == "oar_claim_review_drafting"
        user_prompt = messages[1]["content"]
        assert '"unit_id": "P1"' not in user_prompt
        assert '"unit_type": "reused_oa"' not in user_prompt
        assert '"unit_id": "MERGED_1"' in user_prompt
        assert '"unit_type": "merged_into_independent"' in user_prompt
        assert '"unit_id": "NEW_F4"' in user_prompt
        assert '"unit_type": "supplemented_new"' in user_prompt
        assert '"claim_id": "4"' in user_prompt
        return {
            "items": [
                {"unit_id": "MERGED_1", "review_text": "权利要求2并入权利要求1后的评述"},
                {"unit_id": "NEW_F4", "review_text": "权利要求4新增补充评述"},
            ]
        }

    monkeypatch.setattr(node.llm_service, "invoke_text_json", _fake_invoke_text_json)

    result = node._draft_review_units(
        claims_old_structured=[
            {"claim_id": "1", "claim_text": "旧权1", "claim_type": "independent", "parent_claim_ids": []},
            {"claim_id": "2", "claim_text": "旧权2", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "3", "claim_text": "旧权3", "claim_type": "dependent", "parent_claim_ids": ["1"]},
        ],
        claims_effective_structured=[
            {"claim_id": "1", "claim_text": "新权1", "claim_type": "independent", "parent_claim_ids": []},
            {"claim_id": "4", "claim_text": "新权4", "claim_type": "dependent", "parent_claim_ids": ["1"]},
        ],
        prepared_materials={
            "office_action": {
                "paragraphs": [
                    {
                        "paragraph_id": "P1",
                        "claim_ids": ["1"],
                        "content": "OA 对权利要求1的原文评述",
                    },
                    {
                        "paragraph_id": "P2",
                        "claim_ids": ["2", "3"],
                        "content": "OA 对权利要求2-3的组合评述",
                    },
                ]
            }
        },
        added_features=[
            {
                "feature_id": "F2",
                "feature_text": "将旧权2并入权1的新增特征",
                "target_claim_ids": ["1"],
                "source_type": "claim",
                "source_claim_ids": ["2"],
            },
            {
                "feature_id": "F4",
                "feature_text": "权利要求4新增特征",
                "target_claim_ids": ["4"],
                "source_type": "spec",
                "source_claim_ids": [],
            },
        ],
        disputes=[
            {
                "dispute_id": "DSP_1",
                "origin": "response_dispute",
                "claim_ids": ["1"],
                "feature_text": "争议特征1",
                "applicant_opinion": {"reasoning": "申请人对权利要求1提出意见"},
            },
            {
                "dispute_id": "TOPUP_F2",
                "origin": "amendment_review",
                "source_feature_id": "F2",
                "claim_ids": ["1"],
                "feature_text": "将旧权2并入权1的新增特征",
            },
            {
                "dispute_id": "TOPUP_F4",
                "origin": "amendment_review",
                "source_feature_id": "F4",
                "claim_ids": ["4"],
                "feature_text": "权利要求4新增特征",
            },
        ],
        evidence_assessments=[
            {
                "dispute_id": "DSP_1",
                "origin": "response_dispute",
                "assessment": {"verdict": "EXAMINER_CORRECT", "reasoning": "权利要求1维持驳回", "examiner_rejection_rationale": ""},
            },
            {
                "dispute_id": "TOPUP_F2",
                "origin": "amendment_review",
                "source_feature_id": "F2",
                "assessment": {"verdict": "EXAMINER_CORRECT", "reasoning": "并入特征不足以授权", "examiner_rejection_rationale": ""},
            },
            {
                "dispute_id": "TOPUP_F4",
                "origin": "amendment_review",
                "source_feature_id": "F4",
                "assessment": {"verdict": "INCONCLUSIVE", "reasoning": "需要补充检索", "examiner_rejection_rationale": ""},
            },
        ],
        drafted_rejection_reasons={"DSP_1": "权利要求1正式答复"},
    )

    assert [item["unit_id"] for item in result] == ["P1", "MERGED_1", "NEW_F4"]
    assert [item["unit_type"] for item in result] == ["reused_oa", "merged_into_independent", "supplemented_new"]
    assert result[0]["display_claim_ids"] == ["1"]
    assert result[0]["review_text"] == "OA 对权利要求1的原文评述"
    assert result[0]["source_summary"]["added_feature_ids"] == []
    assert result[1]["display_claim_ids"] == ["1"]
    assert result[1]["source_paragraph_ids"] == ["P2"]
    assert result[1]["source_summary"]["merged_source_claim_ids"] == ["2"]
    assert result[1]["source_summary"]["added_feature_ids"] == ["F2"]
    assert result[2]["display_claim_ids"] == ["4"]
    assert result[2]["source_summary"]["added_feature_ids"] == ["F4"]


def test_claim_review_drafting_drops_deleted_oa_units_without_replacement(monkeypatch) -> None:
    node = ClaimReviewDraftingNode()

    def _fail(*args, **kwargs):
        raise AssertionError("LLM should not be called when all OA units are deleted")

    monkeypatch.setattr(node.llm_service, "invoke_text_json", _fail)

    result = node._draft_review_units(
        claims_old_structured=[
            {"claim_id": "2", "claim_text": "旧权2", "claim_type": "dependent", "parent_claim_ids": ["1"]},
        ],
        claims_effective_structured=[
            {"claim_id": "1", "claim_text": "新权1", "claim_type": "independent", "parent_claim_ids": []},
        ],
        prepared_materials={
            "office_action": {
                "paragraphs": [
                    {
                        "paragraph_id": "P2",
                        "claim_ids": ["2"],
                        "content": "OA 对权利要求2的原文评述",
                    },
                ]
            }
        },
        added_features=[],
        disputes=[],
        evidence_assessments=[],
        drafted_rejection_reasons={},
    )

    assert result == []


def test_claim_review_drafting_keeps_reused_oa_out_of_llm_even_with_response_materials(monkeypatch) -> None:
    node = ClaimReviewDraftingNode()

    def _fail(*args, **kwargs):
        raise AssertionError("Pure reused OA unit should not call LLM")

    monkeypatch.setattr(node.llm_service, "invoke_text_json", _fail)

    result = node._draft_review_units(
        claims_old_structured=[
            {"claim_id": "1", "claim_text": "旧权1", "claim_type": "independent", "parent_claim_ids": []},
        ],
        claims_effective_structured=[
            {"claim_id": "1", "claim_text": "新权1", "claim_type": "independent", "parent_claim_ids": []},
        ],
        prepared_materials={
            "office_action": {
                "paragraphs": [
                    {
                        "paragraph_id": "P1",
                        "claim_ids": ["1"],
                        "content": "OA 对权利要求1的原文评述",
                    },
                ]
            }
        },
        added_features=[],
        disputes=[
            {
                "dispute_id": "DSP_1",
                "origin": "response_dispute",
                "claim_ids": ["1"],
                "feature_text": "争议特征1",
                "applicant_opinion": {"reasoning": "申请人意见"},
            },
        ],
        evidence_assessments=[
            {
                "dispute_id": "DSP_1",
                "origin": "response_dispute",
                "assessment": {"verdict": "EXAMINER_CORRECT", "reasoning": "维持驳回", "examiner_rejection_rationale": ""},
            },
        ],
        drafted_rejection_reasons={"DSP_1": "正式答复"},
    )

    assert result == [
        {
            "unit_id": "P1",
            "unit_type": "reused_oa",
            "source_paragraph_ids": ["P1"],
            "display_claim_ids": ["1"],
            "anchor_claim_id": "1",
            "title": "权利要求1",
            "review_text": "OA 对权利要求1的原文评述",
            "claim_snapshots": [
                {"claim_id": "1", "claim_text": "新权1", "claim_type": "independent"},
            ],
            "source_summary": {
                "source_paragraph_ids": ["P1"],
                "merged_source_claim_ids": [],
                "added_feature_ids": [],
                "response_dispute_ids": ["DSP_1"],
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
