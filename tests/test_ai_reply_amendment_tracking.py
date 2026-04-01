from agents.ai_reply.src.nodes.amendment_tracking import AmendmentTrackingNode


def test_build_structured_diff_ignores_pure_reindex_shift() -> None:
    node = AmendmentTrackingNode()
    old_claims = [
        {"claim_id": "1", "claim_text": "一种装置，包括模块A。"},
        {"claim_id": "2", "claim_text": "根据权利要求1所述的装置，还包括模块B。"},
        {"claim_id": "3", "claim_text": "根据权利要求1所述的装置，还包括模块C。"},
    ]
    new_claims = [
        {"claim_id": "1", "claim_text": "一种装置，包括模块A。"},
        {"claim_id": "2", "claim_text": "根据权利要求1所述的装置，还包括模块C。"},
    ]

    structured_diff = node._build_structured_diff(old_claims, new_claims)

    assert structured_diff["has_changes"] is False
    assert structured_diff["changed_claim_ids"] == []
    assert structured_diff["changed_claims_pairs"] == []


def test_build_structured_diff_returns_full_changed_claim_pairs() -> None:
    node = AmendmentTrackingNode()
    old_claims = [
        {"claim_id": "1", "claim_text": "一种连接器，包括壳体和滑块。"},
        {"claim_id": "2", "claim_text": "根据权利要求1所述的连接器，还包括弹性件。"},
    ]
    new_claims = [
        {"claim_id": "1", "claim_text": "一种连接器，包括壳体和滑块，所述壳体内设有第一弹簧。"},
        {"claim_id": "2", "claim_text": "根据权利要求1所述的连接器，还包括弹性件。"},
    ]

    structured_diff = node._build_structured_diff(old_claims, new_claims)

    assert structured_diff["has_changes"] is True
    assert structured_diff["changed_claim_ids"] == ["1"]
    assert structured_diff["changed_claims_pairs"] == [
        {
            "claim_id": "1",
            "old_text": "一种连接器，包括壳体和滑块。",
            "new_text": "一种连接器，包括壳体和滑块，所述壳体内设有第一弹簧。",
        }
    ]
    assert structured_diff["full_old_claims_context"] == {
        "1": "一种连接器，包括壳体和滑块。",
        "2": "根据权利要求1所述的连接器，还包括弹性件。",
    }


def test_normalize_tracking_result_supports_feature_diff_fields_and_legacy_defaults() -> None:
    node = AmendmentTrackingNode()

    normalized = node._normalize_tracking_result(
        {
            "has_claim_amendment": True,
            "added_features": [
                {
                    "feature_id": "F1",
                    "feature_text": "第一弹簧与壳体连接",
                    "feature_before_text": "弹簧与壳体连接",
                    "feature_after_text": "第一弹簧与壳体连接",
                    "target_claim_ids": ["1"],
                    "source_type": "spec",
                    "source_claim_ids": [],
                },
                {
                    "feature_id": "F2",
                    "feature_text": "第二弹簧与滑块连接",
                    "target_claim_ids": ["2"],
                    "source_type": "claim",
                    "source_claim_ids": ["5"],
                },
            ],
        }
    )

    assert normalized["added_features"][0]["feature_before_text"] == "弹簧与壳体连接"
    assert normalized["added_features"][0]["feature_after_text"] == "第一弹簧与壳体连接"
    assert normalized["added_features"][1]["feature_before_text"] == ""
    assert normalized["added_features"][1]["feature_after_text"] == "第二弹簧与滑块连接"


def test_track_amendment_uses_original_claims_for_first_notice() -> None:
    node = AmendmentTrackingNode()
    prepared_materials = {
        "original_patent": {
            "data": {
                "claims": [
                    {"claim_id": "1", "claim_text": "一种装置，包括模块A。"},
                ]
            }
        },
        "office_action": {"current_notice_round": 1},
    }

    result = node._track_amendment(prepared_materials, previous_claims=[], current_claims=[])

    assert result["has_claim_amendment"] is False
    assert result["claims_old_structured"] == [
        {
            "claim_id": "1",
            "claim_text": "一种装置，包括模块A。",
            "claim_type": "unknown",
            "parent_claim_ids": [],
        }
    ]
    assert result["claims_effective_structured"] == result["claims_old_structured"]
    assert result["claims_old_source"] == "original_patent"
    assert result["claims_old_source_reason"] == "first_notice_or_missing_previous"


def test_track_amendment_uses_previous_claims_for_multi_notice() -> None:
    node = AmendmentTrackingNode()
    prepared_materials = {
        "original_patent": {
            "data": {
                "claims": [
                    {"claim_id": "1", "claim_text": "一种装置，包括模块A。"},
                ]
            }
        },
        "office_action": {"current_notice_round": 2},
    }
    previous_claims = [
        {"claim_id": "1", "claim_text": "一种装置，包括模块A和模块B。", "claim_type": "independent", "parent_claim_ids": []}
    ]

    result = node._track_amendment(prepared_materials, previous_claims=previous_claims, current_claims=[])

    assert result["has_claim_amendment"] is False
    assert result["claims_old_structured"] == previous_claims
    assert result["claims_effective_structured"] == previous_claims
    assert result["claims_old_source"] == "claims_previous"
    assert result["claims_old_source_reason"] == "multi_notice_previous_claims"


def test_track_amendment_falls_back_to_original_when_previous_missing_for_multi_notice() -> None:
    node = AmendmentTrackingNode()
    node.llm_service = type(
        "StubLLM",
        (),
        {
            "invoke_text_json": staticmethod(
                lambda messages, task_kind, temperature: {
                    "has_claim_amendment": True,
                    "added_features": [
                        {
                            "feature_id": "F1",
                            "feature_text": "模块C",
                            "target_claim_ids": ["1"],
                            "source_type": "spec",
                            "source_claim_ids": [],
                        }
                    ],
                }
            )
        },
    )()
    prepared_materials = {
        "original_patent": {
            "data": {
                "claims": [
                    {"claim_id": "1", "claim_text": "一种装置，包括模块A。"},
                ]
            }
        },
        "office_action": {"current_notice_round": 3},
    }
    current_claims = [
        {"claim_id": "1", "claim_text": "一种装置，包括模块A和模块C。", "claim_type": "independent", "parent_claim_ids": []}
    ]

    result = node._track_amendment(prepared_materials, previous_claims=[], current_claims=current_claims)

    assert result["claims_old_structured"][0]["claim_text"] == "一种装置，包括模块A。"
    assert result["claims_effective_structured"] == current_claims
    assert result["has_claim_amendment"] is True
    assert result["claims_old_source"] == "original_patent"
    assert result["claims_old_source_reason"] == "multi_notice_missing_previous_claims"
