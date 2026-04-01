from agents.ai_reply.src.nodes.claim_review_drafting import ClaimReviewDraftingNode
from agents.ai_reply.src.nodes.topup_search_verification import TopupSearchVerificationNode


def test_claim_review_drafting_aggregates_independent_card_and_residual_group(monkeypatch) -> None:
    node = ClaimReviewDraftingNode()

    def _fake_invoke_text_json(messages, task_kind, temperature):
        assert task_kind == "oar_claim_review_drafting"
        user_prompt = messages[1]["content"]
        assert '"unit_id": "P1"' in user_prompt
        assert '"unit_type": "evidence_restructured"' in user_prompt
        assert '"unit_id": "P2"' in user_prompt
        assert '"unit_type": "dependent_group_restructured"' in user_prompt
        assert '"unit_id": "MERGED_1"' not in user_prompt
        assert '"unit_id": "NEW_F1"' not in user_prompt
        assert '"display_claim_ids": [' in user_prompt
        assert '"1"' in user_prompt
        assert '"3"' in user_prompt
        assert '"4"' in user_prompt
        assert '"review_before_text": "OA 对权利要求1的原文评述\\nOA 对权利要求2-4的组合评述"' in user_prompt
        assert '"feature_text": "将旧权2并入权1的新增特征"' in user_prompt
        assert '"feature_text": "权利要求1说明书新增特征"' in user_prompt
        return {
            "items": [
                {"unit_id": "P1", "review_text": "权利要求1聚合后的重组评述"},
                {"unit_id": "P2", "review_text": "权利要求3-4残余组合评述"},
            ]
        }

    monkeypatch.setattr(node.llm_service, "invoke_text_json", _fake_invoke_text_json)

    result = node._draft_review_units(
        claims_old_structured=[
            {"claim_id": "1", "claim_text": "旧权1", "claim_type": "independent", "parent_claim_ids": []},
            {"claim_id": "2", "claim_text": "旧权2", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "3", "claim_text": "旧权3", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "4", "claim_text": "旧权4", "claim_type": "dependent", "parent_claim_ids": ["1"]},
        ],
        claims_effective_structured=[
            {"claim_id": "1", "claim_text": "新权1", "claim_type": "independent", "parent_claim_ids": []},
            {"claim_id": "3", "claim_text": "新权3", "claim_type": "dependent", "parent_claim_ids": ["1"]},
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
                        "claim_ids": ["2", "3", "4"],
                        "content": "OA 对权利要求2-4的组合评述",
                    },
                ]
            }
        },
        added_features=[
            {
                "feature_id": "F2",
                "feature_text": "将旧权2并入权1的新增特征",
                "feature_before_text": "",
                "feature_after_text": "将旧权2并入权1的新增特征",
                "target_claim_ids": ["1"],
                "source_type": "claim",
                "source_claim_ids": ["2"],
            },
            {
                "feature_id": "F1",
                "feature_text": "权利要求1说明书新增特征",
                "feature_before_text": "",
                "feature_after_text": "权利要求1说明书新增特征",
                "target_claim_ids": ["1"],
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
                "dispute_id": "TOPUP_F1",
                "origin": "amendment_review",
                "source_feature_id": "F1",
                "claim_ids": ["1"],
                "feature_text": "权利要求1说明书新增特征",
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
                "dispute_id": "TOPUP_F1",
                "origin": "amendment_review",
                "source_feature_id": "F1",
                "assessment": {"verdict": "INCONCLUSIVE", "reasoning": "需要补充检索", "examiner_rejection_rationale": ""},
            },
        ],
        drafted_rejection_reasons={"DSP_1": "权利要求1正式答复"},
    )

    assert [item["unit_id"] for item in result] == ["P1", "P2"]
    assert [item["unit_type"] for item in result] == ["evidence_restructured", "dependent_group_restructured"]

    independent_unit = result[0]
    assert independent_unit["display_claim_ids"] == ["1"]
    assert independent_unit["source_paragraph_ids"] == ["P1", "P2"]
    assert independent_unit["review_before_text"] == "OA 对权利要求1的原文评述\nOA 对权利要求2-4的组合评述"
    assert independent_unit["review_text"] == "权利要求1聚合后的重组评述"
    assert independent_unit["claim_snapshots"] == [
        {"claim_id": "1", "claim_before_text": "旧权1", "claim_text": "新权1", "claim_type": "independent"},
    ]
    assert independent_unit["source_summary"] == {
        "source_paragraph_ids": ["P1", "P2"],
        "merged_source_claim_ids": ["2"],
        "added_feature_ids": ["F2", "F1"],
        "response_dispute_ids": ["DSP_1"],
        "amendment_feature_ids": ["F2", "F1"],
    }

    residual_unit = result[1]
    assert residual_unit["display_claim_ids"] == ["3", "4"]
    assert residual_unit["source_paragraph_ids"] == ["P2"]
    assert residual_unit["review_before_text"] == "OA 对权利要求2-4的组合评述"
    assert residual_unit["review_text"] == "权利要求3-4残余组合评述"
    assert residual_unit["claim_snapshots"] == [
        {"claim_id": "3", "claim_before_text": "旧权3", "claim_text": "新权3", "claim_type": "dependent"},
        {"claim_id": "4", "claim_before_text": "旧权4", "claim_text": "新权4", "claim_type": "dependent"},
    ]
    assert residual_unit["source_summary"] == {
        "source_paragraph_ids": ["P2"],
        "merged_source_claim_ids": [],
        "added_feature_ids": [],
        "response_dispute_ids": [],
        "amendment_feature_ids": [],
    }


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


