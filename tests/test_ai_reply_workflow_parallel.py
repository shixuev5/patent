from threading import Event

from agents.ai_reply.src.nodes.amendment_strategy import AmendmentStrategyNode
from agents.ai_reply.src.nodes.amendment_tracking import AmendmentTrackingNode
from agents.ai_reply.src.nodes.analysis_parallel import AnalysisParallelNode
from agents.ai_reply.src.nodes.dispute_extraction import DisputeExtractionNode
from agents.ai_reply.src.nodes.support_basis_check import SupportBasisCheckNode


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
            "early_rejection_reason": "",
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
