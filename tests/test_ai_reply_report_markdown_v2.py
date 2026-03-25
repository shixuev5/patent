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
                    "claim_id": "1",
                    "claim_text": "一种装置，其特征在于，包括模块A。",
                    "review_mode": "amendment_based",
                    "review_text": "关于权利要求1，经审查，修改后的限定未能克服创造性缺陷 CLAIM_REVIEW_1_END",
                },
                {
                    "claim_id": "2",
                    "claim_text": "根据权利要求1所述的装置，其特征在于，还包括模块B。",
                    "review_mode": "response_based",
                    "review_text": "关于权利要求2，经审查，对申请人意见不予采纳 CLAIM_REVIEW_2_END",
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
    assert "## 4. 当前生效权利要求逐条评述" in content
    assert "## 5. 争论点总表与AI判断" in content
    assert "## 6. 针对申请人意见陈述的答复" in content
    assert "变更特征_ONLY_IN_SECTION3" in content
    assert "CHANGE_REASON_END" in content
    assert "CHANGE_QUOTE_END" in content
    assert "CHANGE_ANALYSIS_END" in content
    assert "CHANGE_FINAL_END" in content
    assert "权项上提" not in content
    assert "说明书补入" in content


def test_build_final_report_markdown_renders_claim_reviews_and_response_reply_blocks() -> None:
    content = build_final_report_markdown(_sample_report())

    assert "CLAIM_REVIEW_1_END" in content
    assert "CLAIM_REVIEW_2_END" in content
    assert "权利要求文本：" in content
    assert "审查评述：" in content
    assert 'class="oar-opinion-block"' in content
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
    assert 'class="oar-verdict-badge oar-verdict-badge-applicant"' in html_doc
    assert "CLAIM_REVIEW_1_END" in html_doc
    assert "CHANGE_FINAL_END" in html_doc
    assert "<blockquote>" not in html_doc