def test_claim_review_drafting_builds_supplemented_independent_card_without_primary_oa(monkeypatch) -> None:
    node = ClaimReviewDraftingNode()

    def _fake_invoke_text_json(messages, task_kind, temperature):
        assert task_kind == "oar_claim_review_drafting"
        user_prompt = messages[1]["content"]
        assert '"unit_id": "IND_1"' in user_prompt
        assert '"unit_type": "supplemented_new"' in user_prompt
        assert '"source_paragraph_ids": []' in user_prompt
        assert '"feature_text": "权利要求1新增特征"' in user_prompt
        return {
            "items": [
                {"unit_id": "IND_1", "review_text": "权利要求1无主段落时的补充评述"},
            ]
        }

    monkeypatch.setattr(node.llm_service, "invoke_text_json", _fake_invoke_text_json)

    result = node._draft_review_units(
        claims_old_structured=[],
        claims_effective_structured=[
            {"claim_id": "1", "claim_text": "新权1", "claim_type": "independent", "parent_claim_ids": []},
        ],
        prepared_materials={"office_action": {"paragraphs": []}},
        added_features=[
            {
                "feature_id": "F1",
                "feature_text": "权利要求1新增特征",
                "feature_before_text": "",
                "feature_after_text": "权利要求1新增特征",
                "target_claim_ids": ["1"],
                "source_type": "spec",
                "source_claim_ids": [],
            },
        ],
        disputes=[
            {
                "dispute_id": "TOPUP_F1",
                "origin": "amendment_review",
                "source_feature_id": "F1",
                "claim_ids": ["1"],
                "feature_text": "权利要求1新增特征",
            },
        ],
        evidence_assessments=[
            {
                "dispute_id": "TOPUP_F1",
                "origin": "amendment_review",
                "source_feature_id": "F1",
                "assessment": {"verdict": "INCONCLUSIVE", "reasoning": "需要补充检索", "examiner_rejection_rationale": ""},
            },
        ],
        drafted_rejection_reasons={},
    )

    assert result == [
        {
            "unit_id": "IND_1",
            "unit_type": "supplemented_new",
            "source_paragraph_ids": [],
            "display_claim_ids": ["1"],
            "anchor_claim_id": "1",
            "title": "权利要求1",
            "review_before_text": "当前未提取到可复用的审查评述。",
            "review_text": "权利要求1无主段落时的补充评述",
            "claim_snapshots": [
                {"claim_id": "1", "claim_before_text": "", "claim_text": "新权1", "claim_type": "independent"},
            ],
            "source_summary": {
                "source_paragraph_ids": [],
                "merged_source_claim_ids": [],
                "added_feature_ids": ["F1"],
                "response_dispute_ids": [],
                "amendment_feature_ids": ["F1"],
            },
        }
    ]


def test_claim_review_drafting_keeps_direct_primary_oa_out_of_llm(monkeypatch) -> None:
    node = ClaimReviewDraftingNode()

    def _fail(*args, **kwargs):
        raise AssertionError("Direct primary OA unit should not call LLM")

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
        disputes=[],
        evidence_assessments=[],
        drafted_rejection_reasons={},
    )

    assert result == [
        {
            "unit_id": "P1",
            "unit_type": "evidence_restructured",
            "source_paragraph_ids": ["P1"],
            "display_claim_ids": ["1"],
            "anchor_claim_id": "1",
            "title": "权利要求1",
            "review_before_text": "OA 对权利要求1的原文评述",
            "review_text": "OA 对权利要求1的原文评述",
            "claim_snapshots": [
                {"claim_id": "1", "claim_before_text": "旧权1", "claim_text": "新权1", "claim_type": "independent"},
            ],
            "source_summary": {
                "source_paragraph_ids": ["P1"],
                "merged_source_claim_ids": [],
                "added_feature_ids": [],
                "response_dispute_ids": [],
                "amendment_feature_ids": [],
            },
        }
    ]


