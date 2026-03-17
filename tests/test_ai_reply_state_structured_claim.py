from agents.ai_reply.src.state import StructuredClaim


def test_structured_claim_accepts_parent_claim_ids() -> None:
    claim = StructuredClaim(
        claim_id="4",
        claim_text="根据权利要求1至3任一项所述的系统，其特征在于，还包括模块D。",
        claim_type="dependent",
        parent_claim_ids=["1", "2", "3"],
    )
    assert claim.parent_claim_ids == ["1", "2", "3"]


def test_structured_claim_parent_claim_ids_default_empty() -> None:
    claim = StructuredClaim(
        claim_id="1",
        claim_text="一种系统，包括模块A。",
        claim_type="independent",
    )
    assert claim.parent_claim_ids == []
