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