def test_claim_review_drafting_reorders_renumbered_claims_by_effective_claim_order(monkeypatch) -> None:
    node = ClaimReviewDraftingNode()

    def _fake_invoke_text_json(messages, task_kind, temperature):
        assert task_kind == "oar_claim_review_drafting"
        user_prompt = messages[1]["content"]
        assert '"unit_id": "P1"' in user_prompt
        assert '"unit_id": "P4"' in user_prompt
        assert '"unit_id": "P5"' in user_prompt
        assert '"unit_id": "P6"' in user_prompt
        assert '"unit_id": "P7"' in user_prompt
        assert '"unit_id": "P8"' in user_prompt
        assert '"unit_id": "P9"' in user_prompt
        assert '"unit_id": "P10"' in user_prompt
        assert '"review_before_text": "OA 对旧权1的原文评述\\nOA 对旧权2的原文评述\\nOA 对旧权3的原文评述"' in user_prompt
        assert '"review_before_text": "OA 对旧权4的原文评述"' in user_prompt
        assert '"review_before_text": "OA 对旧权9的原文评述"' in user_prompt
        return {
            "items": [
                {"unit_id": "P1", "review_text": "现权1重组评述"},
                {"unit_id": "P4", "review_text": "现权2重组评述"},
                {"unit_id": "P5", "review_text": "现权3重组评述"},
                {"unit_id": "P6", "review_text": "现权4重组评述"},
                {"unit_id": "P7", "review_text": "现权5重组评述"},
                {"unit_id": "P8", "review_text": "现权6重组评述"},
                {"unit_id": "P9", "review_text": "现权7重组评述"},
                {"unit_id": "P10", "review_text": "现权8重组评述"},
            ]
        }

    monkeypatch.setattr(node.llm_service, "invoke_text_json", _fake_invoke_text_json)

    result = node._draft_review_units(
        claims_old_structured=[
            {"claim_id": "1", "claim_text": "旧权1", "claim_type": "independent", "parent_claim_ids": []},
            {"claim_id": "2", "claim_text": "旧权2", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "3", "claim_text": "旧权3", "claim_type": "dependent", "parent_claim_ids": ["2"]},
            {"claim_id": "4", "claim_text": "旧权4", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "5", "claim_text": "旧权5", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "6", "claim_text": "旧权6", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "7", "claim_text": "旧权7", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "8", "claim_text": "旧权8", "claim_type": "dependent", "parent_claim_ids": ["5"]},
            {"claim_id": "9", "claim_text": "旧权9", "claim_type": "independent", "parent_claim_ids": []},
            {"claim_id": "10", "claim_text": "旧权10", "claim_type": "independent", "parent_claim_ids": []},
        ],
        claims_effective_structured=[
            {"claim_id": "1", "claim_text": "现权1", "claim_type": "independent", "parent_claim_ids": []},
            {"claim_id": "2", "claim_text": "现权2", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "3", "claim_text": "现权3", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "4", "claim_text": "现权4", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "5", "claim_text": "现权5", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "6", "claim_text": "现权6", "claim_type": "dependent", "parent_claim_ids": ["5"]},
            {"claim_id": "7", "claim_text": "现权7", "claim_type": "independent", "parent_claim_ids": []},
            {"claim_id": "8", "claim_text": "现权8", "claim_type": "independent", "parent_claim_ids": []},
        ],
        prepared_materials={
            "office_action": {
                "paragraphs": [
                    {"paragraph_id": "P1", "claim_ids": ["1"], "content": "OA 对旧权1的原文评述"},
                    {"paragraph_id": "P2", "claim_ids": ["2"], "content": "OA 对旧权2的原文评述"},
                    {"paragraph_id": "P3", "claim_ids": ["3"], "content": "OA 对旧权3的原文评述"},
                    {"paragraph_id": "P4", "claim_ids": ["4"], "content": "OA 对旧权4的原文评述"},
                    {"paragraph_id": "P5", "claim_ids": ["5"], "content": "OA 对旧权5的原文评述"},
                    {"paragraph_id": "P6", "claim_ids": ["6"], "content": "OA 对旧权6的原文评述"},
                    {"paragraph_id": "P7", "claim_ids": ["7"], "content": "OA 对旧权7的原文评述"},
                    {"paragraph_id": "P8", "claim_ids": ["8"], "content": "OA 对旧权8的原文评述"},
                    {"paragraph_id": "P9", "claim_ids": ["9"], "content": "OA 对旧权9的原文评述"},
                    {"paragraph_id": "P10", "claim_ids": ["10"], "content": "OA 对旧权10的原文评述"},
                ]
            }
        },
        added_features=[
            {"feature_id": "F1", "feature_text": "旧权2并入现权1", "feature_before_text": "", "feature_after_text": "旧权2并入现权1", "target_claim_ids": ["1"], "source_type": "claim", "source_claim_ids": ["2"]},
            {"feature_id": "F2", "feature_text": "旧权3并入现权1", "feature_before_text": "", "feature_after_text": "旧权3并入现权1", "target_claim_ids": ["1"], "source_type": "claim", "source_claim_ids": ["3"]},
            {"feature_id": "F3", "feature_text": "旧权4并入现权2", "feature_before_text": "", "feature_after_text": "旧权4并入现权2", "target_claim_ids": ["2"], "source_type": "claim", "source_claim_ids": ["4"]},
            {"feature_id": "F4", "feature_text": "旧权5并入现权3", "feature_before_text": "", "feature_after_text": "旧权5并入现权3", "target_claim_ids": ["3"], "source_type": "claim", "source_claim_ids": ["5"]},
            {"feature_id": "F5", "feature_text": "旧权6并入现权4", "feature_before_text": "", "feature_after_text": "旧权6并入现权4", "target_claim_ids": ["4"], "source_type": "claim", "source_claim_ids": ["6"]},
            {"feature_id": "F6", "feature_text": "旧权7并入现权5", "feature_before_text": "", "feature_after_text": "旧权7并入现权5", "target_claim_ids": ["5"], "source_type": "claim", "source_claim_ids": ["7"]},
            {"feature_id": "F7", "feature_text": "旧权8并入现权6", "feature_before_text": "", "feature_after_text": "旧权8并入现权6", "target_claim_ids": ["6"], "source_type": "claim", "source_claim_ids": ["8"]},
            {"feature_id": "F8", "feature_text": "旧权9并入现权7", "feature_before_text": "", "feature_after_text": "旧权9并入现权7", "target_claim_ids": ["7"], "source_type": "claim", "source_claim_ids": ["9"]},
            {"feature_id": "F9", "feature_text": "旧权10并入现权8", "feature_before_text": "", "feature_after_text": "旧权10并入现权8", "target_claim_ids": ["8"], "source_type": "claim", "source_claim_ids": ["10"]},
        ],
        disputes=[],
        evidence_assessments=[],
        drafted_rejection_reasons={},
    )

    assert [item["anchor_claim_id"] for item in result] == ["1", "2", "3", "4", "5", "6", "7", "8"]
    assert [item["unit_id"] for item in result] == ["P1", "P4", "P5", "P6", "P7", "P8", "P9", "P10"]
    assert result[0]["source_paragraph_ids"] == ["P1", "P2", "P3"]
    assert result[0]["source_summary"]["merged_source_claim_ids"] == ["2", "3"]
    assert result[0]["review_before_text"] == "OA 对旧权1的原文评述\nOA 对旧权2的原文评述\nOA 对旧权3的原文评述"
    assert result[0]["claim_snapshots"] == [
        {"claim_id": "1", "claim_before_text": "旧权1", "claim_text": "现权1", "claim_type": "independent"},
    ]

    for index, source_claim_id in enumerate(["4", "5", "6", "7", "8"], start=1):
        unit = result[index]
        paragraph_id = f"P{index + 3}"
        assert unit["source_paragraph_ids"] == [paragraph_id]
        assert unit["review_before_text"] == f"OA 对旧权{source_claim_id}的原文评述"
        assert unit["claim_snapshots"][0]["claim_before_text"] == f"旧权{source_claim_id}"

    assert result[6]["source_paragraph_ids"] == ["P9"]
    assert result[6]["review_before_text"] == "OA 对旧权9的原文评述"
    assert result[6]["claim_snapshots"][0]["claim_before_text"] == "旧权9"
    assert result[7]["source_paragraph_ids"] == ["P10"]
    assert result[7]["review_before_text"] == "OA 对旧权10的原文评述"
    assert result[7]["claim_snapshots"][0]["claim_before_text"] == "旧权10"


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
