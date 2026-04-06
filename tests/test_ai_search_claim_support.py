from __future__ import annotations

from agents.ai_search.src.claim_support import (
    build_claim_packets,
    expand_claim_dependency,
    load_structured_claims_from_patent_data,
)


def test_load_structured_claims_from_patent_data_normalizes_parent_claim_ids():
    patent_data = {
        "claims": [
            {
                "claim_id": "1",
                "claim_text": "一种装置，包括模块A。",
                "claim_type": "independent",
                "parent_claim_ids": [],
            },
            {
                "claim_id": "2",
                "claim_text": "根据权利要求1所述的装置，还包括模块B。",
                "claim_type": "dependent",
                "parent_claim_ids": ["1"],
            },
        ]
    }

    claims = load_structured_claims_from_patent_data(patent_data)

    assert len(claims) == 2
    assert claims[1]["parent_claim_ids"] == ["1"]


def test_expand_claim_dependency_expands_chain():
    claims = [
        {"claim_id": "1", "claim_text": "一种装置，包括模块A。", "claim_type": "independent", "parent_claim_ids": []},
        {"claim_id": "2", "claim_text": "根据权利要求1所述的装置，还包括模块B。", "claim_type": "dependent", "parent_claim_ids": ["1"]},
        {"claim_id": "3", "claim_text": "根据权利要求2所述的装置，还包括模块C。", "claim_type": "dependent", "parent_claim_ids": ["2"]},
    ]

    expanded = expand_claim_dependency(claims, ["3"])

    assert len(expanded) == 1
    assert expanded[0]["claim_id"] == "3"
    assert expanded[0]["lineage_claim_ids"] == ["3", "2", "1"]
    assert "模块A" in expanded[0]["combined_claim_text"]
    assert "模块B" in expanded[0]["combined_claim_text"]
    assert "模块C" in expanded[0]["combined_claim_text"]


def test_build_claim_packets_merges_search_elements():
    expanded_claims = [
        {
            "claim_id": "1",
            "claim_type": "independent",
            "claim_text": "一种装置，包括模块A。",
            "parent_claim_ids": [],
            "lineage_claim_ids": ["1"],
            "combined_claim_text": "一种装置，包括模块A。",
            "expanded_limitations": ["一种装置，包括模块A。"],
        }
    ]
    search_elements = [
        {"element_name": "模块A", "keywords_zh": ["模块A"], "keywords_en": ["module A"]},
    ]

    packets = build_claim_packets(expanded_claims, search_elements)

    assert len(packets) == 1
    assert packets[0]["claim_id"] == "1"
    assert "模块A" in packets[0]["candidate_terms"]
    assert "module A" in packets[0]["candidate_terms"]
