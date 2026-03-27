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
    assert office_action.current_notice_round == 1
    assert len(office_action.paragraphs) == 1

    paragraph = office_action.paragraphs[0]
    assert paragraph.claim_ids == ["1", "2", "3"]
    assert paragraph.legal_basis == ["A22.3"]
    assert paragraph.issue_types == ["创造性"]
    assert paragraph.cited_doc_ids == ["D1", "D2"]
    assert paragraph.evaluation == ParagraphEvaluation.NEGATIVE


def test_office_action_extractor_keeps_body_after_internal_heading() -> None:
    markdown_content = """申请号：202211444126.9

# 第 一 次 审 查 意 见 通 知 书

本申请要求保护一种载人火车控制系统。

# 权利要求 1-7 不具备专利法第二十二条第三款规定的创造性

1、独立权利要求1相对于对比文件1和对比文件2不具备创造性，因此不能被授予专利权。
2、权利要求2-3是从属权利要求，在引用的权利要求不具备创造性的基础上，也不具备创造性。
"""

    office_action = OfficeActionExtractor().extract(markdown_content)

    assert office_action.current_notice_round == 1
    assert len(office_action.paragraphs) == 2
    assert office_action.paragraphs[0].claim_ids == ["1"]
    assert office_action.paragraphs[0].cited_doc_ids == ["D1", "D2"]
    assert office_action.paragraphs[1].claim_ids == ["2", "3"]


def test_office_action_extractor_uses_latest_notice_body_for_round_and_comparison_docs() -> None:
    markdown_content = """申请号：202211444126.9

# 第 一 次 审 查 意 见 通 知 书

1、权利要求1相对于对比文件3(CN201010101U)不具备创造性。

# 第二次审查意见通知书

1、权利要求1相对于对比文件1(CN101010101A)不具备创造性。另，对比文件1(CN101010101A)已经公开对应基础结构；对比文件2(ISO 12345《环境测试方法（修订版）》)公开了相关试验条件。
"""

    office_action = OfficeActionExtractor().extract(markdown_content)

    assert office_action.current_notice_round == 2
    assert [doc.document_id for doc in office_action.comparison_documents] == ["D1", "D2"]
    assert office_action.comparison_documents[0].document_number == "CN101010101A"
    assert office_action.comparison_documents[1].document_number == "ISO 12345《环境测试方法（修订版）》"
    assert office_action.paragraphs[0].cited_doc_ids == ["D1", "D2"]


def test_office_action_extractor_extracts_application_number_from_online_word_markdown_header() -> None:
    markdown_content = """<table><tr><td colspan="2">申请号或专利号:202110546646.X 发文序号:</td></tr></table>

# 第 二 次 审 查 意 见 通 知 书

1、权利要求1不具备专利法第二十二条第三款规定的创造性。
"""

    office_action = OfficeActionExtractor().extract(markdown_content)

    assert office_action.application_number == "202110546646.X"
    assert office_action.current_notice_round == 2


def test_office_action_extractor_falls_back_to_table_when_body_has_no_comparison_doc() -> None:
    markdown_content = """申请号：202211444126.9

# 第二次审查意见通知书

对比文件(其编号在今后的审查过程中继续沿用)：
<table>
<tr><td>序号</td><td>对比文件</td><td>公开日</td></tr>
<tr><td>1</td><td>CN104567890A</td><td>2015-01-01</td></tr>
<tr><td>2</td><td>ISO 9988</td><td></td></tr>
</table>

1、权利要求1不具备创造性。
"""

    office_action = OfficeActionExtractor().extract(markdown_content)

    assert office_action.current_notice_round == 2
    assert [doc.document_id for doc in office_action.comparison_documents] == ["D1", "D2"]
    assert office_action.comparison_documents[0].document_number == "CN104567890A"
    assert office_action.comparison_documents[0].publication_date == "2015-01-01"
    assert office_action.comparison_documents[1].document_number == "ISO 9988"
    assert office_action.comparison_documents[1].publication_date is None


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
    assert disputes[0]["origin"] == "response_dispute"
    assert disputes[0]["source_feature_id"] == ""
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


def test_applicant_argument_prompt_requires_skipping_conclusory_remaining_dependent_claim_statements() -> None:
    node = DisputeExtractionNode()
    prompt = node._build_applicant_argument_system_prompt()

    assert "其余/剩余从属权利要求也具备创造性" in prompt
    assert "必须忽略，不得提取" in prompt


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
