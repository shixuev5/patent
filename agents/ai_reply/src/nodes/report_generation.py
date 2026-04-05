"""
报告生成节点
生成最终的 JSON 格式报告。
"""

from difflib import SequenceMatcher
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from loguru import logger

from agents.ai_reply.src.utils import get_node_cache
from agents.common.utils.serialization import item_get, to_jsonable


def _to_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return {}


class ReportGenerationNode:
    """报告生成节点"""

    def __init__(self, config=None):
        self.config = config

    def __call__(self, state):
        logger.info("开始生成最终报告")

        updates = {
            "current_node": "report_generation",
            "status": "running",
            "progress": 95.0,
        }

        try:
            cache = get_node_cache(self.config, "report_generation")
            report = cache.run_step("generate_report_v17", self._generate_report, state)
            output_path = self._save_report(report, state)

            updates["final_report"] = report
            updates["progress"] = 97.0
            updates["status"] = "completed"
            logger.info(f"报告已生成: {output_path}")
        except Exception as exc:
            logger.error(f"报告生成节点执行失败: {exc}")
            updates["errors"] = [{
                "node_name": "report_generation",
                "error_message": str(exc),
                "error_type": "report_generation_error",
            }]
            updates["status"] = "failed"

        return updates

    def _generate_report(self, state) -> Dict[str, Any]:
        evidence_map = self._build_evidence_map(item_get(state, "evidence_assessments", []))
        drafted_rejection_reasons = to_jsonable(item_get(state, "drafted_rejection_reasons", {}) or {})
        review_units = to_jsonable(item_get(state, "review_units", []))
        early_rejection_reason = str(item_get(state, "early_rejection_reason", "")).strip()
        application_number = self._extract_application_number(state)
        current_notice_round = self._extract_current_notice_round(state)
        next_notice_round = current_notice_round + 1

        disputes = [self._serialize_dispute(item, evidence_map) for item in item_get(state, "disputes", []) or []]
        response_disputes = [item for item in disputes if str(item.get("origin", "")).strip() == "response_dispute"]
        amendment_disputes = [item for item in disputes if str(item.get("origin", "")).strip() == "amendment_review"]
        response_reply_items = self._build_response_reply_items(response_disputes, drafted_rejection_reasons)
        substantive_change_groups = self._build_claim_change_groups(
            substantive_amendments=item_get(state, "substantive_amendments", []),
            support_findings=item_get(state, "support_findings", []),
            amendment_disputes=amendment_disputes,
            effective_claims=item_get(state, "claims_effective_structured", [])
            or item_get(state, "claims_current_structured", []),
        )

        report = {
            "title": self._build_report_title(application_number, current_notice_round),
            "task_id": item_get(state, "task_id", ""),
            "status": "early_rejected" if early_rejection_reason else "completed",
            "notice_context": {
                "application_number": application_number,
                "current_notice_round": current_notice_round,
                "next_notice_round": next_notice_round,
            },
            "summary": self._generate_summary(
                response_disputes=response_disputes,
                response_reply_items=response_reply_items,
                application_number=application_number,
                current_notice_round=current_notice_round,
                early_rejection_reason=early_rejection_reason,
                has_claim_amendment=bool(item_get(state, "has_claim_amendment", False)),
                added_matter_risk=bool(item_get(state, "added_matter_risk", False)),
            ),
            "amendment_section": {
                "has_claim_amendment": bool(item_get(state, "has_claim_amendment", False)),
                "added_matter_risk": bool(item_get(state, "added_matter_risk", False)),
                "early_rejection_reason": early_rejection_reason,
                "substantive_amendments": to_jsonable(item_get(state, "substantive_amendments", [])),
                "structural_adjustments": to_jsonable(item_get(state, "structural_adjustments", [])),
                "support_findings": to_jsonable(item_get(state, "support_findings", [])),
                "substantive_change_groups": substantive_change_groups,
            },
            "claim_review_section": {
                "items": review_units,
            },
            "response_dispute_section": {
                "items": response_disputes,
            },
            "response_reply_section": {
                "items": response_reply_items,
            },
        }
        return report

    def _serialize_dispute(
        self,
        dispute: Any,
        evidence_map: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        dispute_id = str(item_get(dispute, "dispute_id", "")).strip()
        return {
            "dispute_id": dispute_id,
            "origin": str(item_get(dispute, "origin", "response_dispute")).strip() or "response_dispute",
            "source_argument_id": str(item_get(dispute, "source_argument_id", "")).strip(),
            "source_feature_id": str(item_get(dispute, "source_feature_id", "")).strip(),
            "claim_ids": self._normalize_claim_ids(item_get(dispute, "claim_ids", [])),
            "feature_text": str(item_get(dispute, "feature_text", "")).strip(),
            "examiner_opinion": to_jsonable(item_get(dispute, "examiner_opinion", {})),
            "applicant_opinion": to_jsonable(item_get(dispute, "applicant_opinion", {})),
            "evidence_assessment": to_jsonable(evidence_map.get(dispute_id)),
        }

    def _generate_summary(
        self,
        response_disputes: List[Dict[str, Any]],
        response_reply_items: List[Dict[str, Any]],
        application_number: str,
        current_notice_round: int,
        early_rejection_reason: str,
        has_claim_amendment: bool,
        added_matter_risk: bool,
    ) -> Dict[str, Any]:
        total = len(response_disputes)
        assessed = 0

        verdict_distribution = {
            "applicant_correct": 0,
            "examiner_correct": 0,
            "inconclusive": 0,
        }
        rebuttal_type_distribution = {
            "fact_dispute": 0,
            "logic_dispute": 0,
            "unknown": 0,
        }

        for dispute in response_disputes:
            applicant_opinion = item_get(dispute, "applicant_opinion", {}) or {}
            rebuttal_type = str(item_get(applicant_opinion, "type", "")).strip()
            if rebuttal_type in rebuttal_type_distribution:
                rebuttal_type_distribution[rebuttal_type] += 1
            else:
                rebuttal_type_distribution["unknown"] += 1

            evidence_assessment = item_get(dispute, "evidence_assessment", None)
            if not evidence_assessment:
                continue

            assessed += 1
            verdict = str(item_get(item_get(evidence_assessment, "assessment", {}), "verdict", "")).strip()
            if verdict == "APPLICANT_CORRECT":
                verdict_distribution["applicant_correct"] += 1
            elif verdict == "EXAMINER_CORRECT":
                verdict_distribution["examiner_correct"] += 1
            else:
                verdict_distribution["inconclusive"] += 1

        return {
            "application_number": application_number,
            "current_notice_round": current_notice_round,
            "overall_conclusion": self._build_overall_conclusion(
                verdict_distribution=verdict_distribution,
                assessed_disputes=assessed,
                early_rejection_reason=early_rejection_reason,
            ),
            "amendment_strategy": self._build_amendment_strategy(
                has_claim_amendment=has_claim_amendment,
                added_matter_risk=added_matter_risk,
                early_rejection_reason=early_rejection_reason,
            ),
            "total_disputes": total,
            "assessed_disputes": assessed,
            "unassessed_disputes": max(total - assessed, 0),
            "response_reply_points": sum(
                1 for item in response_reply_items if str(item.get("final_examiner_rejection_reason", "")).strip()
            ),
            "rebuttal_type_distribution": rebuttal_type_distribution,
            "verdict_distribution": verdict_distribution,
        }

    def _build_response_reply_items(
        self,
        response_disputes: List[Dict[str, Any]],
        drafted_rejection_reasons: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for dispute in response_disputes:
            dispute_id = str(item_get(dispute, "dispute_id", "")).strip()
            final_reason = str(drafted_rejection_reasons.get(dispute_id, "")).strip()
            items.append(
                {
                    "dispute_id": dispute_id,
                    "claim_ids": self._normalize_claim_ids(item_get(dispute, "claim_ids", [])),
                    "feature_text": str(item_get(dispute, "feature_text", "")).strip(),
                    "applicant_opinion": to_jsonable(item_get(dispute, "applicant_opinion", {})),
                    "final_examiner_rejection_reason": final_reason,
                }
            )
        return items

    def _build_claim_change_groups(
        self,
        substantive_amendments: List[Any],
        support_findings: List[Any],
        amendment_disputes: List[Dict[str, Any]],
        effective_claims: List[Any],
    ) -> List[Dict[str, Any]]:
        support_map = {
            str(item_get(item, "amendment_id", "")).strip(): to_jsonable(item)
            for item in support_findings or []
            if str(item_get(item, "amendment_id", "")).strip()
        }
        dispute_map = {
            str(item.get("source_feature_id", "")).strip(): item
            for item in amendment_disputes
            if str(item.get("source_feature_id", "")).strip()
        }

        claim_type_map = {
            str(item_get(item, "claim_id", "")).strip(): str(item_get(item, "claim_type", "")).strip()
            for item in effective_claims or []
            if str(item_get(item, "claim_id", "")).strip()
        }

        grouped_items: Dict[str, List[Dict[str, Any]]] = {}
        for amendment in substantive_amendments or []:
            amendment_id = str(item_get(amendment, "amendment_id", "")).strip()
            feature_text = str(item_get(amendment, "feature_text", "")).strip()
            feature_before_text = str(item_get(amendment, "feature_before_text", "")).strip()
            feature_after_text = str(item_get(amendment, "feature_after_text", "")).strip() or feature_text
            amendment_kind = str(item_get(amendment, "amendment_kind", "")).strip()
            content_origin = str(item_get(amendment, "content_origin", "")).strip()
            if not feature_text:
                feature_text = feature_after_text
            dispute = dispute_map.get(amendment_id, {})
            evidence_assessment = item_get(dispute, "evidence_assessment", {}) or {}
            assessment = item_get(evidence_assessment, "assessment", {}) or {}
            item = {
                "amendment_id": amendment_id,
                "feature_text": feature_text,
                "feature_before_text": feature_before_text,
                "feature_after_text": feature_after_text,
                "contains_added_text": self._change_contains_added_text(feature_before_text, feature_after_text),
                "amendment_kind": amendment_kind,
                "content_origin": content_origin,
                "source_claim_ids": self._normalize_claim_ids(item_get(amendment, "source_claim_ids", [])),
                "support_finding": support_map.get(amendment_id, {}),
                "assessment": to_jsonable(assessment),
                "evidence": to_jsonable(item_get(evidence_assessment, "evidence", [])),
                "final_review_reason": self._build_amendment_final_reason(assessment),
                "has_ai_assessment": self._has_ai_assessment(assessment),
            }
            for claim_id in self._normalize_claim_ids(item_get(amendment, "target_claim_ids", [])):
                grouped_items.setdefault(claim_id, []).append(dict(item))
        return [
            {
                "claim_id": claim_id,
                "claim_type": claim_type_map.get(claim_id, "unknown"),
                "items": self._sort_claim_change_items(items),
            }
            for claim_id, items in sorted(grouped_items.items(), key=lambda entry: self._claim_sort_key(entry[0]))
        ]

    def _change_contains_added_text(self, before_text: str, after_text: str) -> bool:
        before_tokens = self._tokenize_change_text(before_text)
        after_tokens = self._tokenize_change_text(after_text)
        if not after_tokens:
            return False

        matcher = SequenceMatcher(a=before_tokens, b=after_tokens)
        for tag, _, _, start_after, end_after in matcher.get_opcodes():
            if tag in {"insert", "replace"} and after_tokens[start_after:end_after]:
                return True
        return False


    def _tokenize_change_text(self, text: Any) -> List[str]:
        value = str(text or "")
        if not value:
            return []
        return re.findall(r"\s+|[A-Za-z0-9_]+|[\u4e00-\u9fff]|[^\sA-Za-z0-9_\u4e00-\u9fff]", value)

    def _build_amendment_final_reason(self, assessment: Dict[str, Any]) -> str:
        verdict = str(item_get(assessment, "verdict", "")).strip()
        if verdict == "APPLICANT_CORRECT":
            return str(item_get(assessment, "examiner_rejection_rationale", "")).strip()
        return str(item_get(assessment, "reasoning", "")).strip()

    def _has_ai_assessment(self, assessment: Dict[str, Any]) -> bool:
        verdict = str(item_get(assessment, "verdict", "")).strip()
        return verdict in {"APPLICANT_CORRECT", "EXAMINER_CORRECT", "INCONCLUSIVE"}

    def _extract_current_notice_round(self, state: Any) -> int:
        prepared = _to_dict(item_get(state, "prepared_materials", {}))
        office_action = _to_dict(prepared.get("office_action", {}))
        try:
            current_notice_round = int(office_action.get("current_notice_round", 0) or 0)
        except Exception:
            return 0
        if current_notice_round <= 0:
            raise ValueError("report_generation 数据非法: 缺少有效的 current_notice_round")
        return current_notice_round

    def _extract_application_number(self, state: Any) -> str:
        prepared = _to_dict(item_get(state, "prepared_materials", {}))
        office_action = _to_dict(prepared.get("office_action", {}))
        return str(office_action.get("application_number", "")).strip()

    def _build_report_title(self, application_number: str, current_notice_round: int) -> str:
        suffix = f"第{current_notice_round}通" if current_notice_round > 0 else "未知轮次"
        if application_number:
            return f"AI答复报告_{application_number}_{suffix}"
        return f"AI答复报告_{suffix}"

    def _build_overall_conclusion(
        self,
        verdict_distribution: Dict[str, int],
        assessed_disputes: int,
        early_rejection_reason: str,
    ) -> str:
        if early_rejection_reason:
            return "存在可提前驳回事由"
        if assessed_disputes <= 0:
            return "暂无可用核查结论"

        applicant_correct = int(verdict_distribution.get("applicant_correct", 0) or 0)
        examiner_correct = int(verdict_distribution.get("examiner_correct", 0) or 0)
        inconclusive = int(verdict_distribution.get("inconclusive", 0) or 0)
        if applicant_correct > examiner_correct:
            return "申请人主要争点更占优"
        if examiner_correct > applicant_correct:
            return "审查员主要争点更占优"
        if inconclusive == assessed_disputes:
            return "现有争点暂无法形成明确结论"
        return "双方争点暂时相持"

    def _build_amendment_strategy(
        self,
        has_claim_amendment: bool,
        added_matter_risk: bool,
        early_rejection_reason: str,
    ) -> str:
        if early_rejection_reason:
            return "可提前驳回"
        if not has_claim_amendment:
            return "无权利要求修改"
        if added_matter_risk:
            return "修改存在超范围风险"
        return "修改可继续进入实质审查"

    def _save_report(self, report: Dict[str, Any], state) -> Path:
        output_dir = Path(item_get(state, "output_dir"))
        output_dir.mkdir(parents=True, exist_ok=True)

        output_path = output_dir / "final_report.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(to_jsonable(report), f, ensure_ascii=False, indent=2)
        return output_path

    def _build_evidence_map(self, evidence_assessments: list) -> Dict[str, Dict[str, Any]]:
        result: Dict[str, Dict[str, Any]] = {}
        for item in evidence_assessments or []:
            dispute_id = str(item_get(item, "dispute_id", "")).strip()
            if not dispute_id:
                continue
            result[dispute_id] = {
                "origin": str(item_get(item, "origin", "response_dispute")).strip() or "response_dispute",
                "source_argument_id": str(item_get(item, "source_argument_id", "")).strip(),
                "source_feature_id": str(item_get(item, "source_feature_id", "")).strip(),
                "claim_ids": self._normalize_claim_ids(item_get(item, "claim_ids", [])),
                "claim_text": str(item_get(item, "claim_text", "")).strip(),
                "assessment": to_jsonable(item_get(item, "assessment", {})),
                "evidence": to_jsonable(item_get(item, "evidence", [])),
                "trace": to_jsonable(item_get(item, "trace", {})),
            }
        return result

    def _normalize_claim_ids(self, value: Any) -> List[str]:
        claim_ids: List[str] = []
        candidates = value if isinstance(value, list) else [value]
        for raw in candidates:
            text = str(raw or "").strip()
            if not text:
                continue
            for piece in re.split(r"[，,\s]+", text):
                part = piece.strip()
                if not part:
                    continue
                if part.isdigit() and part not in claim_ids:
                    claim_ids.append(part)
        return claim_ids

    def _claim_sort_key(self, value: Any) -> tuple[int, str]:
        text = str(value or "").strip()
        if text.isdigit():
            return (0, f"{int(text):09d}")
        return (1, text)

    def _sort_claim_change_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        def _item_key(item: Dict[str, Any]) -> tuple[int, str]:
            amendment_kind = str(item.get("amendment_kind", "")).strip()
            source_rank = 1 if amendment_kind == "spec_feature_addition" else 0
            return (source_rank, str(item.get("amendment_id", "")).strip())

        return sorted(items, key=_item_key)
