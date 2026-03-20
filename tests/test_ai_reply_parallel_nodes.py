import threading
import time

from agents.ai_reply.src.nodes.common_knowledge_verification import CommonKnowledgeVerificationNode
from agents.ai_reply.src.nodes.evidence_verification import EvidenceVerificationNode
from agents.ai_reply.src.nodes.topup_search_verification import TopupSearchVerificationNode
from config import settings


def _build_parallel_tracker():
    lock = threading.Lock()
    tracker = {"active": 0, "max_active": 0}

    def enter():
        with lock:
            tracker["active"] += 1
            tracker["max_active"] = max(tracker["max_active"], tracker["active"])

    def leave():
        with lock:
            tracker["active"] -= 1

    return tracker, enter, leave


def test_evidence_verification_runs_disputes_in_parallel(monkeypatch) -> None:
    node = EvidenceVerificationNode()
    monkeypatch.setattr(settings, "OAR_MAX_CONCURRENCY", 3)
    tracker, enter, leave = _build_parallel_tracker()

    def _fake_verify_single_dispute(**kwargs):
        enter()
        try:
            time.sleep(0.05)
            dispute = kwargs["dispute"]
            return {
                "dispute_id": dispute.get("dispute_id", ""),
                "assessment": {"verdict": "INCONCLUSIVE", "confidence": 0.1, "reasoning": "", "examiner_rejection_reason": ""},
                "evidence": [],
            }
        finally:
            leave()

    monkeypatch.setattr(node, "_verify_single_dispute", _fake_verify_single_dispute)

    disputes = [
        {
            "dispute_id": "DSP_1",
            "claim_ids": ["1"],
            "feature_text": "特征1",
            "examiner_opinion": {"type": "document_based", "supporting_docs": [{"doc_id": "D1"}]},
            "applicant_opinion": {"type": "fact_dispute"},
        },
        {
            "dispute_id": "DSP_2",
            "claim_ids": ["1"],
            "feature_text": "特征2",
            "examiner_opinion": {"type": "document_based", "supporting_docs": [{"doc_id": "D1"}]},
            "applicant_opinion": {"type": "fact_dispute"},
        },
        {
            "dispute_id": "DSP_3",
            "claim_ids": ["1"],
            "feature_text": "特征3",
            "examiner_opinion": {"type": "document_based", "supporting_docs": [{"doc_id": "D2"}]},
            "applicant_opinion": {"type": "fact_dispute"},
        },
    ]
    prepared_materials = {
        "original_patent": {"data": {"claims": [{"claim_text": "一种装置"}]}},
        "comparison_documents": [
            {"document_id": "D1", "is_patent": False, "data": "D1内容"},
            {"document_id": "D2", "is_patent": False, "data": "D2内容"},
        ],
    }

    result = node._verify_evidence(disputes, prepared_materials)
    assert len(result) == 3
    assert tracker["max_active"] >= 2


def test_evidence_verification_normalizes_doc_groups_for_prefix_cache() -> None:
    node = EvidenceVerificationNode()

    disputes = [
        {
            "examiner_opinion": {
                "supporting_docs": [{"doc_id": "D2"}, {"doc_id": "D1"}, {"doc_id": "D2"}]
            }
        },
        {
            "examiner_opinion": {
                "supporting_docs": [{"doc_id": "D1"}, {"doc_id": "D2"}]
            }
        },
    ]

    grouped = node._group_disputes_by_docs(disputes)

    assert list(grouped.keys()) == [("D1", "D2")]
    assert len(grouped[("D1", "D2")]) == 2


