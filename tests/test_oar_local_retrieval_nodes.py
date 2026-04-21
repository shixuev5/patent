from pathlib import Path

from agents.ai_reply.src.nodes.common_knowledge_verification import CommonKnowledgeVerificationNode
from agents.ai_reply.src.retrieval_utils import make_query_spec
from agents.ai_reply.src.nodes.data_preparation import DataPreparationNode
from agents.ai_reply.src.state import WorkflowConfig
from agents.common.retrieval import LocalEvidenceRetriever
from config import settings


class _FakeEmbeddingProvider:
    embedding_dim = 6

    def encode_queries(self, texts):
        return [self._encode(text) for text in texts]

    def encode_passages(self, texts):
        return [self._encode(text) for text in texts]

    def _encode(self, text):
        value = str(text or "").lower()
        vector = [0.0] * self.embedding_dim
        for idx, token in enumerate(["文献", "test", "local", "retrieval", "锁定", "structure"]):
            if token in value:
                vector[idx % self.embedding_dim] += 1.0
        if not any(vector):
            vector[0] = 1.0
        norm = sum(item * item for item in vector) ** 0.5
        return [item / norm for item in vector]


def _patch_fake_embeddings(monkeypatch) -> None:
    monkeypatch.setattr(settings, "RETRIEVAL_EMBEDDING_MODEL", "fake/bge-m3")
    monkeypatch.setattr(
        LocalEvidenceRetriever,
        "_build_embedding_provider",
        lambda self: _FakeEmbeddingProvider(),
    )


def test_data_preparation_builds_local_retrieval_index(tmp_path: Path, monkeypatch) -> None:
    _patch_fake_embeddings(monkeypatch)
    monkeypatch.setattr(settings, "LOCAL_RETRIEVAL_ENABLED", True)

    node = DataPreparationNode(config=WorkflowConfig(cache_dir=str(tmp_path / ".cache")))
    office_action = {
        "application_number": "CNAPP1",
        "current_notice_round": 1,
        "paragraphs": [],
        "comparison_documents": [
            {
                "document_id": "D1",
                "document_number": "文献标题A, 作者X",
                "is_patent": False,
                "publication_date": "2020-01-01",
            }
        ],
    }
    parsed_files = [
        {
            "file_type": "comparison_doc",
            "file_path": "doc-a.pdf",
            "markdown_path": "doc-a.md",
            "content": "文献标题A\n这是一段用于测试本地全文检索的非专利文本内容。",
        },
        {"file_type": "response", "content": "答复内容"},
        {"file_type": "claims_current", "content": "权利要求内容"},
    ]

    prepared = node._prepare_materials(office_action=office_action, parsed_files=parsed_files, search_results=[])
    local_meta = prepared.get("local_retrieval", {})
    assert local_meta.get("enabled") is True
    assert int(local_meta.get("chunk_count", 0)) > 0
    assert Path(str(local_meta.get("index_path", ""))).exists()
    assert local_meta.get("embedding_model") == "fake/bge-m3"
    assert "embedding_provider" not in local_meta
    assert local_meta.get("documents") == [
        {
            "doc_id": "D1",
            "source_type": "comparison_document",
            "doc_language": "mixed",
        }
    ]


def test_data_preparation_reports_missing_comparison_documents_with_doc_labels(tmp_path: Path, monkeypatch) -> None:
    _patch_fake_embeddings(monkeypatch)
    monkeypatch.setattr(settings, "LOCAL_RETRIEVAL_ENABLED", False)

    node = DataPreparationNode(config=WorkflowConfig(cache_dir=str(tmp_path / ".cache")))
    office_action = {
        "application_number": "CNAPP1",
        "current_notice_round": 1,
        "paragraphs": [],
        "comparison_documents": [
            {
                "document_id": "D1",
                "document_number": "ISO 12345, 标准文本",
                "is_patent": False,
                "publication_date": None,
            },
            {
                "document_id": "D2",
                "document_number": "CH708501A1",
                "is_patent": True,
                "publication_date": None,
            },
        ],
    }
    parsed_files = [
        {"file_type": "response", "content": "答复内容"},
    ]

    try:
        node._prepare_materials(office_action=office_action, parsed_files=parsed_files, search_results=[])
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected ValueError")

    assert message == "缺少对比文件，请上传：D1（ISO 12345, 标准文本）。"


