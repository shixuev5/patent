from pathlib import Path

from agents.ai_reply.src.nodes.common_knowledge_verification import CommonKnowledgeVerificationNode
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
    monkeypatch.setattr(settings, "LOCAL_RETRIEVAL_EMBEDDING_MODEL", "fake/bge-m3")
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
                        "source_type": "tavily_web",
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
        lambda dispute, claim_text, priority_date: {"openalex": ["query-1"], "tavily": ["query-2"]},
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
