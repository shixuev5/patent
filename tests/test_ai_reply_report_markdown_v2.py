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
            "next_office_action_points": 1,
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
        "amendment_review": {
            "has_claim_amendment": False,
            "added_matter_risk": False,
            "early_rejection_reason": "",
        },
        "disputes": [
            {
                "dispute_id": "DSP_1",
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
        ],
        "next_office_action_notice": {
            "text": "旧整段文本不应作为第 5 部分主展示内容。",
            "items": [
                {
                    "dispute_id": "DSP_1",
                    "claim_ids": ["1", "2"],
                    "feature_text": "争议特征" * 24 + " FEATURE_END",
                    "final_examiner_rejection_reason": "经再次审查，仍可基于 D1 与 D2 的结合维持驳回。",
                }
            ],
        },
    }


def test_build_final_report_markdown_renders_layered_tables_without_truncation() -> None:
    content = build_final_report_markdown(_sample_report())

    assert '<table class="oar-layered-table oar-layered-table-overview">' in content
    assert 'class="oar-layered-cell" colspan="4"' in content
    assert 'class="oar-layered-grid oar-layered-grid-overview"' in content
    assert '<col style="width: 40px;">' in content
    assert '<col style="width: 96px;">' in content
    assert '<col style="width: 132px;">' in content
    assert 'colspan="4"' in content
    assert 'rowspan="2"' not in content
    assert "审查员依据类型" in content
    assert "AI判断" in content
    assert 'class="oar-verdict-badge oar-verdict-badge-applicant"' in content
    assert 'class="oar-verdict-badge oar-verdict-badge-inconclusive"' in content
    assert "FEATURE_END" in content
    assert "EXAM_END" in content
    assert "APP_END" in content
    assert "AI_REASON_END" in content
    assert "QUOTE_END" in content
    assert "ANALYSIS_END" in content
    assert "SECOND_ANALYSIS_END" in content
    assert "..." not in content
    assert "None" not in content


def test_build_final_report_markdown_renders_argument_blocks_for_section_five() -> None:
    content = build_final_report_markdown(_sample_report())

    assert 'class="oar-opinion-block"' in content
    assert "第 1 项｜权利要求 1,2｜争议特征：" in content
    assert "申请人指出：" in content
    assert "审查员认为：" in content
    assert "未提取到申请人详细意见陈述。" in content
    assert "旧整段文本不应作为第 5 部分主展示内容。" not in content
    assert "第 3 次审查意见通知书要点" in content
    assert "## 4. 第 3 次审查意见通知书要点" in content
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
    assert 'colspan="4"' in html_doc
    assert 'class="oar-verdict-badge oar-verdict-badge-applicant"' in html_doc
    assert "<blockquote>" not in html_doc
