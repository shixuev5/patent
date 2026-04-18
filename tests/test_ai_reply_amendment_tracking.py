import json
from pathlib import Path

from agents.ai_reply.src.nodes.amendment_tracking import AmendmentTrackingNode


_NOISE_FIXTURE = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "ai_reply_noise_samples.json").read_text(encoding="utf-8")
)


def test_build_claim_alignments_marks_pure_reindex_shift() -> None:
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

    alignments = node._build_claim_alignments(old_claims, new_claims)
    changed_pairs = node._build_changed_claim_pairs(old_claims, new_claims, alignments)

    assert alignments == [
        {"claim_id": "1", "old_claim_id": "1", "alignment_kind": "same_number_match", "reason": "unchanged"},
        {"claim_id": "2", "old_claim_id": "3", "alignment_kind": "renumbered_successor", "reason": "upstream_deleted"},
    ]
    assert changed_pairs == []


def test_build_changed_claim_pairs_keeps_only_substantive_changes() -> None:
    node = AmendmentTrackingNode()
    old_claims = [
        {"claim_id": "1", "claim_text": "一种连接器，包括壳体和滑块。"},
        {"claim_id": "2", "claim_text": "根据权利要求1所述的连接器，还包括弹性件。"},
    ]
    new_claims = [
        {"claim_id": "1", "claim_text": "一种连接器，包括壳体和滑块，所述壳体内设有第一弹簧。"},
        {"claim_id": "2", "claim_text": "根据权利要求1所述的连接器，还包括弹性件。"},
    ]

    alignments = node._build_claim_alignments(old_claims, new_claims)
    changed_pairs = node._build_changed_claim_pairs(old_claims, new_claims, alignments)

    assert changed_pairs == [
        {
            "claim_id": "1",
            "old_text": "一种连接器，包括壳体和滑块。",
            "new_text": "一种连接器，包括壳体和滑块，所述壳体内设有第一弹簧。",
        }
    ]


def test_build_claim_alignments_prefers_shifted_dependent_successor_over_same_number_fallback() -> None:
    node = AmendmentTrackingNode()
    old_claims = [
        {"claim_id": "1", "claim_text": "一种车辆控制装置，包括控制器。", "claim_type": "independent", "parent_claim_ids": []},
        {"claim_id": "2", "claim_text": "根据权利要求1所述的车辆控制装置，其中，控制所述车辆的速度。", "claim_type": "dependent", "parent_claim_ids": ["1"]},
        {
            "claim_id": "3",
            "claim_text": "根据权利要求1或2所述的车辆控制装置，其中，与所述轮胎有关的信息包括滚动阻力系数值。",
            "claim_type": "dependent",
            "parent_claim_ids": ["1", "2"],
        },
        {
            "claim_id": "4",
            "claim_text": "根据权利要求1至3中任一项所述的车辆控制装置，其中，所述路线包括斜面区间，所述控制器使所述车辆的目标行驶速度保持恒定。",
            "claim_type": "dependent",
            "parent_claim_ids": ["1", "2", "3"],
        },
    ]
    new_claims = [
        {"claim_id": "1", "claim_text": "一种车辆控制装置，包括控制器。", "claim_type": "independent", "parent_claim_ids": []},
        {"claim_id": "2", "claim_text": "根据权利要求1所述的车辆控制装置，其中，控制所述车辆的速度。", "claim_type": "dependent", "parent_claim_ids": ["1"]},
        {
            "claim_id": "3",
            "claim_text": "根据权利要求1或2所述的车辆控制装置，其中，所述路线包括斜面区间，所述控制器使所述车辆的目标行驶速度保持恒定。",
            "claim_type": "dependent",
            "parent_claim_ids": ["1", "2"],
        },
    ]

    alignments = node._build_claim_alignments(old_claims, new_claims)
    changed_pairs = node._build_changed_claim_pairs(old_claims, new_claims, alignments)

    assert alignments == [
        {"claim_id": "1", "old_claim_id": "1", "alignment_kind": "same_number_match", "reason": "unchanged"},
        {"claim_id": "2", "old_claim_id": "2", "alignment_kind": "same_number_match", "reason": "unchanged"},
        {"claim_id": "3", "old_claim_id": "4", "alignment_kind": "renumbered_successor", "reason": "upstream_deleted"},
    ]
    assert changed_pairs == []


