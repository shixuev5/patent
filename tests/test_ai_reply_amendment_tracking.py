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