def test_evidence_verification_builds_doc_level_cache_markers() -> None:
    node = EvidenceVerificationNode()
    docs_context = [
        {"doc_id": "D1", "document_number": "DOC1", "content": "内容1"},
        {"doc_id": "D2", "document_number": "DOC2", "content": "内容2"},
        {"doc_id": "D3", "document_number": "DOC3", "content": "内容3"},
        {"doc_id": "D4", "document_number": "DOC4", "content": "内容4"},
    ]

    messages = node._build_prefix_messages(docs_context)

    assert messages[0]["role"] == "system"
    for idx in range(1, 4):
        content = messages[idx]["content"]
        assert isinstance(content, list)
        assert content[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in messages[4]["content"][0]


def test_evidence_verification_routes_long_non_patent_docs_to_retrieval_context() -> None:
    node = EvidenceVerificationNode()
    comparison_doc_map = {
        "D1": {
            "document_id": "D1",
            "document_number": "文献1",
            "is_patent": False,
            "data": "A" * (node._FULL_DOC_CONTEXT_LIMIT + 50),
        },
        "D2": {
            "document_id": "D2",
            "document_number": "CN123",
            "is_patent": True,
            "data": {"description": {"detailed_description": "专利内容"}},
        },
    }

    docs_context, retrieval_docs, missing_doc_ids = node._build_docs_context(("D1", "D2"), comparison_doc_map)

    assert missing_doc_ids == []
    assert [item["doc_id"] for item in docs_context] == ["D2"]
    assert [item["doc_id"] for item in retrieval_docs] == ["D1"]
    assert len(retrieval_docs[0]["content"]) > node._FULL_DOC_CONTEXT_LIMIT


def test_evidence_verification_builds_non_patent_retrieval_messages_without_cache_markers(monkeypatch) -> None:
    node = EvidenceVerificationNode()
    retrieval_docs = [
        {
            "doc_id": "D1",
            "document_number": "文献1",
            "content": "原文" * 100,
        }
    ]

    monkeypatch.setattr(
        node,
        "_search_non_patent_evidence_cards",
        lambda doc_id, queries, local_retriever: [
            {
                "doc_id": doc_id,
                "quote": "证据片段",
                "location": "chunk:1-2",
                "analysis": "定位到相关段落",
            }
        ],
    )

    messages = node._build_long_non_patent_messages(
        dispute={"feature_text": "特征A"},
        claim_text="权利要求1: 一种装置",
        examiner_opinion={"reasoning": "D1公开了该特征"},
        applicant_opinion={"reasoning": "D1未公开", "core_conflict": "是否公开特征A"},
        retrieval_docs=retrieval_docs,
        local_retriever=None,
    )

    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert isinstance(messages[0]["content"], str)
    assert "cache_control" not in messages[0]
    assert "evidence_cards" in messages[0]["content"]


def test_evidence_verification_selects_warmup_job_by_prefix_reuse() -> None:
    node = EvidenceVerificationNode()

    jobs = [
        node._build_verification_job(
            dispute={"dispute_id": "A1"},
            claims=[],
            doc_group=("D1", "D2"),
            missing_doc_ids=[],
            prefix_messages=[],
            docs_context=[
                {"doc_id": "D1", "document_number": "DOC1", "content": "A" * 100},
                {"doc_id": "D2", "document_number": "DOC2", "content": "B" * 100},
            ],
        ),
        node._build_verification_job(
            dispute={"dispute_id": "A2"},
            claims=[],
            doc_group=("D1", "D2"),
            missing_doc_ids=[],
            prefix_messages=[],
            docs_context=[
                {"doc_id": "D1", "document_number": "DOC1", "content": "A" * 100},
                {"doc_id": "D2", "document_number": "DOC2", "content": "B" * 100},
            ],
        ),
        node._build_verification_job(
            dispute={"dispute_id": "B1"},
            claims=[],
            doc_group=("D1", "D3"),
            missing_doc_ids=[],
            prefix_messages=[],
            docs_context=[
                {"doc_id": "D1", "document_number": "DOC1", "content": "A" * 100},
                {"doc_id": "D3", "document_number": "DOC3", "content": "C" * 100},
            ],
        ),
        node._build_verification_job(
            dispute={"dispute_id": "C1"},
            claims=[],
            doc_group=("D4",),
            missing_doc_ids=[],
            prefix_messages=[],
            docs_context=[
                {"doc_id": "D4", "document_number": "DOC4", "content": "D" * 100},
            ],
        ),
    ]

    assert node._select_warmup_job_index(jobs) == 0


def test_evidence_verification_prioritizes_pending_job_with_best_cache_hit() -> None:
    node = EvidenceVerificationNode()

    jobs = [
        node._build_verification_job(
            dispute={"dispute_id": "A1"},
            claims=[],
            doc_group=("D1", "D2"),
            missing_doc_ids=[],
            prefix_messages=[],
            docs_context=[
                {"doc_id": "D1", "document_number": "DOC1", "content": "A" * 100},
                {"doc_id": "D2", "document_number": "DOC2", "content": "B" * 100},
            ],
        ),
        node._build_verification_job(
            dispute={"dispute_id": "A2"},
            claims=[],
            doc_group=("D1", "D2"),
            missing_doc_ids=[],
            prefix_messages=[],
            docs_context=[
                {"doc_id": "D1", "document_number": "DOC1", "content": "A" * 100},
                {"doc_id": "D2", "document_number": "DOC2", "content": "B" * 100},
            ],
        ),
        node._build_verification_job(
            dispute={"dispute_id": "B1"},
            claims=[],
            doc_group=("D1", "D3"),
            missing_doc_ids=[],
            prefix_messages=[],
            docs_context=[
                {"doc_id": "D1", "document_number": "DOC1", "content": "A" * 100},
                {"doc_id": "D3", "document_number": "DOC3", "content": "C" * 100},
            ],
        ),
        node._build_verification_job(
            dispute={"dispute_id": "C1"},
            claims=[],
            doc_group=("D4",),
            missing_doc_ids=[],
            prefix_messages=[],
            docs_context=[
                {"doc_id": "D4", "document_number": "DOC4", "content": "D" * 100},
            ],
        ),
    ]

    available_prefixes = node._job_cache_prefixes(jobs[0])
    pending_indices = [1, 2, 3]

    assert node._select_next_job_index(pending_indices, jobs, available_prefixes) == 1


def test_common_knowledge_verification_runs_disputes_in_parallel(monkeypatch) -> None:
    node = CommonKnowledgeVerificationNode()
    monkeypatch.setattr(settings, "OAR_MAX_CONCURRENCY", 3)
    tracker, enter, leave = _build_parallel_tracker()

    def _fake_evaluate_common_knowledge_dispute(**kwargs):
        enter()
        try:
            time.sleep(0.05)
            dispute = kwargs["dispute"]
            return {
                "dispute_id": dispute.get("dispute_id", ""),
                "assessment": {"verdict": "INCONCLUSIVE", "confidence": 0.1, "reasoning": "", "examiner_rejection_reason": ""},
                "evidence": [],
                "trace": {},
            }
        finally:
            leave()

    monkeypatch.setattr(node, "_evaluate_common_knowledge_dispute", _fake_evaluate_common_knowledge_dispute)

    disputes = [
        {
            "dispute_id": "DSP_CK_1",
            "claim_ids": ["1"],
            "feature_text": "特征1",
            "examiner_opinion": {"type": "common_knowledge_based"},
            "applicant_opinion": {"type": "logic_dispute"},
        },
        {
            "dispute_id": "DSP_CK_2",
            "claim_ids": ["1"],
            "feature_text": "特征2",
            "examiner_opinion": {"type": "mixed_basis"},
            "applicant_opinion": {"type": "logic_dispute"},
        },
    ]
    prepared_materials = {
        "original_patent": {"data": {"claims": [{"claim_text": "一种装置"}]}},
        "comparison_documents": [],
        "local_retrieval": {"enabled": False},
    }

    result = node._verify_common_knowledge(disputes, prepared_materials)
    assert len(result) == 2
    assert tracker["max_active"] >= 2


def test_topup_search_verification_runs_tasks_in_parallel(monkeypatch) -> None:
    node = TopupSearchVerificationNode()
    monkeypatch.setattr(settings, "OAR_MAX_CONCURRENCY", 3)
    tracker, enter, leave = _build_parallel_tracker()

    def _fake_evaluate_task(**kwargs):
        enter()
        try:
            time.sleep(0.05)
            task = kwargs["task"]
            task_id = task.get("task_id", "")
            dispute_id = f"TOPUP_{task_id}"
            dispute = {
                "dispute_id": dispute_id,
                "claim_ids": ["1"],
                "feature_text": task.get("feature_text", ""),
                "examiner_opinion": {"type": "document_based", "supporting_docs": []},
                "applicant_opinion": {"type": "fact_dispute"},
            }
            assessment = {
                "dispute_id": dispute_id,
                "assessment": {"verdict": "INCONCLUSIVE", "confidence": 0.1, "reasoning": "", "examiner_rejection_reason": ""},
                "evidence": [],
                "trace": {},
            }
            return dispute, assessment
        finally:
            leave()

    monkeypatch.setattr(node, "_evaluate_task", _fake_evaluate_task)

    topup_tasks = [
        {"task_id": "F1", "feature_text": "新增特征1", "claim_ids": ["1"]},
        {"task_id": "F2", "feature_text": "新增特征2", "claim_ids": ["1"]},
        {"task_id": "F3", "feature_text": "新增特征3", "claim_ids": ["1"]},
    ]
    prepared_materials = {
        "original_patent": {"data": {"claims": [{"claim_text": "一种装置"}]}},
        "comparison_documents": [],
        "local_retrieval": {"enabled": False},
    }

    result = node._verify_topup(topup_tasks, prepared_materials)
    assert len(result.get("disputes", [])) == 3
    assert len(result.get("evidence_assessments", [])) == 3
    assert tracker["max_active"] >= 2
