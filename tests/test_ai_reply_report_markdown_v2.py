import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.ai_reply.src.report_markdown import build_final_report_markdown
from agents.common.rendering.report_render import markdown_to_html_document


_NOISE_FIXTURE = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "ai_reply_noise_samples.json").read_text(encoding="utf-8")
)


def _sample_report() -> dict:
    return {
        "notice_context": {
            "current_notice_round": 2,
            "next_notice_round": 3,
        },
        "summary": {
            "total_disputes": 2,
            "assessed_disputes": 2,
            "unassessed_disputes": 0,
            "response_reply_points": 1,
            "rebuttal_type_distribution": {
                "fact_dispute": 1,
                "logic_dispute": 1,
                "unknown": 0,
            },
            "verdict_distribution": {
                "applicant_correct": 1,
                "examiner_correct": 0,
                "inconclusive": 1,
            },
        },
        "amendment_section": {
            "has_claim_amendment": True,
            "added_matter_risk": False,
            "added_matter_risk_summary": "",
            "substantive_amendments": [
                {
                    "amendment_id": "A1",
                    "target_claim_ids": ["1"],
                    "feature_text": "星间激光通信模块用于星间组网 SUMMARY_FEATURE_END",
                }
            ],
            "substantive_change_groups": [
                {
                    "claim_id": "1",
                    "claim_type": "independent",
                    "items": [
                        {
                            "amendment_id": "A1",
                            "feature_text": "变更特征_ONLY_IN_SECTION3",
                            "feature_before_text": "旧版本片段",
                            "feature_after_text": "变更特征_ONLY_IN_SECTION3",
                            "contains_added_text": True,
                            "amendment_kind": "spec_feature_addition",
                            "content_origin": "specification",
                            "source_claim_ids": [],
                            "has_ai_assessment": True,
                            "assessment": {
                                "verdict": "EXAMINER_CORRECT",
                                "reasoning": "修改后的特征属于本领域常规设置 CHANGE_REASON_END",
                            },
                            "evidence": [
                                {
                                    "doc_id": "D1",
                                    "location": "第2页",
                                    "quote": "修改证据引文 CHANGE_QUOTE_END",
                                    "analysis": "修改证据分析 CHANGE_ANALYSIS_END",
                                }
                            ],
                            "final_review_reason": "经审查，修改后的权利要求1仍不具备创造性 CHANGE_FINAL_END",
                        }
                    ],
                }
            ],
            "structural_adjustments": [
                {
                    "adjustment_id": "S1",
                    "claim_id": "5",
                    "claim_type": "dependent",
                    "old_claim_id": "6",
                    "adjustment_kind": "renumbering",
                    "reason": "upstream_deleted",
                    "before_text": "权利要求6",
                    "after_text": "权利要求5",
                }
            ],
        },
        "claim_review_section": {
            "items": [
                {
                    "unit_id": "P1",
                    "unit_type": "evidence_restructured",
                    "source_paragraph_ids": ["P1"],
                    "display_claim_ids": ["1"],
                    "anchor_claim_id": "1",
                    "title": "权利要求1",
                    "source_summary": {
                        "merged_source_claim_ids": ["3"],
                        "amendment_ids": ["A1"],
                    },
                    "review_before_text": "关于权利要求1，旧评述基线 REVIEW_BEFORE_ONLY",
                    "claim_snapshots": [
                        {
                            "claim_id": "1",
                            "claim_before_text": "一种装置，其特征在于，包括旧模块A CLAIM_BEFORE_ONLY。",
                            "claim_text": "一种装置，其特征在于，包括新模块A CLAIM_AFTER_ONLY。",
                        },
                    ],
                    "review_text": "关于权利要求1，经审查，结合新证据后的重组评述 CLAIM_REVIEW_1_END",
                },
                {
                    "unit_id": "M1",
                    "unit_type": "supplemented_new",
                    "source_paragraph_ids": ["Claim4"],
                    "display_claim_ids": ["4", "5"],
                    "anchor_claim_id": "4",
                    "title": "权利要求4、权利要求5",
                    "source_summary": {},
                    "review_before_text": "",
                    "claim_snapshots": [
                        {
                            "claim_id": "4",
                            "claim_before_text": "",
                            "claim_text": "根据权利要求1所述的一种装置，其特征在于，包括模块A和模块B。 CLAIM4_ADD_ONLY",
                        },
                        {
                            "claim_id": "5",
                            "claim_before_text": "",
                            "claim_text": "根据权利要求4所述的一种装置，其特征在于，包括模块C和模块D。 CLAIM5_ADD_ONLY",
                        },
                    ],
                    "review_text": "关于权利要求4-5，结合原从权内容，经审查，对申请人意见不予采纳 CLAIM_REVIEW_2_END",
                },
            ]
        },
        "response_dispute_section": {
            "items": [
                {
                    "dispute_id": "DSP_1",
                    "origin": "response_dispute",
                    "claim_ids": ["1", "2"],
                    "feature_text": "争议特征" * 24 + " FEATURE_END",
                    "examiner_opinion": {
                        "type": "document_based",
                        "reasoning": "审查员理由" * 18 + " EXAM_END",
                    },
                    "applicant_opinion": {
                        "reasoning": "申请人理由" * 18 + " APP_END",
                    },
                    "evidence_assessment": {
                        "assessment": {
                            "verdict": "APPLICANT_CORRECT",
                            "confidence": 0.84,
                            "reasoning": "AI理由" * 18 + " AI_REASON_END",
                            "examiner_rejection_rationale": "结合 D1 与 D2 的公开内容，相关权利要求仍可被认定为不具备创造性。",
                        },
                        "evidence": [
                            {
                                "source_title": None,
                                "doc_id": "对比文件D1",
                                "location": "第3页第1段",
                                "quote": "证据引文" * 18 + " QUOTE_END",
                                "analysis": "证据分析" * 18 + " ANALYSIS_END",
                            },
                            {
                                "doc_id": "EXT1",
                                "location": "第2页",
                                "analysis": "补强分析 SECOND_ANALYSIS_END",
                            },
                        ],
                    },
                },
                {
                    "dispute_id": "DSP_2",
                    "origin": "response_dispute",
                    "claim_ids": ["3"],
                    "feature_text": "第二个争议特征 SECOND_FEATURE_END",
                    "examiner_opinion": {
                        "type": "mixed_basis",
                        "reasoning": "第二条审查理由 SECOND_EXAM_END",
                    },
                    "applicant_opinion": {
                        "reasoning": "",
                    },
                    "evidence_assessment": {
                        "assessment": {
                            "verdict": "INCONCLUSIVE",
                            "confidence": 0.33,
                            "reasoning": "尚需补充检索 SECOND_AI_REASON_END",
                            "examiner_rejection_rationale": "",
                        },
                        "evidence": [],
                    },
                },
            ]
        },
        "response_reply_section": {
            "items": [
                {
                    "dispute_id": "DSP_1",
                    "claim_ids": ["1", "2"],
                    "feature_text": "争议特征" * 24 + " FEATURE_END",
                    "applicant_opinion": {
                        "reasoning": "申请人理由" * 18 + " APP_END",
                    },
                    "final_examiner_rejection_reason": "经再次审查，仍可基于 D1 与 D2 的结合维持驳回。",
                },
                {
                    "dispute_id": "DSP_2",
                    "claim_ids": ["3"],
                    "feature_text": "第二个争议特征 SECOND_FEATURE_END",
                    "applicant_opinion": {
                        "reasoning": "",
                    },
                    "final_examiner_rejection_reason": "",
                },
            ]
        },
    }


