from __future__ import annotations

from agents.ai_reply.src.nodes.dispute_extraction import DisputeExtractionNode
from agents.common.office_action_structuring.models import ParagraphEvaluation
from agents.common.office_action_structuring.rule_based_extractor import OfficeActionExtractor


def test_office_action_extractor_extracts_reliable_paragraph_fields() -> None:
    markdown_content = """申请号：202211411308.6

# 第 一 次 审 查 意 见 通 知 书

1、权利要求1-3不具备专利法第二十二条第三款规定的创造性。对比文件1公开了相关结构，D2公开了相关控制逻辑，因此不能被授予专利权。
"""

    office_action = OfficeActionExtractor().extract(markdown_content)

    assert office_action.application_number == "202211411308.6"
    assert len(office_action.paragraphs) == 1

    paragraph = office_action.paragraphs[0]
    assert paragraph.claim_ids == ["1", "2", "3"]
    assert paragraph.legal_basis == ["A22.3"]
    assert paragraph.issue_types == ["创造性"]
    assert paragraph.cited_doc_ids == ["D1", "D2"]
    assert paragraph.evaluation == ParagraphEvaluation.NEGATIVE


def test_dispute_extraction_uses_two_stage_pipeline() -> None:
    class _StubLLM:
        def __init__(self):
            self.task_kinds = []

        def invoke_text_json(self, messages, task_kind, temperature):
            self.task_kinds.append(task_kind)
            if task_kind == "oar_applicant_argument_extraction":
                return {
                    "arguments": [
                        {
                            "argument_id": "ARG_1",
                            "claim_ids": ["权利要求1"],
                            "doc_ids": ["D1", "D2"],
                            "feature_text": "环境模拟箱",
                            "argument_type": "fact_dispute",
                            "reasoning": "申请人主张 D1 和 D2 均未公开环境模拟箱，且结合后仍无法得到该配置。",
                            "core_conflict": "D1 和 D2 是否公开环境模拟箱并给出结合启示",
                            "source_quote": "对于区别技术特征2，未在 D1 和 D2 中公开环境模拟箱。",
                        }
                    ]
                }
            if task_kind == "oar_dispute_oa_matching":
                return {
                    "disputes": [
                        {
                            "dispute_id": "",
                            "source_argument_id": "ARG_1",
                            "claim_ids": ["1"],
                            "feature_text": "环境模拟箱",
                            "examiner_opinion": {
                                "type": "mixed_basis",
                                "supporting_docs": [
                                    {"doc_id": "D1", "cited_text": "对比文件1公开了基础测试系统"},
                                    {"doc_id": "D2", "cited_text": "对比文件2公开了控制逻辑"},
                                ],
                                "reasoning": "审查员认为基于 D1 和 D2 的结合并结合常规设置可以得到环境模拟箱。",
                            },
                            "applicant_opinion": {
                                "type": "fact_dispute",
                                "reasoning": "申请人主张 D1 和 D2 均未公开环境模拟箱。",
                                "core_conflict": "D1 和 D2 是否公开环境模拟箱并给出结合启示",
                            },
                        }
                    ]
                }
            raise AssertionError(f"unexpected task_kind: {task_kind}")

    node = DisputeExtractionNode()
    node.llm_service = _StubLLM()

    prepared_materials = {
        "comparison_documents": [
            {"document_id": "D1", "document_number": "CN204101269U", "is_patent": True},
            {"document_id": "D2", "document_number": "文献2", "is_patent": False},
        ],
        "office_action": {
            "paragraphs": [
                {
                    "paragraph_id": "Claim1",
                    "claim_ids": ["1"],
                    "legal_basis": ["A22.3"],
                    "issue_types": ["创造性"],
                    "cited_doc_ids": ["D1", "D2"],
                    "evaluation": "negative",
                    "content": "对于上述区别技术特征，审查员认为 D1 与 D2 结合并辅以常规设置即可得到环境模拟箱。",
                }
            ]
        },
        "response": {
            "content": "申请人认为，对于区别技术特征2，D1 和 D2 均未公开环境模拟箱，且两者结合后仍无法得到本申请方案。"
        },
    }

    applicant_arguments = node._extract_applicant_arguments(prepared_materials)
    disputes = node._match_and_validate_disputes(prepared_materials, applicant_arguments)

    assert node.llm_service.task_kinds == [
        "oar_applicant_argument_extraction",
        "oar_dispute_oa_matching",
    ]
    assert applicant_arguments == [
        {
            "argument_id": "ARG_1",
            "claim_ids": ["1"],
            "doc_ids": ["D1", "D2"],
            "feature_text": "环境模拟箱",
            "argument_type": "fact_dispute",
            "reasoning": "申请人主张 D1 和 D2 均未公开环境模拟箱，且结合后仍无法得到该配置。",
            "core_conflict": "D1 和 D2 是否公开环境模拟箱并给出结合启示",
            "source_quote": "对于区别技术特征2，未在 D1 和 D2 中公开环境模拟箱。",
        }
    ]
    assert len(disputes) == 1
    assert disputes[0]["source_argument_id"] == "ARG_1"
    assert disputes[0]["claim_ids"] == ["1"]
    assert disputes[0]["feature_text"] == "环境模拟箱"
    assert disputes[0]["examiner_opinion"]["type"] == "mixed_basis"
    assert disputes[0]["examiner_opinion"]["supporting_docs"] == [
        {"doc_id": "D1", "cited_text": "对比文件1公开了基础测试系统"},
        {"doc_id": "D2", "cited_text": "对比文件2公开了控制逻辑"},
    ]