def test_build_claim_alignments_body_match_respects_parent_chain() -> None:
    node = AmendmentTrackingNode()
    old_claims = [
        {"claim_id": "1", "claim_text": "一种装置，包括模块A。", "claim_type": "independent", "parent_claim_ids": []},
        {"claim_id": "2", "claim_text": "根据权利要求1所述的装置，其中，包括模块B。", "claim_type": "dependent", "parent_claim_ids": ["1"]},
        {"claim_id": "3", "claim_text": "根据权利要求1所述的装置，其中，包括模块C。", "claim_type": "dependent", "parent_claim_ids": ["1"]},
        {"claim_id": "4", "claim_text": "根据权利要求3所述的装置，其中，包括模块D。", "claim_type": "dependent", "parent_claim_ids": ["3"]},
    ]
    new_claims = [
        {"claim_id": "1", "claim_text": "一种装置，包括模块A。", "claim_type": "independent", "parent_claim_ids": []},
        {"claim_id": "2", "claim_text": "根据权利要求1所述的装置，其中，包括模块B。", "claim_type": "dependent", "parent_claim_ids": ["1"]},
        {"claim_id": "3", "claim_text": "根据权利要求1所述的装置，其中，包括模块D。", "claim_type": "dependent", "parent_claim_ids": ["1"]},
    ]

    alignments = node._build_claim_alignments(old_claims, new_claims)

    assert alignments == [
        {"claim_id": "1", "old_claim_id": "1", "alignment_kind": "same_number_match", "reason": "unchanged"},
        {"claim_id": "2", "old_claim_id": "2", "alignment_kind": "same_number_match", "reason": "unchanged"},
        {"claim_id": "3", "old_claim_id": "3", "alignment_kind": "same_number_match", "reason": "unchanged"},
    ]


def test_extract_structural_adjustments_builds_renumbering_and_reference_adjustment() -> None:
    node = AmendmentTrackingNode()
    old_claims = [
        {"claim_id": "1", "claim_text": "一种方法，包括步骤A。"},
        {"claim_id": "2", "claim_text": "根据权利要求1所述的方法，还包括步骤B。"},
        {"claim_id": "3", "claim_text": "根据权利要求2所述的方法，还包括步骤C。"},
    ]
    new_claims = [
        {"claim_id": "1", "claim_text": "一种方法，包括步骤A。"},
        {"claim_id": "2", "claim_text": "根据权利要求1所述的方法，还包括步骤C。"},
    ]

    alignments = node._build_claim_alignments(old_claims, new_claims)
    refreshed = node._refresh_alignment_reasons(alignments, old_claims, [])
    adjustments = node._extract_structural_adjustments(old_claims, new_claims, refreshed)

    assert adjustments == [
        {
            "adjustment_id": "S1",
            "claim_id": "2",
            "claim_type": "unknown",
            "old_claim_id": "3",
            "adjustment_kind": "renumbering",
            "reason": "upstream_deleted",
            "before_text": "权利要求3",
            "after_text": "权利要求2",
        },
        {
            "adjustment_id": "S2",
            "claim_id": "2",
            "claim_type": "unknown",
            "old_claim_id": "3",
            "adjustment_kind": "reference_adjustment",
            "reason": "upstream_deleted",
            "before_text": "根据权利要求2所述的方法，还包括步骤C。",
            "after_text": "根据权利要求1所述的方法，还包括步骤C。",
        },
    ]