def test_data_preparation_reports_remaining_missing_documents_after_partial_upload(tmp_path: Path, monkeypatch) -> None:
    _patch_fake_embeddings(monkeypatch)
    monkeypatch.setattr(settings, "LOCAL_RETRIEVAL_ENABLED", False)

    node = DataPreparationNode(config=WorkflowConfig(cache_dir=str(tmp_path / ".cache")))
    office_action = {
        "application_number": "CNAPP1",
        "current_notice_round": 1,
        "paragraphs": [],
        "comparison_documents": [
            {
                "document_id": "D1",
                "document_number": "ISO 12345, 标准文本",
                "is_patent": False,
                "publication_date": None,
            },
            {
                "document_id": "D2",
                "document_number": "IEEE 802.11, 通信协议",
                "is_patent": False,
                "publication_date": None,
            },
        ],
    }
    parsed_files = [
        {
            "file_type": "comparison_doc",
            "file_path": "doc-iso.pdf",
            "markdown_path": "doc-iso.md",
            "content": "ISO 12345\n这是标准文本。",
        },
        {"file_type": "response", "content": "答复内容"},
    ]

    try:
        node._prepare_materials(office_action=office_action, parsed_files=parsed_files, search_results=[])
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected ValueError")

    assert message == "缺少对比文件，请上传：D2（IEEE 802.11, 通信协议）。 当前已上传 1 份对比文件，但仍未匹配到上述文献。"


def test_data_preparation_matches_non_patent_titles_despite_quotes_and_punctuation(tmp_path: Path, monkeypatch) -> None:
    _patch_fake_embeddings(monkeypatch)
    monkeypatch.setattr(settings, "LOCAL_RETRIEVAL_ENABLED", False)

    node = DataPreparationNode(config=WorkflowConfig(cache_dir=str(tmp_path / ".cache")))
    office_action = {
        "application_number": "CNAPP1",
        "current_notice_round": 1,
        "paragraphs": [],
        "comparison_documents": [
            {
                "document_id": "D2",
                "document_number": "“悬索桥桁架加劲梁动力等效成等截面欧拉梁方法”，祝卫亮;葛耀君，《哈尔滨工业大学学报》第 52 卷第 9 期 23-30 页",
                "is_patent": False,
                "publication_date": "2020-09",
            },
            {
                "document_id": "D3",
                "document_number": "“悬挑环形廊桥的气动弹性模型试验”，桂龙辉;谢霁明;林颖孜;张鸿玮，《浙江大学学报(工学版)》，第 51 卷第 11 期 34-42 页",
                "is_patent": False,
                "publication_date": "2017-11-15",
            },
        ],
    }
    parsed_files = [
        {
            "file_type": "comparison_doc",
            "file_path": "doc-d2.pdf",
            "markdown_path": "doc-d2.md",
            "content": "# 悬索桥桁架加劲梁动力等效成等截面欧拉梁方法祝卫亮，葛耀君\n正文",
        },
        {
            "file_type": "comparison_doc",
            "file_path": "doc-d3.pdf",
            "markdown_path": "doc-d3.md",
            "content": "# 悬挑环形廊桥的气动弹性模型试验\n正文",
        },
        {"file_type": "response", "content": "答复内容"},
    ]

    prepared = node._prepare_materials(office_action=office_action, parsed_files=parsed_files, search_results=[])

    comparison_docs = prepared["comparison_documents"]
    assert comparison_docs[0]["document_id"] == "D2"
    assert comparison_docs[0]["data"] == "# 悬索桥桁架加劲梁动力等效成等截面欧拉梁方法祝卫亮，葛耀君\n正文"
    assert comparison_docs[1]["document_id"] == "D3"
    assert comparison_docs[1]["data"] == "# 悬挑环形廊桥的气动弹性模型试验\n正文"


def test_data_preparation_matches_by_author_when_title_missing(tmp_path: Path, monkeypatch) -> None:
    _patch_fake_embeddings(monkeypatch)
    monkeypatch.setattr(settings, "LOCAL_RETRIEVAL_ENABLED", False)

    node = DataPreparationNode(config=WorkflowConfig(cache_dir=str(tmp_path / ".cache")))
    office_action = {
        "application_number": "CNAPP1",
        "current_notice_round": 1,
        "paragraphs": [],
        "comparison_documents": [
            {
                "document_id": "D2",
                "document_number": "桥梁风场研究方法, 张三;李四，《空气动力学学报》",
                "is_patent": False,
                "publication_date": None,
            }
        ],
    }
    parsed_files = [
        {
            "file_type": "comparison_doc",
            "file_path": "doc-d2.pdf",
            "file_name": "现场测试记录.pdf",
            "markdown_path": "doc-d2.md",
            "content": "作者：李四 王五\n摘要：本文讨论桥梁风场问题。",
        }
    ]

    prepared = node._prepare_materials(office_action=office_action, parsed_files=parsed_files, search_results=[])
    assert prepared["comparison_documents"][0]["data"] == "作者：李四 王五\n摘要：本文讨论桥梁风场问题。"


