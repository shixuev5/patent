from __future__ import annotations

from agents.common.office_action_structuring.rule_based_extractor import OfficeActionExtractor


def test_extract_claim_ids_only_uses_first_single_claim_reference() -> None:
    paragraph = (
        "权利要求1相对于对比文件1不具备创造性。"
        "此外，权利要求2属于从属权利要求，且引用的权利要求1也不具备创造性。"
    )

    claim_ids = OfficeActionExtractor()._extract_claim_ids(paragraph)

    assert claim_ids == ["1"]


def test_extract_claim_ids_only_uses_first_range_claim_reference() -> None:
    paragraph = (
        "权利要求2-4不具备创造性。"
        "另，权利要求6相对于对比文件2也不具备创造性。"
    )

    claim_ids = OfficeActionExtractor()._extract_claim_ids(paragraph)

    assert claim_ids == ["2", "3", "4"]
