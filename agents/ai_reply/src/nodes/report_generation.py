"""
报告生成节点
生成最终的 JSON 格式报告
"""

import json
import re
from pathlib import Path
from typing import Dict, Any, List
from loguru import logger
from agents.ai_reply.src.utils import get_node_cache
from agents.common.utils.serialization import to_jsonable, item_get


class ReportGenerationNode:
    """报告生成节点"""

    def __init__(self, config=None):
        self.config = config

    def __call__(self, state):
        logger.info("开始生成最终报告")

        updates = {
            "current_node": "report_generation",
            "status": "running",
            "progress": 90.0
        }

        try:
            # 获取节点缓存
            cache = get_node_cache(self.config, "report_generation")

            # 使用缓存运行报告生成
            report = cache.run_step("generate_report_v4", self._generate_report, state)

            # 保存到文件
            output_path = self._save_report(report, state)

            # 更新状态
            updates["final_report"] = report
            updates["progress"] = 95.0
            updates["status"] = "completed"

            logger.info(f"报告已生成: {output_path}")

        except Exception as e:
            logger.error(f"报告生成节点执行失败: {e}")
            updates["errors"] = [{
                "node_name": "report_generation",
                "error_message": str(e),
                "error_type": "report_generation_error"
            }]
            updates["status"] = "failed"

        return updates

    def _generate_report(self, state) -> Dict[str, Any]:
        """生成最终报告"""
        evidence_map = self._build_evidence_map(item_get(state, "evidence_assessments", []))
        drafted_rejection_reasons = to_jsonable(item_get(state, "drafted_rejection_reasons", {}) or {})
        early_rejection_reason = str(item_get(state, "early_rejection_reason", "")).strip()

        report = {
            "task_id": item_get(state, "task_id", ""),
            "status": "early_rejected" if early_rejection_reason else "completed",
            "amendment_review": {
                "has_claim_amendment": bool(item_get(state, "has_claim_amendment", False)),
                "added_matter_risk": bool(item_get(state, "added_matter_risk", False)),
                "early_rejection_reason": early_rejection_reason,
                "added_features": to_jsonable(item_get(state, "added_features", [])),
                "support_findings": to_jsonable(item_get(state, "support_findings", [])),
                "reuse_oa_tasks": to_jsonable(item_get(state, "reuse_oa_tasks", [])),
                "topup_tasks": to_jsonable(item_get(state, "topup_tasks", [])),
            },
            "disputes": []
        }

        # 遍历所有争议点
        for dispute in item_get(state, "disputes", []):
            dispute_id = item_get(dispute, "dispute_id", "")
            claim_ids = self._normalize_claim_ids(item_get(dispute, "claim_ids", []))
            feature_text = item_get(dispute, "feature_text", "")

            # 构建争议点报告
            dispute_report = {
                "dispute_id": dispute_id,
                "claim_ids": claim_ids,
                "feature_text": feature_text,
                "examiner_opinion": to_jsonable(item_get(dispute, "examiner_opinion", {})),
                "applicant_opinion": to_jsonable(item_get(dispute, "applicant_opinion", {})),
                "evidence_assessment": to_jsonable(evidence_map.get(dispute_id)),
            }

            report["disputes"].append(dispute_report)

        # 添加汇总信息
        report["summary"] = self._generate_summary(report["disputes"])
        second_notice_items = self._collect_second_office_action_items(report["disputes"], drafted_rejection_reasons)
        report["summary"]["second_office_action_points"] = len(second_notice_items)
        report["second_office_action_notice"] = {
            "text": self._build_second_office_action_text(second_notice_items),
            "items": second_notice_items,
        }

        return report

    def _generate_summary(self, disputes: list) -> Dict[str, Any]:
        """生成汇总信息"""
        total = len(disputes)
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

        for dispute in disputes:
            applicant_opinion = item_get(dispute, "applicant_opinion", {}) or {}
            rebuttal_type = item_get(applicant_opinion, "type", "")
            if rebuttal_type in rebuttal_type_distribution:
                rebuttal_type_distribution[rebuttal_type] += 1
            else:
                rebuttal_type_distribution["unknown"] += 1

            evidence_assessment = dispute.get("evidence_assessment")
            if not evidence_assessment:
                continue

            assessed += 1
            verdict = item_get(item_get(evidence_assessment, "assessment", {}), "verdict", "")
            if verdict == "APPLICANT_CORRECT":
                verdict_distribution["applicant_correct"] += 1
            elif verdict == "EXAMINER_CORRECT":
                verdict_distribution["examiner_correct"] += 1
            else:
                verdict_distribution["inconclusive"] += 1

        return {
            "total_disputes": total,
            "assessed_disputes": assessed,
            "unassessed_disputes": max(total - assessed, 0),
            "rebuttal_type_distribution": rebuttal_type_distribution,
            "verdict_distribution": verdict_distribution,
        }

    def _collect_second_office_action_items(
        self,
        disputes: List[Dict[str, Any]],
        drafted_rejection_reasons: Dict[str, str],
    ) -> List[Dict[str, str]]:
        """收集可用于二次审查意见通知书的驳回说理点。"""
        items: List[Dict[str, str]] = []
        for dispute in disputes:
            evidence_assessment = item_get(dispute, "evidence_assessment", {}) or {}
            assessment = item_get(evidence_assessment, "assessment", {}) or {}
            verdict = str(item_get(assessment, "verdict", "")).strip()
            if verdict != "APPLICANT_CORRECT":
                continue

            rationale = str(item_get(assessment, "examiner_rejection_rationale", "")).strip()
            if not rationale:
                dispute_id = str(item_get(dispute, "dispute_id", "")).strip() or "<unknown_dispute>"
                raise ValueError(
                    f"report_generation 数据非法: dispute_id={dispute_id} verdict=APPLICANT_CORRECT 但缺少 examiner_rejection_rationale"
                )
            dispute_id = str(item_get(dispute, "dispute_id", "")).strip()
            final_reason = str(drafted_rejection_reasons.get(dispute_id, "")).strip()
            if not final_reason:
                raise ValueError(
                    f"report_generation 数据非法: dispute_id={dispute_id} verdict=APPLICANT_CORRECT 但缺少 drafted final reason"
                )

            items.append({
                "dispute_id": dispute_id,
                "claim_ids": self._normalize_claim_ids(item_get(dispute, "claim_ids", [])),
                "feature_text": str(item_get(dispute, "feature_text", "")).strip(),
                "final_examiner_rejection_reason": final_reason,
            })
        return items

    def _build_second_office_action_text(self, items: List[Dict[str, str]]) -> str:
        """将驳回说理点汇总为一段可直接用于二次审查意见通知书的文本。"""
        if not items:
            return ""

        clauses: List[str] = []
        for index, item in enumerate(items, start=1):
            claim_ids = self._normalize_claim_ids(item.get("claim_ids", []))
            claim_label = "、".join(claim_ids) if claim_ids else "未标注权利要求"
            feature_text = item.get("feature_text", "") or "未提取争议特征"
            reason = item.get("final_examiner_rejection_reason", "").strip().rstrip("。；;")
            clauses.append(
                f"关于第{index}项核查结论（权利要求{claim_label}，争议特征“{feature_text}”），{reason}"
            )

        return (
            "经对申请人意见陈述书与现有证据再次审查，本局形成如下进一步审查意见："
            + "；".join(clauses)
            + "。"
        )

    def _save_report(self, report: Dict[str, Any], state) -> Path:
        """保存报告到文件"""
        output_dir = Path(item_get(state, "output_dir"))
        output_dir.mkdir(parents=True, exist_ok=True)

        output_path = output_dir / "final_report.json"

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(to_jsonable(report), f, ensure_ascii=False, indent=2)

        return output_path

    def _build_evidence_map(self, evidence_assessments: list) -> Dict[str, Dict[str, Any]]:
        """按 dispute_id 索引核查结果。"""
        result: Dict[str, Dict[str, Any]] = {}
        for item in evidence_assessments or []:
            dispute_id = item_get(item, "dispute_id", "")
            if not dispute_id:
                continue
            result[dispute_id] = {
                "claim_ids": self._normalize_claim_ids(item_get(item, "claim_ids", [])),
                "claim_text": item_get(item, "claim_text", ""),
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
                if not part or not part.isdigit():
                    continue
                if part not in claim_ids:
                    claim_ids.append(part)
        return claim_ids
