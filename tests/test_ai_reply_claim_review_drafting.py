import json

import pytest

from agents.ai_reply.src.nodes.claim_review_drafting import ClaimReviewDraftingNode
from agents.ai_reply.src.nodes.topup_search_verification import TopupSearchVerificationNode


def test_claim_review_drafting_aggregates_independent_card_and_residual_group(monkeypatch) -> None:
    node = ClaimReviewDraftingNode()
    seen_unit_ids = []

    def _fake_invoke_text_json(messages, task_kind, temperature):
        assert task_kind == "oar_claim_review_drafting"
        user_prompt = messages[1]["content"]
        payload = messages[1]["content"].split("=== 待处理的评述单元素材 ===\n", 1)[1]
        item = json.loads(payload)
        unit_id = item["unit_id"]
        seen_unit_ids.append(unit_id)
        assert '"unit_id": "P1"' not in user_prompt or unit_id == "P1"
        assert '"unit_id": "P2"' not in user_prompt or unit_id == "P2"
        assert '"unit_id": "MERGED_1"' not in user_prompt
        assert '"unit_id": "NEW_F1"' not in user_prompt
        assert isinstance(item["display_claim_ids"], list)
        if unit_id == "P1":
            assert item["unit_type"] == "evidence_restructured"
            assert item["display_claim_ids"] == ["1"]
            assert item["review_before_text"] == "OA 对权利要求1的原文评述\nOA 对权利要求2-4的组合评述"
            assert any(material["feature_text"] == "将旧权2并入权1的新增特征" for material in item["amendment_materials"])
            assert any(material["feature_text"] == "权利要求1说明书新增特征" for material in item["amendment_materials"])
            return {"unit_id": "P1", "review_text": "权利要求1聚合后的重组评述"}

        assert unit_id == "P2"
        assert item["unit_type"] == "dependent_group_restructured"
        assert item["display_claim_ids"] == ["3", "4"]
        return {"unit_id": "P2", "review_text": "权利要求3-4残余组合评述"}

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
        substantive_amendments=[
            {
                "amendment_id": "F2",
                "feature_text": "将旧权2并入权1的新增特征",
                "feature_before_text": "",
                "feature_after_text": "将旧权2并入权1的新增特征",
                "target_claim_ids": ["1"],
                "amendment_kind": "claim_feature_merge",
                "content_origin": "old_claim",
                "source_claim_ids": ["2"],
            },
            {
                "amendment_id": "F1",
                "feature_text": "权利要求1说明书新增特征",
                "feature_before_text": "",
                "feature_after_text": "权利要求1说明书新增特征",
                "target_claim_ids": ["1"],
                "amendment_kind": "spec_feature_addition",
                "content_origin": "specification",
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
    assert seen_unit_ids == ["P1", "P2"]
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
        "merged_source_claim_ids": ["2"],
        "amendment_ids": ["F2", "F1"],
        "response_dispute_ids": ["DSP_1"],
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
        "merged_source_claim_ids": [],
        "amendment_ids": [],
        "response_dispute_ids": [],
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
        substantive_amendments=[],
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
        payload = messages[1]["content"].split("=== 待处理的评述单元素材 ===\n", 1)[1]
        item = json.loads(payload)
        assert '"unit_id": "IND_1"' in user_prompt
        assert item["unit_type"] == "supplemented_new"
        assert item["source_paragraph_ids"] == []
        assert item["amendment_materials"][0]["feature_text"] == "权利要求1新增特征"
        return {"unit_id": "IND_1", "review_text": "权利要求1无主段落时的补充评述"}

    monkeypatch.setattr(node.llm_service, "invoke_text_json", _fake_invoke_text_json)

    result = node._draft_review_units(
        claims_old_structured=[],
        claims_effective_structured=[
            {"claim_id": "1", "claim_text": "新权1", "claim_type": "independent", "parent_claim_ids": []},
        ],
        prepared_materials={"office_action": {"paragraphs": []}},
        substantive_amendments=[
            {
                "amendment_id": "F1",
                "feature_text": "权利要求1新增特征",
                "feature_before_text": "",
                "feature_after_text": "权利要求1新增特征",
                "target_claim_ids": ["1"],
                "amendment_kind": "spec_feature_addition",
                "content_origin": "specification",
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
                "merged_source_claim_ids": [],
                "amendment_ids": ["F1"],
                "response_dispute_ids": [],
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
        substantive_amendments=[],
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
                "merged_source_claim_ids": [],
                "amendment_ids": [],
                "response_dispute_ids": [],
            },
        }
    ]


def test_claim_review_drafting_reorders_renumbered_claims_by_effective_claim_order(monkeypatch) -> None:
    node = ClaimReviewDraftingNode()
    seen_unit_ids = []

    def _fake_invoke_text_json(messages, task_kind, temperature):
        assert task_kind == "oar_claim_review_drafting"
        payload = messages[1]["content"].split("=== 待处理的评述单元素材 ===\n", 1)[1]
        item = json.loads(payload)
        seen_unit_ids.append(item["unit_id"])
        return {"unit_id": item["unit_id"], "review_text": f"现权{item['anchor_claim_id']}重组评述"}

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
        substantive_amendments=[
            {"amendment_id": "F1", "feature_text": "旧权2并入现权1", "feature_before_text": "", "feature_after_text": "旧权2并入现权1", "target_claim_ids": ["1"], "amendment_kind": "claim_feature_merge", "content_origin": "old_claim", "source_claim_ids": ["2"]},
            {"amendment_id": "F2", "feature_text": "旧权3并入现权1", "feature_before_text": "", "feature_after_text": "旧权3并入现权1", "target_claim_ids": ["1"], "amendment_kind": "claim_feature_merge", "content_origin": "old_claim", "source_claim_ids": ["3"]},
            {"amendment_id": "F3", "feature_text": "旧权4并入现权2", "feature_before_text": "", "feature_after_text": "旧权4并入现权2", "target_claim_ids": ["2"], "amendment_kind": "claim_feature_merge", "content_origin": "old_claim", "source_claim_ids": ["4"]},
            {"amendment_id": "F4", "feature_text": "旧权5并入现权3", "feature_before_text": "", "feature_after_text": "旧权5并入现权3", "target_claim_ids": ["3"], "amendment_kind": "claim_feature_merge", "content_origin": "old_claim", "source_claim_ids": ["5"]},
            {"amendment_id": "F5", "feature_text": "旧权6并入现权4", "feature_before_text": "", "feature_after_text": "旧权6并入现权4", "target_claim_ids": ["4"], "amendment_kind": "claim_feature_merge", "content_origin": "old_claim", "source_claim_ids": ["6"]},
            {"amendment_id": "F6", "feature_text": "旧权7并入现权5", "feature_before_text": "", "feature_after_text": "旧权7并入现权5", "target_claim_ids": ["5"], "amendment_kind": "claim_feature_merge", "content_origin": "old_claim", "source_claim_ids": ["7"]},
            {"amendment_id": "F7", "feature_text": "旧权8并入现权6", "feature_before_text": "", "feature_after_text": "旧权8并入现权6", "target_claim_ids": ["6"], "amendment_kind": "claim_feature_merge", "content_origin": "old_claim", "source_claim_ids": ["8"]},
            {"amendment_id": "F8", "feature_text": "旧权9并入现权7", "feature_before_text": "", "feature_after_text": "旧权9并入现权7", "target_claim_ids": ["7"], "amendment_kind": "claim_feature_merge", "content_origin": "old_claim", "source_claim_ids": ["9"]},
            {"amendment_id": "F9", "feature_text": "旧权10并入现权8", "feature_before_text": "", "feature_after_text": "旧权10并入现权8", "target_claim_ids": ["8"], "amendment_kind": "claim_feature_merge", "content_origin": "old_claim", "source_claim_ids": ["10"]},
        ],
        disputes=[],
        evidence_assessments=[],
        drafted_rejection_reasons={},
    )

    assert [item["anchor_claim_id"] for item in result] == ["1", "2", "3", "4", "5", "6", "7", "8"]
    assert [item["unit_id"] for item in result] == ["P1", "P4", "P5", "P6", "P7", "P8", "P9", "P10"]
    assert seen_unit_ids[0] == "P1"
    assert sorted(seen_unit_ids[1:]) == ["P10", "P4", "P5", "P6", "P7", "P8", "P9"]
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


def test_claim_review_drafting_uses_alignment_map_for_pure_renumbered_residual_units(monkeypatch) -> None:
    node = ClaimReviewDraftingNode()

    def _fake_invoke_text_json(messages, task_kind, temperature):
        assert task_kind == "oar_claim_review_drafting"
        payload = messages[1]["content"].split("=== 待处理的评述单元素材 ===\n", 1)[1]
        item = json.loads(payload)
        return {
            "unit_id": item["unit_id"],
            "review_text": f"重组评述::{item['unit_id']}::{','.join(item.get('display_claim_ids', []))}",
        }

    monkeypatch.setattr(node.llm_service, "invoke_text_json", _fake_invoke_text_json)

    result = node._draft_review_units(
        claims_old_structured=[
            {"claim_id": "1", "claim_text": "旧权1", "claim_type": "independent", "parent_claim_ids": []},
            {"claim_id": "2", "claim_text": "旧权2", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "3", "claim_text": "旧权3", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "4", "claim_text": "旧权4", "claim_type": "dependent", "parent_claim_ids": ["3"]},
            {"claim_id": "5", "claim_text": "旧权5", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "6", "claim_text": "旧权6", "claim_type": "dependent", "parent_claim_ids": ["5"]},
            {"claim_id": "7", "claim_text": "旧权7", "claim_type": "dependent", "parent_claim_ids": ["2"]},
            {"claim_id": "8", "claim_text": "旧权8", "claim_type": "dependent", "parent_claim_ids": ["3"]},
            {"claim_id": "9", "claim_text": "旧权9", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "10", "claim_text": "旧权10", "claim_type": "independent", "parent_claim_ids": []},
        ],
        claims_effective_structured=[
            {"claim_id": "1", "claim_text": "现权1", "claim_type": "independent", "parent_claim_ids": []},
            {"claim_id": "2", "claim_text": "现权2", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "3", "claim_text": "现权3", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "4", "claim_text": "现权4", "claim_type": "dependent", "parent_claim_ids": ["3"]},
            {"claim_id": "5", "claim_text": "现权5", "claim_type": "dependent", "parent_claim_ids": ["4"]},
            {"claim_id": "6", "claim_text": "现权6", "claim_type": "dependent", "parent_claim_ids": ["2"]},
            {"claim_id": "7", "claim_text": "现权7", "claim_type": "dependent", "parent_claim_ids": ["3"]},
            {"claim_id": "8", "claim_text": "现权8", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "9", "claim_text": "现权9", "claim_type": "independent", "parent_claim_ids": []},
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
        substantive_amendments=[
            {
                "amendment_id": "F1",
                "feature_text": "旧权5并入现权1",
                "feature_before_text": "",
                "feature_after_text": "旧权5并入现权1",
                "target_claim_ids": ["1"],
                "amendment_kind": "claim_feature_merge",
                "content_origin": "old_claim",
                "source_claim_ids": ["5"],
            },
        ],
        disputes=[],
        evidence_assessments=[],
        drafted_rejection_reasons={},
        claim_alignments=[
            {"claim_id": "1", "old_claim_id": "1", "alignment_kind": "same_number_match", "reason": "unchanged"},
            {"claim_id": "2", "old_claim_id": "2", "alignment_kind": "same_number_match", "reason": "unchanged"},
            {"claim_id": "3", "old_claim_id": "3", "alignment_kind": "same_number_match", "reason": "unchanged"},
            {"claim_id": "4", "old_claim_id": "4", "alignment_kind": "same_number_match", "reason": "unchanged"},
            {"claim_id": "5", "old_claim_id": "6", "alignment_kind": "renumbered_successor", "reason": "upstream_merged"},
            {"claim_id": "6", "old_claim_id": "7", "alignment_kind": "renumbered_successor", "reason": "upstream_merged"},
            {"claim_id": "7", "old_claim_id": "8", "alignment_kind": "renumbered_successor", "reason": "upstream_merged"},
            {"claim_id": "8", "old_claim_id": "9", "alignment_kind": "renumbered_successor", "reason": "upstream_merged"},
            {"claim_id": "9", "old_claim_id": "10", "alignment_kind": "renumbered_successor", "reason": "upstream_merged"},
        ],
    )

    assert [item["anchor_claim_id"] for item in result] == ["1", "2", "3", "4", "5", "6", "7", "8", "9"]
    assert [item["display_claim_ids"] for item in result] == [
        ["1"], ["2"], ["3"], ["4"], ["5"], ["6"], ["7"], ["8"], ["9"]
    ]

    assert result[0]["source_paragraph_ids"] == ["P1", "P5"]
    assert result[0]["source_summary"]["merged_source_claim_ids"] == ["5"]
    assert result[0]["claim_snapshots"] == [
        {"claim_id": "1", "claim_before_text": "旧权1", "claim_text": "现权1", "claim_type": "independent"},
    ]

    assert result[4]["source_paragraph_ids"] == ["P6"]
    assert result[4]["source_summary"]["merged_source_claim_ids"] == []
    assert result[4]["claim_snapshots"] == [
        {"claim_id": "5", "claim_before_text": "旧权6", "claim_text": "现权5", "claim_type": "dependent"},
    ]

    assert result[5]["source_paragraph_ids"] == ["P7"]
    assert result[5]["claim_snapshots"] == [
        {"claim_id": "6", "claim_before_text": "旧权7", "claim_text": "现权6", "claim_type": "dependent"},
    ]
    assert result[8]["source_paragraph_ids"] == ["P10"]
    assert result[8]["claim_snapshots"] == [
        {"claim_id": "9", "claim_before_text": "旧权10", "claim_text": "现权9", "claim_type": "independent"},
    ]


def test_claim_review_drafting_prefers_unique_renumbered_alignment_for_grouped_residual_units(monkeypatch) -> None:
    node = ClaimReviewDraftingNode()

    def _fake_invoke_text_json(messages, task_kind, temperature):
        assert task_kind == "oar_claim_review_drafting"
        payload = messages[1]["content"].split("=== 待处理的评述单元素材 ===\n", 1)[1]
        item = json.loads(payload)
        return {
            "unit_id": item["unit_id"],
            "review_text": f"重组评述::{item['unit_id']}::{','.join(item.get('display_claim_ids', []))}",
        }

    monkeypatch.setattr(node.llm_service, "invoke_text_json", _fake_invoke_text_json)

    result = node._draft_review_units(
        claims_old_structured=[
            {"claim_id": "7", "claim_text": "旧权7", "claim_type": "dependent", "parent_claim_ids": ["2"]},
            {"claim_id": "8", "claim_text": "旧权8", "claim_type": "dependent", "parent_claim_ids": ["5"]},
            {"claim_id": "9", "claim_text": "旧权9", "claim_type": "dependent", "parent_claim_ids": ["8"]},
            {"claim_id": "10", "claim_text": "旧权10", "claim_type": "dependent", "parent_claim_ids": ["1", "2", "3", "4", "5", "6", "7", "8", "9"]},
        ],
        claims_effective_structured=[
            {"claim_id": "6", "claim_text": "现权6", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "7", "claim_text": "现权7", "claim_type": "dependent", "parent_claim_ids": ["4"]},
            {"claim_id": "8", "claim_text": "现权8", "claim_type": "dependent", "parent_claim_ids": ["7"]},
            {"claim_id": "9", "claim_text": "现权9", "claim_type": "dependent", "parent_claim_ids": ["1", "2", "3", "4", "5", "6", "7", "8"]},
        ],
        prepared_materials={
            "office_action": {
                "paragraphs": [
                    {"paragraph_id": "Claim4", "claim_ids": ["7"], "content": "权利要求 7 是从属权利要求。"},
                    {"paragraph_id": "Claim5", "claim_ids": ["8", "9"], "content": "权利要求 8-9 是从属权利要求。"},
                    {"paragraph_id": "Claim6", "claim_ids": ["10"], "content": "权利要求 10 是从属权利要求。"},
                ]
            }
        },
        substantive_amendments=[
            {
                "amendment_id": "A3",
                "feature_text": "测试系统包括光源、第一光功率计、第二光功率计和第三光功率计",
                "feature_before_text": "旧权10",
                "feature_after_text": "现权9",
                "target_claim_ids": ["9"],
                "amendment_kind": "claim_feature_merge",
                "content_origin": "old_claim",
                "source_claim_ids": ["10"],
            },
        ],
        disputes=[],
        evidence_assessments=[],
        drafted_rejection_reasons={},
        claim_alignments=[
            {"claim_id": "6", "old_claim_id": "7", "alignment_kind": "renumbered_successor", "reason": "upstream_merged"},
            {"claim_id": "7", "old_claim_id": "8", "alignment_kind": "renumbered_successor", "reason": "upstream_merged"},
            {"claim_id": "8", "old_claim_id": "9", "alignment_kind": "renumbered_successor", "reason": "upstream_merged"},
            {"claim_id": "9", "old_claim_id": "9", "alignment_kind": "same_number_match", "reason": "upstream_merged"},
        ],
    )

    assert [item["unit_id"] for item in result] == ["Claim4", "Claim5", "Claim6"]
    assert result[0]["display_claim_ids"] == ["6"]
    assert result[0]["claim_snapshots"][0]["claim_before_text"] == "旧权7"
    assert result[1]["display_claim_ids"] == ["7", "8"]
    assert result[1]["review_before_text"] == "权利要求 8-9 是从属权利要求。"
    assert result[1]["claim_snapshots"] == [
        {"claim_id": "7", "claim_before_text": "旧权8", "claim_text": "现权7", "claim_type": "dependent"},
        {"claim_id": "8", "claim_before_text": "旧权9", "claim_text": "现权8", "claim_type": "dependent"},
    ]
    assert result[2]["display_claim_ids"] == ["9"]
    assert result[2]["source_summary"]["merged_source_claim_ids"] == ["10"]
    assert result[2]["claim_snapshots"][0]["claim_before_text"] == "旧权10"


def test_claim_review_drafting_keeps_pure_renumbered_grouped_paragraphs_grouped(monkeypatch) -> None:
    node = ClaimReviewDraftingNode()

    def _fake_invoke_text_json(messages, task_kind, temperature):
        assert task_kind == "oar_claim_review_drafting"
        payload = messages[1]["content"].split("=== 待处理的评述单元素材 ===\n", 1)[1]
        item = json.loads(payload)
        return {
            "unit_id": item["unit_id"],
            "review_text": f"重组评述::{item['unit_id']}::{','.join(item.get('display_claim_ids', []))}",
        }

    monkeypatch.setattr(node.llm_service, "invoke_text_json", _fake_invoke_text_json)

    result = node._draft_review_units(
        claims_old_structured=[
            {"claim_id": "1", "claim_text": "旧权1", "claim_type": "independent", "parent_claim_ids": []},
            {"claim_id": "2", "claim_text": "旧权2", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "3", "claim_text": "旧权3", "claim_type": "dependent", "parent_claim_ids": ["2"]},
            {"claim_id": "4", "claim_text": "旧权4", "claim_type": "dependent", "parent_claim_ids": ["2"]},
            {"claim_id": "5", "claim_text": "旧权5", "claim_type": "dependent", "parent_claim_ids": ["2"]},
            {"claim_id": "6", "claim_text": "旧权6", "claim_type": "dependent", "parent_claim_ids": ["5"]},
            {"claim_id": "7", "claim_text": "旧权7", "claim_type": "dependent", "parent_claim_ids": ["2"]},
            {"claim_id": "8", "claim_text": "旧权8", "claim_type": "dependent", "parent_claim_ids": ["5"]},
            {"claim_id": "9", "claim_text": "旧权9", "claim_type": "dependent", "parent_claim_ids": ["8"]},
        ],
        claims_effective_structured=[
            {"claim_id": "1", "claim_text": "现权1", "claim_type": "independent", "parent_claim_ids": []},
            {"claim_id": "2", "claim_text": "现权2", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "3", "claim_text": "现权3", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "4", "claim_text": "现权4", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "5", "claim_text": "现权5", "claim_type": "dependent", "parent_claim_ids": ["4"]},
            {"claim_id": "6", "claim_text": "现权6", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            {"claim_id": "7", "claim_text": "现权7", "claim_type": "dependent", "parent_claim_ids": ["4"]},
            {"claim_id": "8", "claim_text": "现权8", "claim_type": "dependent", "parent_claim_ids": ["7"]},
        ],
        prepared_materials={
            "office_action": {
                "paragraphs": [
                    {"paragraph_id": "Claim3", "claim_ids": ["3", "4", "5", "6"], "content": "权利要求 3-6 是从属权利要求。"},
                    {"paragraph_id": "Claim4", "claim_ids": ["7"], "content": "权利要求 7 是从属权利要求。"},
                    {"paragraph_id": "Claim5", "claim_ids": ["8", "9"], "content": "权利要求 8-9 是从属权利要求。"},
                ]
            }
        },
        substantive_amendments=[],
        disputes=[],
        evidence_assessments=[],
        drafted_rejection_reasons={},
        claim_alignments=[
            {"claim_id": "1", "old_claim_id": "1", "alignment_kind": "same_number_match", "reason": "unchanged"},
            {"claim_id": "2", "old_claim_id": "3", "alignment_kind": "renumbered_successor", "reason": "upstream_merged"},
            {"claim_id": "3", "old_claim_id": "4", "alignment_kind": "renumbered_successor", "reason": "upstream_merged"},
            {"claim_id": "4", "old_claim_id": "5", "alignment_kind": "renumbered_successor", "reason": "upstream_merged"},
            {"claim_id": "5", "old_claim_id": "6", "alignment_kind": "renumbered_successor", "reason": "upstream_merged"},
            {"claim_id": "6", "old_claim_id": "7", "alignment_kind": "renumbered_successor", "reason": "upstream_merged"},
            {"claim_id": "7", "old_claim_id": "8", "alignment_kind": "renumbered_successor", "reason": "upstream_merged"},
            {"claim_id": "8", "old_claim_id": "9", "alignment_kind": "renumbered_successor", "reason": "upstream_merged"},
        ],
    )

    claim3_unit = next(item for item in result if item["unit_id"] == "Claim3")
    assert claim3_unit["display_claim_ids"] == ["2", "3", "4", "5"]
    assert claim3_unit["source_paragraph_ids"] == ["Claim3"]
    assert claim3_unit["claim_snapshots"] == [
        {"claim_id": "2", "claim_before_text": "旧权3", "claim_text": "现权2", "claim_type": "dependent"},
        {"claim_id": "3", "claim_before_text": "旧权4", "claim_text": "现权3", "claim_type": "dependent"},
        {"claim_id": "4", "claim_before_text": "旧权5", "claim_text": "现权4", "claim_type": "dependent"},
        {"claim_id": "5", "claim_before_text": "旧权6", "claim_text": "现权5", "claim_type": "dependent"},
    ]

    claim5_unit = next(item for item in result if item["unit_id"] == "Claim5")
    assert claim5_unit["display_claim_ids"] == ["7", "8"]
    assert claim5_unit["source_paragraph_ids"] == ["Claim5"]
    assert claim5_unit["claim_snapshots"] == [
        {"claim_id": "7", "claim_before_text": "旧权8", "claim_text": "现权7", "claim_type": "dependent"},
        {"claim_id": "8", "claim_before_text": "旧权9", "claim_text": "现权8", "claim_type": "dependent"},
    ]


def test_claim_review_drafting_prompt_requires_preserving_existing_detail() -> None:
    node = ClaimReviewDraftingNode()

    system_prompt = node._build_single_system_prompt()
    user_prompt = node._build_single_user_prompt(
        {
            "unit_id": "P1",
            "unit_type": "evidence_restructured",
            "title": "权利要求1",
            "source_paragraph_ids": ["P1"],
            "display_claim_ids": ["1"],
            "anchor_claim_id": "1",
            "source_summary": {"merged_source_claim_ids": []},
            "review_before_text": "旧评述全文",
            "claim_snapshots": [],
            "oa_materials": [{"paragraph_id": "P1", "content": "旧评述全文"}],
            "response_materials": [],
            "amendment_materials": [],
        }
    )

    assert "最小编辑与保真原则（反压缩机制）" in system_prompt
    assert "自动摘要与压缩" in system_prompt
    assert "原样保留" in system_prompt
    assert "无实质变化时原文复用优先" in system_prompt
    assert "不得为了措辞统一而整体改写" in system_prompt
    assert "必须完整保留当前仍然成立的事实、对比文件公开内容、区别特征分析及结论" in system_prompt
    assert "必须返回输入中的同一个 unit_id" in system_prompt
    assert "review_text 应尽量直接沿用 review_before_text" in user_prompt
    assert "只能返回该 unit_id 对应的单个 JSON 对象" in user_prompt


def test_claim_review_drafting_dedupes_review_units_by_unit_id_and_claims() -> None:
    node = ClaimReviewDraftingNode()

    units = [
        {
            "unit_id": "Claim3",
            "display_claim_ids": ["5"],
            "title": "权利要求5",
            "review_text": "第一次",
        },
        {
            "unit_id": "Claim3",
            "display_claim_ids": ["5"],
            "title": "权利要求5",
            "review_text": "重复项",
        },
        {
            "unit_id": "Claim4",
            "display_claim_ids": ["6"],
            "title": "权利要求6",
            "review_text": "保留项",
        },
    ]

    result = node._dedupe_review_units(units)

    assert [item["unit_id"] for item in result] == ["Claim3", "Claim4"]
    assert [item["display_claim_ids"] for item in result] == [["5"], ["6"]]
    assert result[0]["review_text"] == "第一次"


def test_claim_review_drafting_raises_on_mismatched_unit_id(monkeypatch) -> None:
    node = ClaimReviewDraftingNode()

    monkeypatch.setattr(
        node.llm_service,
        "invoke_text_json",
        lambda *args, **kwargs: {"unit_id": "WRONG", "review_text": "错误结果"},
    )

    with pytest.raises(ValueError, match="unit_id=WRONG"):
        node._draft_single_review_unit(
            {
                "unit_id": "P1",
                "unit_type": "evidence_restructured",
                "title": "权利要求1",
                "source_paragraph_ids": ["P1"],
                "display_claim_ids": ["1"],
                "anchor_claim_id": "1",
                "source_summary": {"merged_source_claim_ids": []},
                "review_before_text": "旧评述全文",
                "claim_snapshots": [],
                "oa_materials": [{"paragraph_id": "P1", "content": "旧评述全文"}],
                "response_materials": [],
                "amendment_materials": [],
            }
        )


def test_claim_review_drafting_raises_on_missing_review_text(monkeypatch) -> None:
    node = ClaimReviewDraftingNode()

    monkeypatch.setattr(
        node.llm_service,
        "invoke_text_json",
        lambda *args, **kwargs: {"unit_id": "P1"},
    )

    with pytest.raises(ValueError, match="缺少 review_text"):
        node._draft_single_review_unit(
            {
                "unit_id": "P1",
                "unit_type": "evidence_restructured",
                "title": "权利要求1",
                "source_paragraph_ids": ["P1"],
                "display_claim_ids": ["1"],
                "anchor_claim_id": "1",
                "source_summary": {"merged_source_claim_ids": []},
                "review_before_text": "旧评述全文",
                "claim_snapshots": [],
                "oa_materials": [{"paragraph_id": "P1", "content": "旧评述全文"}],
                "response_materials": [],
                "amendment_materials": [],
            }
        )


def test_claim_review_drafting_raises_on_legacy_items_array(monkeypatch) -> None:
    node = ClaimReviewDraftingNode()

    monkeypatch.setattr(
        node.llm_service,
        "invoke_text_json",
        lambda *args, **kwargs: {"items": [{"unit_id": "P1", "review_text": "旧协议"}]},
    )

    with pytest.raises(ValueError, match="items 数组"):
        node._draft_single_review_unit(
            {
                "unit_id": "P1",
                "unit_type": "evidence_restructured",
                "title": "权利要求1",
                "source_paragraph_ids": ["P1"],
                "display_claim_ids": ["1"],
                "anchor_claim_id": "1",
                "source_summary": {"merged_source_claim_ids": []},
                "review_before_text": "旧评述全文",
                "claim_snapshots": [],
                "oa_materials": [{"paragraph_id": "P1", "content": "旧评述全文"}],
                "response_materials": [],
                "amendment_materials": [],
            }
        )


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