def test_build_final_report_markdown_renders_new_six_section_layout() -> None:
    content = build_final_report_markdown(_sample_report())

    assert "整体判断" in content
    assert "本次答复基本成立" in content
    assert "重点风险" in content
    assert "1 项仍需重点复核" in content
    assert "核查进度" in content
    assert "2/2 项已核查" in content
    assert "支撑强度" in content
    assert "中等支撑 1 项" in content
    assert "主导裁决" not in content
    assert "主导争议类型" not in content
    assert "主导置信分层" not in content
    assert 'class="oar-conclusion-card oar-conclusion-card-emphasis"' in content
    assert "## 3. 权利要求变更表" in content
    assert "### 3.1 实质修改" not in content
    assert "### 3.2 结构调整" not in content
    assert "## 4. 基于上一轮审查意见的重组评述" in content
    assert "## 5. 争论点总表与AI判断" in content
    assert "## 6. 针对申请人意见陈述的答复" in content
    assert '<th class="oar-col-claims">权利要求</th>' in content
    assert "变更特征_ONLY_IN_SECTION3" in content
    assert "权利要求5" in content
    assert "来源类型" not in content
    assert "1（来源权利要求 5）" not in content
    assert "2（来源权利要求 5）" not in content
    assert "CHANGE_REASON_END" in content
    assert "CHANGE_QUOTE_END" in content
    assert "CHANGE_ANALYSIS_END" in content
    assert "CHANGE_FINAL_END" in content
    assert "CLAIM_CHANGE_REASON_SHOULD_HIDE" not in content
    assert "CLAIM_CHANGE_QUOTE_SHOULD_HIDE" not in content
    assert "CLAIM_CHANGE_ANALYSIS_SHOULD_HIDE" not in content
    assert "CLAIM_CHANGE_FINAL_SHOULD_HIDE" not in content
    assert "权项上提" not in content
    assert "说明书记载补入" in content
    assert "编号顺延" in content
    assert "未核查" not in content
    assert 'class="oar-layered-table oar-layered-table-overview oar-claim-change-table"' in content
    assert 'rowspan="2"' in content
    assert 'class="oar-layered-cell oar-claim-change-cell-detail">' in content
    assert 'class="oar-change-item-card"' in content
    assert 'class="oar-change-item-title">来源说明书</div>' in content
    assert 'class="oar-change-item-label">变更内容</div>' in content
    assert 'class="oar-change-ai-detail-stack"' in content
    assert ">1（独权）<" in content
    assert "旧权利要求6因上游权项删除，顺延为现权利要求5。" in content
    section3 = content.split("## 4. 基于上一轮审查意见的重组评述", 1)[0]
    assert '<th class="oar-col-verdict">AI判断</th>' not in section3
    assert '<th class="oar-col-index">序号</th>' not in section3
    assert 'colspan="2"' not in section3
    assert 'colspan="3"' not in section3
    assert "oar-change-ai-summary-item" not in section3
    assert "调整说明：" not in section3
    assert "当前权项：" not in section3
    assert "调整内容" not in section3