def test_normalize_tracking_result_accepts_new_substantive_amendment_schema() -> None:
    node = AmendmentTrackingNode()

    normalized = node._normalize_tracking_result(
        {
            "substantive_amendments": [
                {
                    "amendment_id": "A1",
                    "feature_text": "第一弹簧与壳体连接",
                    "feature_before_text": "弹簧与壳体连接",
                    "feature_after_text": "第一弹簧与壳体连接",
                    "target_claim_ids": ["1"],
                    "amendment_kind": "spec_feature_addition",
                    "content_origin": "specification",
                    "source_claim_ids": ["9"],
                },
                {
                    "amendment_id": "A2",
                    "feature_text": "第二弹簧与滑块连接",
                    "target_claim_ids": ["2"],
                    "amendment_kind": "claim_feature_merge",
                    "content_origin": "old_claim",
                    "source_claim_ids": ["5"],
                },
            ],
        }
    )

    assert normalized["substantive_amendments"][0]["source_claim_ids"] == []
    assert normalized["substantive_amendments"][0]["feature_before_text"] == "弹簧与壳体连接"
    assert normalized["substantive_amendments"][0]["search_feature_text"] == "第一弹簧与壳体连接"
    assert normalized["substantive_amendments"][1]["feature_after_text"] == "第二弹簧与滑块连接"
    assert normalized["substantive_amendments"][1]["search_feature_text"] == "第二弹簧与滑块连接"


def test_normalize_tracking_result_filters_punctuation_only_change() -> None:
    node = AmendmentTrackingNode()

    normalized = node._normalize_tracking_result(
        {"substantive_amendments": [_NOISE_FIXTURE["amendment_claim_8"]]}
    )

    assert normalized["substantive_amendments"] == []


def test_normalize_tracking_result_filters_formula_spacing_only_change() -> None:
    node = AmendmentTrackingNode()

    normalized = node._normalize_tracking_result(
        {"substantive_amendments": [_NOISE_FIXTURE["amendment_claim_10"]]}
    )

    assert normalized["substantive_amendments"] == []


def test_build_changed_claim_pairs_ignores_html_and_image_placeholders() -> None:
    node = AmendmentTrackingNode()
    old_claims = [
        {"claim_id": "1", "claim_text": "一种方法，包括步骤A。"},
    ]
    new_claims = [
        {"claim_id": "1", "claim_text": "<u>一种方法</u>，包括步骤A。<img src=\"image.png\" alt=\"IMG\" />"},
    ]

    alignments = node._build_claim_alignments(old_claims, new_claims)
    changed_pairs = node._build_changed_claim_pairs(old_claims, new_claims, alignments)

    assert alignments == [
        {"claim_id": "1", "old_claim_id": "1", "alignment_kind": "same_number_match", "reason": "unchanged"},
    ]
    assert changed_pairs == []


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
    assert result["claim_alignments"] == []
    assert result["substantive_amendments"] == []
    assert result["structural_adjustments"] == []