def test_applicant_arguments_do_not_dedupe() -> None:
    node = DisputeExtractionNode()
    arguments = node._normalize_applicant_arguments(
        [
            {
                "argument_id": "ARG_1",
                "claim_ids": ["1"],
                "doc_ids": ["D1"],
                "feature_text": "环境模拟箱",
                "argument_type": "fact_dispute",
                "reasoning": "申请人主张 D1 未公开环境模拟箱。",
                "core_conflict": "D1 是否公开环境模拟箱",
                "source_quote": "D1 未公开环境模拟箱。",
            },
            {
                "argument_id": "ARG_2",
                "claim_ids": ["1"],
                "doc_ids": ["D1"],
                "feature_text": "环境模拟箱",
                "argument_type": "fact_dispute",
                "reasoning": "申请人再次强调 D1 未公开环境模拟箱。",
                "core_conflict": "D1 是否公开环境模拟箱",
                "source_quote": "再次说明 D1 未公开环境模拟箱。",
            },
        ]
    )

    assert [item["argument_id"] for item in arguments] == ["ARG_1", "ARG_2"]


def test_oa_matching_prompt_does_not_include_comparison_docs() -> None:
    node = DisputeExtractionNode()
    prompt = node._build_oa_matching_user_prompt(
        {
            "comparison_documents": [
                {"document_id": "D1", "document_number": "CN204101269U", "publication_date": "2015-01-21"}
            ],
            "office_action": {
                "paragraphs": [
                    {
                        "paragraph_id": "Claim1",
                        "claim_ids": ["1"],
                        "legal_basis": ["A22.3"],
                        "issue_types": ["创造性"],
                        "cited_doc_ids": ["D1"],
                        "evaluation": "negative",
                        "content": "审查员认为 D1 公开了相关特征。",
                    }
                ]
            },
        },
        [
            {
                "argument_id": "ARG_1",
                "claim_ids": ["1"],
                "doc_ids": ["D1"],
                "feature_text": "环境模拟箱",
                "argument_type": "fact_dispute",
                "reasoning": "申请人主张 D1 未公开环境模拟箱。",
                "core_conflict": "D1 是否公开环境模拟箱",
                "source_quote": "D1 未公开环境模拟箱。",
            }
        ],
    )

    assert "<comparison_docs>" not in prompt
    assert "<applicant_arguments>" in prompt
    assert "<office_action_paragraphs>" in prompt