def test_build_final_report_markdown_renders_claim_reviews_and_response_reply_blocks() -> None:
    content = build_final_report_markdown(_sample_report())

    assert "CLAIM_REVIEW_1_END" in content
    assert "CLAIM_REVIEW_2_END" in content
    assert "独权重组" in content
    assert "补充评述" in content
    assert "来源OA段落：" not in content
    assert "权利要求：" in content
    assert "正式评述：" in content
    assert "修改摘要：" in content
    assert 'oar-change-del">CLAIM_BEFORE_ONLY' in content
    assert 'oar-change-add">CLAIM_AFTER_ONLY' in content
    assert 'oar-change-add">根据权利要求1所述的一种装置' in content
    assert "REVIEW_BEFORE_ONLY" not in content
    assert "并入来源：吸收权利要求3的旧权限定。" in content
    assert "新增限定：星间激光通信模块用于星间组网 SUMMARY_FEATURE_END。" in content
    assert "评述处理：沿用上一轮审查意见骨架后完成补强。" in content
    assert "评述处理：无可复用原评述，本轮补成正式评述。" in content
    assert "权利要求4" in content
    assert "5（从权）" in content
    assert 'class="oar-opinion-block"' in content
    assert 'class="oar-claim-snapshot-list"' in content
    assert 'class="oar-claim-snapshot-item"' in content
    assert 'class="oar-review-summary-list"' in content
    assert 'class="oar-claim-snapshot-head">权利要求1</div>' not in content
    assert 'class="oar-claim-snapshot-head">权利要求4</div>' in content
    assert 'class="oar-claim-snapshot-head">权利要求5</div>' in content
    assert "申请人指出：" in content