def test_track_amendment_falls_back_to_original_when_previous_missing_for_multi_notice() -> None:
    node = AmendmentTrackingNode()
    node.llm_service = type(
        "StubLLM",
        (),
        {
            "invoke_text_json": staticmethod(
                lambda messages, task_kind, temperature: {
                    "substantive_amendments": [
                        {
                            "amendment_id": "A1",
                            "feature_text": "模块C",
                            "target_claim_ids": ["1"],
                            "amendment_kind": "spec_feature_addition",
                            "content_origin": "specification",
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
    assert result["claim_alignments"] == [
        {"claim_id": "1", "old_claim_id": "1", "alignment_kind": "same_number_match", "reason": "unchanged"}
    ]
    assert result["substantive_amendments"][0]["amendment_id"] == "A1"


def test_build_system_prompt_tightens_claim_merge_source_boundary_without_over_splitting() -> None:
    node = AmendmentTrackingNode()

    prompt = node._build_system_prompt()

    assert "旧权利要求明确记载的技术限定内容" in prompt
    assert "并列备选项" in prompt
    assert "Markush 式列举" in prompt
    assert "至少一个" in prompt
    assert "语法承接表达" in prompt
    assert "输入" in prompt
    assert "接收" in prompt
    assert "控制" in prompt
    assert "外层动作或连接性措辞" in prompt
    assert "纯粹的、客观的技术特征陈述" in prompt
    assert "增加了基于RRC值控制目标加速度的逻辑" in prompt
    assert "基于轮胎的RRC值控制车辆的目标加速度" in prompt
    assert "外壳表面设有厚度为0.1mm的防水涂层" in prompt
    assert "核心限定变化" in prompt
    assert "单条完整技术特征" in prompt
    assert "不要为了规避上述要求而把一个原本单一的技术限定拆得更细" in prompt


def test_build_user_prompt_emphasizes_objective_feature_rewrite_and_global_traceability() -> None:
    node = AmendmentTrackingNode()

    prompt = node._build_user_prompt(
        {
            "changed_claims_pairs": [
                {
                    "claim_id": "1",
                    "old_text": "旧权1",
                    "new_text": "新权1",
                }
            ],
            "full_old_claims_context": {"3": "旧权3"},
        }
    )

    assert "先对比 `new_text` 和 `old_text`" in prompt
    assert "对每个新增技术点" in prompt
    assert "Markush 式列举" in prompt
    assert "客观技术事实" in prompt
    assert "不能返回整条权利要求" in prompt
    assert "old_text` 为空" in prompt


def test_track_amendment_accepts_claim_feature_merge_for_markush_selection() -> None:
    node = AmendmentTrackingNode()
    node.llm_service = type(
        "StubLLM",
        (),
        {
            "invoke_text_json": staticmethod(
                lambda messages, task_kind, temperature: {
                    "substantive_amendments": [
                        {
                            "amendment_id": "A1",
                            "feature_text": "基于轮胎的RRC值控制车辆的目标加速度",
                            "feature_before_text": "与所述轮胎有关的信息包括以下项中的至少一个：滚动阻力系数值、均匀性、外径",
                            "feature_after_text": "基于轮胎的RRC值控制车辆的目标加速度",
                            "target_claim_ids": ["1"],
                            "amendment_kind": "claim_feature_merge",
                            "content_origin": "old_claim",
                            "source_claim_ids": ["3"],
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
                    {
                        "claim_id": "1",
                        "claim_text": "一种车辆控制装置，包括控制器，所述控制器控制车辆行驶。",
                        "claim_type": "independent",
                        "parent_claim_ids": [],
                    },
                    {
                        "claim_id": "3",
                        "claim_text": "根据权利要求1所述的车辆控制装置，其中，与所述轮胎有关的信息包括以下项中的至少一个：滚动阻力系数值、均匀性、外径。",
                        "claim_type": "dependent",
                        "parent_claim_ids": ["1"],
                    },
                ]
            }
        },
        "office_action": {"current_notice_round": 1},
    }
    current_claims = [
        {
            "claim_id": "1",
            "claim_text": "一种车辆控制装置，包括控制器，所述控制器基于轮胎的RRC值控制车辆的目标加速度。",
            "claim_type": "independent",
            "parent_claim_ids": [],
        }
    ]

    result = node._track_amendment(prepared_materials, previous_claims=[], current_claims=current_claims)

    assert result["has_claim_amendment"] is True
    assert result["substantive_amendments"] == [
        {
            "amendment_id": "A1",
            "target_claim_ids": ["1"],
            "amendment_kind": "claim_feature_merge",
            "content_origin": "old_claim",
            "source_claim_ids": ["3"],
            "feature_text": "基于轮胎的RRC值控制车辆的目标加速度",
            "search_feature_text": "基于轮胎的RRC值控制车辆的目标加速度",
            "feature_before_text": "与所述轮胎有关的信息包括以下项中的至少一个：滚动阻力系数值、均匀性、外径",
            "feature_after_text": "基于轮胎的RRC值控制车辆的目标加速度",
        }
    ]