def test_data_preparation_uses_second_condition_to_break_ties(tmp_path: Path, monkeypatch) -> None:
    _patch_fake_embeddings(monkeypatch)
    monkeypatch.setattr(settings, "LOCAL_RETRIEVAL_ENABLED", False)

    node = DataPreparationNode(config=WorkflowConfig(cache_dir=str(tmp_path / ".cache")))
    office_action = {
        "application_number": "CNAPP1",
        "current_notice_round": 1,
        "paragraphs": [],
        "comparison_documents": [
            {
                "document_id": "D2",
                "document_number": "模型试验方法, 张三;李四，《空气动力学学报》",
                "is_patent": False,
                "publication_date": None,
            }
        ],
    }
    parsed_files = [
        {
            "file_type": "comparison_doc",
            "file_path": "doc-a.pdf",
            "file_name": "doc-a.pdf",
            "markdown_path": "doc-a.md",
            "content": "文中提到模型试验方法。\n作者：王五\n期刊：空气动力学学报",
        },
        {
            "file_type": "comparison_doc",
            "file_path": "doc-b.pdf",
            "file_name": "doc-b.pdf",
            "markdown_path": "doc-b.md",
            "content": "文中提到模型试验方法。\n作者：李四\n期刊：其他期刊",
        },
    ]

    prepared = node._prepare_materials(office_action=office_action, parsed_files=parsed_files, search_results=[])
    assert prepared["comparison_documents"][0]["data"] == "文中提到模型试验方法。\n作者：李四\n期刊：其他期刊"


def test_data_preparation_falls_back_to_upload_order_when_all_docs_unmatched(tmp_path: Path, monkeypatch) -> None:
    _patch_fake_embeddings(monkeypatch)
    monkeypatch.setattr(settings, "LOCAL_RETRIEVAL_ENABLED", False)

    node = DataPreparationNode(config=WorkflowConfig(cache_dir=str(tmp_path / ".cache")))
    office_action = {
        "application_number": "CNAPP1",
        "current_notice_round": 1,
        "paragraphs": [],
        "comparison_documents": [
            {
                "document_id": "D2",
                "document_number": "文献甲, 张三，《期刊甲》",
                "is_patent": False,
                "publication_date": None,
            },
            {
                "document_id": "D3",
                "document_number": "文献乙, 李四，《期刊乙》",
                "is_patent": False,
                "publication_date": None,
            },
        ],
    }
    parsed_files = [
        {
            "file_type": "comparison_doc",
            "file_path": "first.pdf",
            "file_name": "first.pdf",
            "markdown_path": "first.md",
            "content": "完全无关内容A",
        },
        {
            "file_type": "comparison_doc",
            "file_path": "second.pdf",
            "file_name": "second.pdf",
            "markdown_path": "second.md",
            "content": "完全无关内容B",
        },
    ]

    prepared = node._prepare_materials(office_action=office_action, parsed_files=parsed_files, search_results=[])
    assert prepared["comparison_documents"][0]["data"] == "完全无关内容A"
    assert prepared["comparison_documents"][1]["data"] == "完全无关内容B"


def test_extract_non_patent_metadata_omits_title_from_authors_and_trims_journal() -> None:
    node = DataPreparationNode()

    d2 = node._extract_non_patent_metadata(
        "“悬索桥桁架加劲梁动力等效成等截面欧拉梁方法”，祝卫亮;葛耀君，《哈尔滨工业大学学报》第 52 卷第 9 期 23-30 页"
    )
    assert d2 == {
        "title": "悬索桥桁架加劲梁动力等效成等截面欧拉梁方法",
        "authors": ["祝卫亮", "葛耀君"],
        "journal": "哈尔滨工业大学学报",
    }

    d3 = node._extract_non_patent_metadata(
        "“悬挑环形廊桥的气动弹性模型试验”，桂龙辉;谢霁明;林颖孜;张鸿玮，《浙江大学学报(工学版)》，第 51 卷第 11 期 34-42 页"
    )
    assert d3 == {
        "title": "悬挑环形廊桥的气动弹性模型试验",
        "authors": ["桂龙辉", "谢霁明", "林颖孜", "张鸿玮"],
        "journal": "浙江大学学报(工学版)",
    }


