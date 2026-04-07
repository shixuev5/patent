from agents.ai_reply.src.nodes.amendment_strategy import AmendmentStrategyNode


def test_build_strategy_preserves_summary_and_search_text_roles() -> None:
    node = AmendmentStrategyNode()

    result = node._build_strategy(
        has_claim_amendment=True,
        substantive_amendments=[
            {
                "amendment_id": "A1",
                "feature_text": "基于轮胎信息控制车辆加速度",
                "search_feature_text": "基于轮胎的RRC值控制车辆的目标加速度",
                "amendment_kind": "spec_feature_addition",
                "target_claim_ids": ["1"],
                "source_claim_ids": [],
            }
        ],
        prepared_materials={"office_action": {"paragraphs": []}},
    )

    assert result["reuse_oa_tasks"] == []
    assert result["topup_tasks"] == [
        {
            "task_id": "A1",
            "claim_ids": ["1"],
            "feature_text": "基于轮胎信息控制车辆加速度",
            "search_feature_text": "基于轮胎的RRC值控制车辆的目标加速度",
            "amendment_kind": "spec_feature_addition",
            "source_claim_ids": [],
            "target_claim_ids": ["1"],
        }
    ]