def test_build_final_report_markdown_merges_structural_adjustments_per_claim() -> None:
    report = _sample_report()
    report["amendment_section"]["structural_adjustments"] = [
        {
            "adjustment_id": "S1",
            "claim_id": "4",
            "claim_type": "dependent",
            "old_claim_id": "6",
            "adjustment_kind": "renumbering",
            "reason": "upstream_merged",
            "before_text": "权利要求6",
            "after_text": "权利要求4",
        },
        {
            "adjustment_id": "S2",
            "claim_id": "4",
            "claim_type": "dependent",
            "old_claim_id": "6",
            "adjustment_kind": "reference_adjustment",
            "reason": "upstream_merged",
            "before_text": "根据权利要求3所述的一种装置",
            "after_text": "根据权利要求1所述的一种装置",
        },
    ]

    content = build_final_report_markdown(report)

    assert content.count("对应旧权利要求 6") == 1
    assert "旧权利要求6因上游权项并入，顺延为现权利要求4，且其引用基础由“权利要求3”变更为“权利要求1”。" in content
    assert content.count("编号顺延") == 1
    assert content.count("引用关系调整") == 1
    assert "审查员答复：" in content
    assert "未提取到申请人详细意见陈述。" in content
    assert "旧整段文本不应作为第 5 部分主展示内容。" not in content
    assert "\n> " not in content
    assert "支持申请人" in content
    assert "支持审查员" in content
    assert "暂不确定" in content
    assert "AI更支持申请人" not in content
    assert "AI更支持审查员" not in content
    assert "AI暂不确定" not in content


def test_build_final_report_markdown_html_conversion_preserves_layered_layout() -> None:
    content = build_final_report_markdown(_sample_report())
    html_doc = markdown_to_html_document(
        content,
        title="AI Reply Report",
        enable_mathjax=False,
        enable_echarts=False,
    )

    assert '<table class="oar-layered-table oar-layered-table-overview oar-claim-change-table">' in html_doc
    assert 'class="oar-opinion-block"' in html_doc
    assert 'class="oar-claim-snapshot-list"' in html_doc
    assert 'class="oar-verdict-badge oar-verdict-badge-applicant"' in html_doc
    assert 'class="oar-change-add"' in html_doc
    assert 'class="oar-change-del"' in html_doc
    assert 'class="oar-change-source-tag oar-change-source-tag-spec"' in html_doc
    assert "独权重组" in html_doc
    assert "CLAIM_BEFORE_ONLY" in html_doc
    assert "CLAIM_AFTER_ONLY" in html_doc
    assert "CLAIM_REVIEW_1_END" in html_doc
    assert "修改摘要：" in html_doc
    assert "CHANGE_FINAL_END" in html_doc
    assert "<blockquote>" not in html_doc


def test_build_final_report_markdown_renders_quote_translation_for_non_chinese_quotes() -> None:
    report = {
        "summary": {},
        "amendment_section": {},
        "response_dispute_section": {
            "items": [
                {
                    "dispute_id": "DSP_1",
                    "origin": "response_dispute",
                    "claim_ids": ["1"],
                    "feature_text": "特征A",
                    "examiner_opinion": {"type": "mixed_basis", "reasoning": "审查员理由"},
                    "applicant_opinion": {"reasoning": "申请人理由"},
                    "evidence_assessment": {
                        "assessment": {
                            "verdict": "EXAMINER_CORRECT",
                            "confidence": 0.85,
                            "reasoning": "AI判断",
                            "examiner_rejection_rationale": "",
                        },
                        "evidence": [
                            {
                                "doc_id": "EXT1",
                                "location": "摘要",
                                "quote": "A system and a method for monitoring pressure inside a railway vehicle comprise a carriage pressure detection device.",
                                "quote_translation": "一种用于监测轨道车辆内部压力的系统和方法，包括车厢压力检测装置。",
                                "analysis": "英文证据支持分析。",
                            },
                            {
                                "doc_id": "D1",
                                "location": "说明书后半段",
                                "quote": "車両がトンネル内走行中に、車内圧力検出装置５で検出した車内圧力値が車内の圧力の制御の基準値に対し変動した場合。",
                                "quote_translation": "当车辆在隧道内行驶时，如果车内压力检测装置5检测到的车内压力值相对于车内压力控制基准值发生变化。",
                                "analysis": "日文证据支持分析。",
                            },
                            {
                                "doc_id": "D2",
                                "location": "第3页",
                                "quote": "该专利公开了车内压力监测与报警的基础系统架构。",
                                "analysis": "中文证据支持分析。",
                            },
                        ],
                    },
                }
            ]
        },
        "response_reply_section": {"items": []},
        "claim_review_section": {"items": []},
    }

    content = build_final_report_markdown(report)

    assert "译文：" in content
    assert "一种用于监测轨道车辆内部压力的系统和方法，包括车厢压力检测装置。" in content
    assert "当车辆在隧道内行驶时，如果车内压力检测装置5检测到的车内压力值相对于车内压力控制基准值发生变化。" in content
    assert "中文证据支持分析。" in content
    english_quote_pos = content.index("A system and a method for monitoring pressure inside a railway vehicle")
    english_translation_pos = content.index("一种用于监测轨道车辆内部压力的系统和方法，包括车厢压力检测装置。")
    english_analysis_pos = content.index("英文证据支持分析。")
    assert english_quote_pos < english_translation_pos < english_analysis_pos
    chinese_quote_pos = content.index("该专利公开了车内压力监测与报警的基础系统架构。")
    chinese_analysis_pos = content.index("中文证据支持分析。")
    assert chinese_quote_pos < chinese_analysis_pos
    assert content.count("译文：") == 2


