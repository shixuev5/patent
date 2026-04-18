from threading import Event

from agents.ai_reply.main import create_workflow
from agents.ai_reply.src.nodes.claim_review_drafting import ClaimReviewDraftingNode
from agents.ai_reply.src.nodes.amendment_strategy import AmendmentStrategyNode
from agents.ai_reply.src.nodes.amendment_tracking import AmendmentTrackingNode
from agents.ai_reply.src.nodes.analysis_parallel import AnalysisParallelNode
from agents.ai_reply.src.nodes.common_knowledge_verification import CommonKnowledgeVerificationNode
from agents.ai_reply.src.nodes.data_preparation import DataPreparationNode
from agents.ai_reply.src.nodes.document_processing import DocumentProcessingNode
from agents.ai_reply.src.nodes.dispute_extraction import DisputeExtractionNode
from agents.ai_reply.src.nodes.evidence_verification import EvidenceVerificationNode
from agents.ai_reply.src.nodes.final_report_render import FinalReportRenderNode
from agents.ai_reply.src.nodes.patent_retrieval import PatentRetrievalNode
from agents.ai_reply.src.nodes.rejection_drafting import RejectionDraftingNode
from agents.ai_reply.src.nodes.report_generation import ReportGenerationNode
from agents.ai_reply.src.nodes.search_followup_generation import SearchFollowupGenerationNode
from agents.ai_reply.src.nodes.support_basis_check import SupportBasisCheckNode
from agents.ai_reply.src.nodes.topup_search_verification import TopupSearchVerificationNode
from agents.ai_reply.src.nodes.verification_join import VerificationJoinNode


def test_analysis_parallel_runs_amendment_and_dispute_branches_in_parallel(monkeypatch) -> None:
    amendment_started = Event()
    dispute_started = Event()

    def _fake_amendment_tracking(self, state):
        amendment_started.set()
        assert dispute_started.wait(timeout=1.0)
        return {
            "status": "completed",
            "claims_old_structured": [],
            "claims_effective_structured": [],
            "has_claim_amendment": False,
            "claim_alignments": [],
            "substantive_amendments": [],
            "structural_adjustments": [],
        }

    def _fake_dispute_extraction(self, state):
        dispute_started.set()
        assert amendment_started.wait(timeout=1.0)
        return {
            "status": "completed",
            "disputes": [],
        }

    monkeypatch.setattr(AmendmentTrackingNode, "__call__", _fake_amendment_tracking)
    monkeypatch.setattr(
        SupportBasisCheckNode,
        "__call__",
        lambda self, state: {
            "status": "completed",
            "support_findings": [],
            "added_matter_risk": False,
            "added_matter_risk_summary": "",
        },
    )
    monkeypatch.setattr(
        AmendmentStrategyNode,
        "__call__",
        lambda self, state: {"status": "completed", "reuse_oa_tasks": [], "topup_tasks": []},
    )
    monkeypatch.setattr(DisputeExtractionNode, "__call__", _fake_dispute_extraction)

    node = AnalysisParallelNode()
    updates = node(
        {
            "prepared_materials": {
                "office_action": {"current_notice_round": 1, "paragraphs": []},
                "response": {"content": "response"},
            }
        }
    )

    assert updates["status"] == "completed"
    assert amendment_started.is_set()
    assert dispute_started.is_set()
    assert updates["topup_tasks"] == []
    assert updates["disputes"] == []


def test_workflow_continues_verification_when_added_matter_risk_exists(monkeypatch) -> None:
    calls: list[str] = []

    def _record(name: str, payload: dict | None = None, *, include_status: bool = True):
        def _inner(self, state):
            calls.append(name)
            result = dict(payload or {})
            if include_status:
                result["status"] = "completed"
            return result

        return _inner

    monkeypatch.setattr(DocumentProcessingNode, "__call__", _record("document_processing"))
    monkeypatch.setattr(PatentRetrievalNode, "__call__", _record("patent_retrieval"))
    monkeypatch.setattr(DataPreparationNode, "__call__", _record("data_preparation"))
    monkeypatch.setattr(
        AnalysisParallelNode,
        "__call__",
        _record(
            "analysis_parallel",
            {
                "added_matter_risk": True,
                "added_matter_risk_summary": "存在修改超范围风险",
                "topup_tasks": [{"task_id": "A1", "feature_text": "新增特征"}],
                "disputes": [
                    {
                        "dispute_id": "DSP1",
                        "origin": "response_dispute",
                        "feature_text": "争议特征",
                        "claim_ids": ["1"],
                        "examiner_opinion": {"type": "mixed_basis", "supporting_docs": [], "reasoning": ""},
                        "applicant_opinion": {"type": "fact_dispute", "reasoning": "", "core_conflict": ""},
                    }
                ],
            },
        ),
    )
    monkeypatch.setattr(
        EvidenceVerificationNode,
        "__call__",
        _record("evidence_verification", include_status=False),
    )
    monkeypatch.setattr(
        CommonKnowledgeVerificationNode,
        "__call__",
        _record("common_knowledge_verification", include_status=False),
    )
    monkeypatch.setattr(
        TopupSearchVerificationNode,
        "__call__",
        _record("topup_search_verification", include_status=False),
    )
    monkeypatch.setattr(VerificationJoinNode, "__call__", _record("verification_join"))
    monkeypatch.setattr(RejectionDraftingNode, "__call__", _record("rejection_drafting"))
    monkeypatch.setattr(ClaimReviewDraftingNode, "__call__", _record("claim_review_drafting"))
    monkeypatch.setattr(
        SearchFollowupGenerationNode,
        "__call__",
        _record("search_followup_generation", {"search_followup_section": {"needed": True}}),
    )
    monkeypatch.setattr(
        ReportGenerationNode,
        "__call__",
        _record("report_generation", {"final_report": {"status": "completed"}}),
    )
    monkeypatch.setattr(
        FinalReportRenderNode,
        "__call__",
        _record("final_report_render", {"final_report_artifacts": {"md": "out.md", "pdf": "out.pdf"}}),
    )

    workflow = create_workflow()
    result = workflow.invoke({})
    result_dict = result if isinstance(result, dict) else result.model_dump()

    assert result_dict["status"] == "completed"
    assert "evidence_verification" in calls
    assert "common_knowledge_verification" in calls
    assert "topup_search_verification" in calls
    assert calls.index("analysis_parallel") < calls.index("verification_join")
    assert calls.index("claim_review_drafting") < calls.index("search_followup_generation")
    assert calls.index("search_followup_generation") < calls.index("report_generation")
    assert calls[-1] == "final_report_render"
