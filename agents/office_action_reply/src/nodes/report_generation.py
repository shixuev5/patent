"""
报告生成节点
生成最终的 JSON 格式报告
"""

import json
from pathlib import Path
from typing import Dict, Any
from loguru import logger
from agents.office_action_reply.src.utils import get_node_cache
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
            report = cache.run_step("generate_report", self._generate_report, state)

            # 保存到文件
            output_path = self._save_report(report, state)

            # 更新状态
            updates["final_report"] = report
            updates["progress"] = 100.0
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
            original_claim_id = item_get(dispute, "original_claim_id", "")
            feature_text = item_get(dispute, "feature_text", "")

            # 构建争议点报告
            dispute_report = {
                "dispute_id": dispute_id,
                "original_claim_id": original_claim_id,
                "feature_text": feature_text,
                "examiner_opinion": to_jsonable(item_get(dispute, "examiner_opinion", {})),
                "applicant_opinion": to_jsonable(item_get(dispute, "applicant_opinion", {})),
                "evidence_assessment": to_jsonable(evidence_map.get(dispute_id)),
            }

            report["disputes"].append(dispute_report)

        # 添加汇总信息
        report["summary"] = self._generate_summary(report["disputes"])

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
                "original_claim_id": item_get(item, "original_claim_id", ""),
                "claim_text": item_get(item, "claim_text", ""),
                "assessment": to_jsonable(item_get(item, "assessment", {})),
                "evidence": to_jsonable(item_get(item, "evidence", [])),
                "trace": to_jsonable(item_get(item, "trace", {})),
            }
        return result