def test_build_final_report_markdown_preserves_upstream_unique_claim_cards() -> None:
    report = {
        "summary": {},
        "amendment_section": {},
        "response_dispute_section": {"items": []},
        "response_reply_section": {"items": []},
        "claim_review_section": {
            "items": [
                {
                    "unit_id": "U1",
                    "unit_type": "evidence_restructured",
                    "display_claim_ids": ["1"],
                    "title": "权利要求1",
                    "claim_snapshots": [
                        {"claim_id": "1", "claim_before_text": "", "claim_text": "权1文本。"},
                    ],
                    "review_before_text": "",
                    "review_text": "权1评述。",
                },
                {
                    "unit_id": "U2",
                    "unit_type": "supplemented_new",
                    "display_claim_ids": ["8"],
                    "title": "权利要求8",
                    "claim_snapshots": [
                        {"claim_id": "8", "claim_before_text": "", "claim_text": "权8文本。"},
                    ],
                    "review_before_text": "",
                    "review_text": "权8评述。",
                },
                {
                    "unit_id": "U3",
                    "unit_type": "dependent_group_restructured",
                    "display_claim_ids": ["2", "3", "4", "5", "6", "7", "9"],
                    "title": "权利要求2、权利要求3、权利要求4、权利要求5、权利要求6、权利要求7、权利要求9",
                    "claim_snapshots": [
                        {"claim_id": "2", "claim_before_text": "", "claim_text": "权2文本。"},
                        {"claim_id": "3", "claim_before_text": "", "claim_text": "权3文本。"},
                        {"claim_id": "4", "claim_before_text": "", "claim_text": "权4文本。"},
                        {"claim_id": "5", "claim_before_text": "", "claim_text": "权5文本。"},
                        {"claim_id": "6", "claim_before_text": "", "claim_text": "权6文本。"},
                        {"claim_id": "7", "claim_before_text": "", "claim_text": "权7文本。"},
                        {"claim_id": "9", "claim_before_text": "", "claim_text": "权9文本。"},
                    ],
                    "review_before_text": "",
                    "review_text": "权9评述。",
                },
            ]
        },
    }

    content = build_final_report_markdown(report)

    assert "权利要求1｜独权重组" in content
    assert "权利要求8｜补充评述" in content
    assert "权利要求2、权利要求3、权利要求4、权利要求5、权利要求6、权利要求7、权利要求9｜从权组重组" in content
    assert "权8文本。" in content
    assert "权9文本。" in content
    assert "修改摘要：" in content


