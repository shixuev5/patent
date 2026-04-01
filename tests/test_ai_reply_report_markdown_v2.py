from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.ai_reply.src.report_markdown import build_final_report_markdown
from agents.common.rendering.report_render import markdown_to_html_document


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
            "early_rejection_reason": "",
            "change_items": [
                {
                    "feature_id": "F1",
                    "feature_text": "变更特征_ONLY_IN_SECTION3",
                    "feature_before_text": "旧版本片段",
                    "feature_after_text": "变更特征_ONLY_IN_SECTION3",
                    "contains_added_text": True,
                    "target_claim_ids": ["1"],
                    "source_type": "spec",
                    "source_claim_ids": [],
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

    assert "## 3. 权利要求变更表" in content
    assert "## 4. 基于上一轮审查意见的重组评述" in content
    assert "## 5. 争论点总表与AI判断" in content
    assert "## 6. 针对申请人意见陈述的答复" in content
    assert "变更特征_ONLY_IN_SECTION3" in content
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
    assert "说明书补入" in content
    assert "未核查" not in content


def test_build_final_report_markdown_renders_claim_reviews_and_response_reply_blocks() -> None:
    content = build_final_report_markdown(_sample_report())

    assert "CLAIM_REVIEW_1_END" in content
    assert "CLAIM_REVIEW_2_END" in content
    assert "结合证据重组" in content
    assert "来源OA段落：" not in content
    assert "当前权利要求文本：" in content
    assert "重组评述：" in content
    assert 'oar-change-del">CLAIM_BEFORE_ONLY' in content
    assert 'oar-change-add">CLAIM_AFTER_ONLY' in content
    assert 'oar-change-del">REVIEW_BEFORE_ONLY' in content
    assert 'oar-change-add">CLAIM_REVIEW_1_END' in content
    assert 'oar-change-add">根据权利要求1所述的一种装置' in content
    assert "权利要求4" in content
    assert "权利要求5" in content
    assert 'class="oar-opinion-block"' in content
    assert 'class="oar-claim-snapshot-list"' in content
    assert 'class="oar-claim-snapshot-item"' in content
    assert "申请人指出：" in content
    assert "审查员答复：" in content
    assert "未提取到申请人详细意见陈述。" in content
    assert "旧整段文本不应作为第 5 部分主展示内容。" not in content
    assert "\n> " not in content


def test_build_final_report_markdown_html_conversion_preserves_layered_layout() -> None:
    content = build_final_report_markdown(_sample_report())
    html_doc = markdown_to_html_document(
        content,
        title="AI Reply Report",
        enable_mathjax=False,
        enable_echarts=False,
    )

    assert '<table class="oar-layered-table oar-layered-table-overview">' in html_doc
    assert 'class="oar-opinion-block"' in html_doc
    assert 'class="oar-claim-snapshot-list"' in html_doc
    assert 'class="oar-verdict-badge oar-verdict-badge-applicant"' in html_doc
    assert 'class="oar-change-add"' in html_doc
    assert 'class="oar-change-del"' in html_doc
    assert 'class="oar-change-source-tag oar-change-source-tag-spec"' in html_doc
    assert "结合证据重组" in html_doc
    assert "CLAIM_BEFORE_ONLY" in html_doc
    assert "CLAIM_AFTER_ONLY" in html_doc
    assert "CLAIM_REVIEW_1_END" in html_doc
    assert "CHANGE_FINAL_END" in html_doc
    assert "<blockquote>" not in html_doc


def test_build_final_report_markdown_only_shows_current_claim_snapshots_in_sequence() -> None:
    report = {
        "summary": {},
        "amendment_section": {},
        "response_dispute_section": {"items": []},
        "response_reply_section": {"items": []},
        "claim_review_section": {
            "items": [
                {
                    "unit_id": "U1",
                    "unit_type": "reused_oa",
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
                    "unit_type": "reused_oa",
                    "display_claim_ids": ["8", "1"],
                    "title": "权利要求8、权利要求1",
                    "claim_snapshots": [
                        {"claim_id": "8", "claim_before_text": "", "claim_text": "权8文本。"},
                        {"claim_id": "1", "claim_before_text": "", "claim_text": "不应重复展示的权1文本。"},
                    ],
                    "review_before_text": "",
                    "review_text": "权8评述。",
                },
                {
                    "unit_id": "U3",
                    "unit_type": "reused_oa",
                    "display_claim_ids": ["1", "2", "3", "4", "5", "6", "7", "9"],
                    "title": "权利要求1、权利要求2、权利要求3、权利要求4、权利要求5、权利要求6、权利要求7、权利要求9",
                    "claim_snapshots": [
                        {"claim_id": "1", "claim_before_text": "", "claim_text": "不应展示的权1文本。"},
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

    assert "当前权利要求 8,1" not in content
    assert "当前权利要求 1,2,3,4,5,6,7,9" not in content
    assert "权利要求8｜当前权利要求 8｜复用原OA" in content
    assert "权利要求2、权利要求3、权利要求4、权利要求5、权利要求6、权利要求7、权利要求9｜当前权利要求 2,3,4,5,6,7,9｜复用原OA" in content
    assert "不应重复展示的权1文本。" not in content
    assert "不应展示的权1文本。" not in content
    assert "权8文本。" in content
    assert "权9文本。" in content