def test_extract_non_patent_metadata_handles_plain_comma_separated_references() -> None:
    node = DataPreparationNode()

    case1 = node._extract_non_patent_metadata(
        "基于LIN总线的雨刮电机自动控制系统的设计,姜义成 等，计算机测量与控制，第12期，第3970-3972页，2014年12月"
    )
    assert case1 == {
        "title": "基于LIN总线的雨刮电机自动控制系统的设计",
        "authors": ["姜义成"],
        "journal": "计算机测量与控制",
    }

    case2 = node._extract_non_patent_metadata(
        "基于控制器局域网的悬浮控制器调试监测系统，曾颖丰，湖南工业大学学报，第04期，2018年7月"
    )
    assert case2 == {
        "title": "基于控制器局域网的悬浮控制器调试监测系统",
        "authors": ["曾颖丰"],
        "journal": "湖南工业大学学报",
    }

    case3 = node._extract_non_patent_metadata(
        "中低速磁浮悬浮架装配精度研究，魏德豪，中国优秀博硕士学位论文全文数据库(硕士)工程科技Ⅱ辑，第07期，2017年7月"
    )
    assert case3 == {
        "title": "中低速磁浮悬浮架装配精度研究",
        "authors": ["魏德豪"],
        "journal": "中国优秀博硕士学位论文全文数据库(硕士)工程科技Ⅱ辑",
    }


def test_common_knowledge_uses_compact_evidence_cards(monkeypatch) -> None:
    _patch_fake_embeddings(monkeypatch)

    class StubLLM:
        def __init__(self):
            self.messages = []

        def invoke_text_json(self, messages, task_kind, temperature):
            self.messages.append(messages)
            return {
                "assessment": {
                    "verdict": "EXAMINER_CORRECT",
                    "reasoning": "证据显示该技术属于常见手段。",
                    "confidence": 0.84,
                    "examiner_rejection_rationale": "",
                },
                "evidence": [
                    {
                        "doc_id": "EXT1",
                        "quote": "公开文献中已有该技术路径。",
                        "location": "摘要",
                        "analysis": "直接公开",
                    }
                ],
            }

    class StubAggregator:
        def search_evidence(self, queries, priority_date, limit=8):
            long_snippet = "公开资料" * 600
            return (
                [
                    {
                        "doc_id": "EXT1",
                        "source_type": "openalex",
                        "title": "文献1",
                        "url": "https://example.com/1",
                        "snippet": long_snippet,
                        "published": "2019-01-01",
                    },
                    {
                        "doc_id": "EXT2",
                        "source_type": "tavily",
                        "title": "文献2",
                        "url": "https://example.com/2",
                        "snippet": long_snippet,
                        "published": "2018-01-01",
                    },
                ],
                ["openalex", "tavily"],
                {"retrieval": {}},
            )

    node = CommonKnowledgeVerificationNode()
    node.llm_service = StubLLM()
    node.external_evidence_aggregator = StubAggregator()
    monkeypatch.setattr(
        node,
        "_build_engine_queries",
        lambda dispute, claim_text, priority_date: {
            "openalex": [make_query_spec("query-1", "boolean", "anchor")],
            "tavily": [make_query_spec("query-2", "web", "technical")],
        },
    )

    disputes = [
        {
            "dispute_id": "DSP_CK_1",
            "claim_ids": ["1"],
            "feature_text": "移动定位架的锁定结构",
            "examiner_opinion": {"type": "common_knowledge_based", "reasoning": "本领域常规手段"},
            "applicant_opinion": {"type": "logic_dispute", "reasoning": "缺乏证据", "core_conflict": "是否公知"},
        }
    ]
    prepared = {
        "original_patent": {"data": {"claims": [{"claim_text": "一种定位装置"}]}},
        "comparison_documents": [],
        "local_retrieval": {"enabled": False},
    }
    claims = [{"claim_id": "1", "claim_text": "一种定位装置"}]

    assessments = node._verify_common_knowledge(disputes, prepared, claims)
    assert len(assessments) == 1
    trace = assessments[0]["trace"]
    assert "local_retrieval" in trace
    assert "lexical_hits" in trace["local_retrieval"]
    assert "dense_hits" in trace["local_retrieval"]

    call_messages = node.llm_service.messages[0]
    evidence_messages = [item["content"] for item in call_messages if "证据卡" in item.get("content", "")]
    assert evidence_messages
    assert all(len(msg) < 700 for msg in evidence_messages)