def test_build_final_report_markdown_shows_non_ai_change_items_as_not_applicable() -> None:
    report = {
        "summary": {},
        "amendment_section": {"substantive_change_groups": [{"claim_id": "3", "claim_type": "dependent", "items": [{"amendment_id": "A2", "feature_text": "保持不变的特征", "feature_before_text": "保持不变的特征", "feature_after_text": "保持不变的特征", "contains_added_text": False, "amendment_kind": "spec_feature_addition", "content_origin": "specification", "source_claim_ids": [], "has_ai_assessment": False, "assessment": {}, "evidence": [], "final_review_reason": ""}]}], "structural_adjustments": []},
        "response_dispute_section": {"items": []},
        "response_reply_section": {"items": []},
        "claim_review_section": {"items": []},
    }

    content = build_final_report_markdown(report)
    section3 = content.split("## 4. 基于上一轮审查意见的重组评述", 1)[0]

    assert "保持不变的特征" in content
    assert 'rowspan="1"' in content
    assert 'oar-claim-change-cell-detail">' not in section3
    assert 'oar-verdict-badge oar-verdict-badge-unassessed' not in section3
    assert 'class="oar-change-unassessed"' not in section3
    assert "当前变更项无需展开 AI 理由与依据。" not in section3
    assert ">3（从权）<" in content


def test_build_final_report_markdown_renders_claim_source_change_items_in_section3() -> None:
    report = {
        "summary": {},
        "amendment_section": {
            "substantive_change_groups": [
                {
                    "claim_id": "7",
                    "claim_type": "dependent",
                    "items": [
                        {
                            "amendment_id": "A9",
                            "feature_text": "旧权9并入现权7",
                            "feature_before_text": "",
                            "feature_after_text": "旧权9并入现权7",
                            "contains_added_text": True,
                            "amendment_kind": "claim_feature_merge",
                            "content_origin": "old_claim",
                            "source_claim_ids": ["9"],
                            "has_ai_assessment": False,
                            "assessment": {},
                            "evidence": [],
                            "final_review_reason": "",
                        }
                    ],
                }
            ]
        },
        "response_dispute_section": {"items": []},
        "response_reply_section": {"items": []},
        "claim_review_section": {"items": []},
    }

    content = build_final_report_markdown(report)

    assert "来源权利要求 9" in content
    assert "从权特征并入" in content
    assert 'class="oar-change-item-title">来源权利要求 9</div>' in content
    assert 'class="oar-change-claims-main">来源权利要求 9</div>' not in content
    assert ">7（从权）<" in content


def test_build_final_report_markdown_group_with_mixed_ai_items_only_renders_ai_conclusions() -> None:
    report = {
        "summary": {},
        "amendment_section": {
            "substantive_change_groups": [
                {
                    "claim_id": "3",
                    "claim_type": "dependent",
                    "items": [
                        {
                            "amendment_id": "A2",
                            "feature_text": "需要AI判断的项",
                            "feature_before_text": "旧特征",
                            "feature_after_text": "需要AI判断的项",
                            "contains_added_text": True,
                            "amendment_kind": "claim_feature_merge",
                            "content_origin": "old_claim",
                            "source_claim_ids": ["1"],
                            "has_ai_assessment": True,
                            "assessment": {
                                "verdict": "EXAMINER_CORRECT",
                                "reasoning": "MIXED_AI_REASON",
                            },
                            "evidence": [{"doc_id": "D1", "analysis": "MIXED_AI_EVIDENCE"}],
                            "final_review_reason": "MIXED_AI_FINAL",
                        },
                        {
                            "amendment_id": "A3",
                            "feature_text": "无需AI判断的项",
                            "feature_before_text": "无需AI判断的项",
                            "feature_after_text": "无需AI判断的项",
                            "contains_added_text": False,
                            "amendment_kind": "spec_feature_addition",
                            "content_origin": "specification",
                            "source_claim_ids": [],
                            "has_ai_assessment": False,
                            "assessment": {},
                            "evidence": [],
                            "final_review_reason": "",
                        },
                    ],
                }
            ]
        },
        "response_dispute_section": {"items": []},
        "response_reply_section": {"items": []},
        "claim_review_section": {"items": []},
    }

    content = build_final_report_markdown(report)

    assert "MIXED_AI_REASON" in content
    assert "MIXED_AI_EVIDENCE" in content
    assert "MIXED_AI_FINAL" in content
    assert "无需AI判断的项" in content
    assert '<span class="oar-verdict-badge oar-verdict-badge-unassessed">无需AI判断</span>' not in content
    assert 'class="oar-change-ai-verdict"' in content
    assert 'class="oar-change-ai-detail-stack"' in content
    assert 'oar-change-ai-summary-item' not in content


def test_build_final_report_markdown_keeps_claim_cards_in_effective_claim_order() -> None:
    report = {
        "summary": {},
        "amendment_section": {},
        "response_dispute_section": {"items": []},
        "response_reply_section": {"items": []},
        "claim_review_section": {
            "items": [
                {
                    "unit_id": "U1",
                    "unit_type": "evidence_restructured",
                    "display_claim_ids": ["1"],
                    "title": "权利要求1",
                    "claim_snapshots": [{"claim_id": "1", "claim_before_text": "", "claim_text": "权1文本。"}],
                    "review_before_text": "",
                    "review_text": "权1评述。",
                },
                {
                    "unit_id": "U2",
                    "unit_type": "dependent_group_restructured",
                    "display_claim_ids": ["2"],
                    "title": "权利要求2",
                    "claim_snapshots": [{"claim_id": "2", "claim_before_text": "", "claim_text": "权2文本。"}],
                    "review_before_text": "权2旧评述。",
                    "review_text": "权2评述。",
                },
                {
                    "unit_id": "U3",
                    "unit_type": "dependent_group_restructured",
                    "display_claim_ids": ["7"],
                    "title": "权利要求7",
                    "claim_snapshots": [{"claim_id": "7", "claim_before_text": "", "claim_text": "权7文本。"}],
                    "review_before_text": "权7旧评述。",
                    "review_text": "权7评述。",
                },
                {
                    "unit_id": "U4",
                    "unit_type": "evidence_restructured",
                    "display_claim_ids": ["8"],
                    "title": "权利要求8",
                    "claim_snapshots": [{"claim_id": "8", "claim_before_text": "", "claim_text": "权8文本。"}],
                    "review_before_text": "权8旧评述。",
                    "review_text": "权8评述。",
                },
            ]
        },
    }

    content = build_final_report_markdown(report)

    pos1 = content.index("权利要求1｜独权重组")
    pos2 = content.index("权利要求2｜从权组重组")
    pos7 = content.index("权利要求7｜从权组重组")
    pos8 = content.index("权利要求8｜独权重组")
    assert pos1 < pos2 < pos7 < pos8


def test_build_final_report_markdown_review_summary_falls_back_without_amendment_data() -> None:
    report = {
        "summary": {},
        "amendment_section": {"substantive_amendments": []},
        "response_dispute_section": {"items": []},
        "response_reply_section": {"items": []},
        "claim_review_section": {
            "items": [
                {
                    "unit_id": "U1",
                    "unit_type": "dependent_group_restructured",
                    "display_claim_ids": ["2", "3"],
                    "title": "权利要求2、权利要求3",
                    "source_summary": {},
                    "claim_snapshots": [
                        {"claim_id": "2", "claim_before_text": "旧权2。", "claim_text": "新权2。"},
                        {"claim_id": "3", "claim_before_text": "旧权3。", "claim_text": "新权3。"},
                    ],
                    "review_before_text": "",
                    "review_text": "从权组正式评述。",
                }
            ]
        },
    }

    content = build_final_report_markdown(report)

    assert "重组范围：围绕权利要求2、3重组剩余从权评述。" in content
    assert "评述处理：缺少可复用原评述，本轮按现有素材重组正式评述。" in content
    assert "新增限定：" not in content


def test_build_final_report_markdown_sanitizes_review_html_noise_from_fixture() -> None:
    report = {
        "summary": {},
        "amendment_section": {"substantive_amendments": []},
        "response_dispute_section": {"items": []},
        "response_reply_section": {"items": []},
        "claim_review_section": {
            "items": [
                {
                    "unit_id": "Claim4",
                    "unit_type": "dependent_group_restructured",
                    "display_claim_ids": ["4"],
                    "title": "权利要求4",
                    "claim_snapshots": [],
                    "review_before_text": "",
                    "review_text": _NOISE_FIXTURE["review_claim_4"]["review_text"],
                    "source_summary": {},
                },
                {
                    "unit_id": "Claim7",
                    "unit_type": "dependent_group_restructured",
                    "display_claim_ids": ["7"],
                    "title": "权利要求7",
                    "claim_snapshots": [],
                    "review_before_text": "",
                    "review_text": _NOISE_FIXTURE["review_claim_7"]["review_text"],
                    "source_summary": {},
                },
                {
                    "unit_id": "Claim9",
                    "unit_type": "dependent_group_restructured",
                    "display_claim_ids": ["9"],
                    "title": "权利要求9",
                    "claim_snapshots": [],
                    "review_before_text": "",
                    "review_text": _NOISE_FIXTURE["review_claim_9"]["review_text"],
                    "source_summary": {},
                },
            ]
        },
    }

    content = build_final_report_markdown(report)

    assert "<u>" not in content
    assert "<img" not in content
    assert "原文含图片或公式，解析未提取到可展示文本" in content


def test_build_final_report_markdown_does_not_highlight_formula_spacing_noise() -> None:
    item = _NOISE_FIXTURE["amendment_claim_10"]
    report = {
        "summary": {},
        "amendment_section": {
            "substantive_change_groups": [
                {
                    "claim_id": "10",
                    "claim_type": "dependent",
                    "items": [
                        {
                            "amendment_id": item["amendment_id"],
                            "feature_text": item["feature_text"],
                            "feature_before_text": item["feature_before_text"],
                            "feature_after_text": item["feature_after_text"],
                            "contains_added_text": False,
                            "amendment_kind": item["amendment_kind"],
                            "content_origin": item["content_origin"],
                            "source_claim_ids": item["source_claim_ids"],
                            "has_ai_assessment": False,
                            "assessment": {},
                            "evidence": [],
                            "final_review_reason": "",
                        }
                    ],
                }
            ],
            "structural_adjustments": [],
        },
        "response_dispute_section": {"items": []},
        "response_reply_section": {"items": []},
        "claim_review_section": {"items": []},
    }

    content = build_final_report_markdown(report)

    assert "所述车内压力预测值的计算公式为" in content
    assert 'class="oar-change-add"' not in content
    assert 'class="oar-change-del"' not in content
    assert "\\left(" in content


def test_build_final_report_markdown_renders_search_followup_section_conditionally() -> None:
    report = _sample_report()
    report["search_followup_section"] = {
        "needed": True,
        "status": "complete",
        "objective": "围绕新增特征继续补检。",
        "trigger_reasons": ["现有核查结论暂不确定", "现有核查置信度偏低"],
        "gap_summaries": [
            {
                "claim_ids": ["1"],
                "feature_text": "星间激光通信模块",
                "gap_type": "insufficient_evidence",
                "gap_summary": "当前证据未稳定覆盖新增特征。",
            }
        ],
        "search_elements": [
            {
                "block_id": "A",
                "element_name": "星间通信系统",
                "keywords_zh": ["星间通信系统", "卫星间通信系统"],
                "keywords_en": ["inter-satellite communication system"],
                "notes": "技术主题锚点",
            },
            {
                "block_id": "B1",
                "element_name": "星间激光通信模块",
                "keywords_zh": ["星间激光通信", "激光通信模块"],
                "keywords_en": ["laser inter-satellite communication"],
                "notes": "优先检索优先权日前公开方案",
            }
        ],
        "suggested_constraints": {
            "applicants": ["示例申请人"],
            "priority_date": "2022-06-01",
            "comparison_document_ids": ["D1", "D2"],
            "notes": ["优先围绕未闭环新增特征补强证据。"],
        },
        "source_dispute_ids": ["TOPUP_A1"],
        "source_feature_ids": ["A1"],
        "missing_items": [],
    }

    content = build_final_report_markdown(report)

    assert "## 7. 补检/检索建议" in content
    assert "### 7.4 检索要素表" in content
    assert "星间激光通信模块" in content
    assert "Block A" in content
    assert "Block B1" in content
    assert "<small style='color:#ccc;'>OR</small>" in content
    assert "当前已存在对比文件编号：D1、D2" in content


def test_build_final_report_markdown_hides_search_followup_section_when_not_needed() -> None:
    report = _sample_report()
    report["search_followup_section"] = {
        "needed": False,
        "status": "complete",
        "objective": "",
        "trigger_reasons": [],
        "gap_summaries": [],
        "search_elements": [],
        "suggested_constraints": {},
        "source_dispute_ids": [],
        "source_feature_ids": [],
        "missing_items": [],
    }

    content = build_final_report_markdown(report)

    assert "## 7. 补检/检索建议" not in content
