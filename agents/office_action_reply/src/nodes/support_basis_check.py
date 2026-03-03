"""
修改支持依据核查节点
使用大模型判断新增特征在原始申请文件中的支持情况，识别修改超范围风险
"""

import json
from typing import Any, Dict, List

from loguru import logger

from agents.common.utils.llm import get_llm_service
from agents.office_action_reply.src.state import SupportFinding
from agents.office_action_reply.src.utils import get_node_cache


class SupportBasisCheckNode:
    """修改支持依据核查节点（LLM主判）"""

    def __init__(self, config=None):
        self.config = config
        self.llm_service = get_llm_service()

    def __call__(self, state):
        logger.info("开始修改支持依据核查")
        updates = {
            "current_node": "support_basis_check",
            "status": "running",
            "progress": 61.0,
        }

        try:
            cache = get_node_cache(self.config, "support_basis_check")
            result = cache.run_step(
                "check_support_basis_v3",
                self._check_support_basis,
                self._state_get(state, "added_features", []),
                self._state_get(state, "prepared_materials", {}),
            )

            updates["support_findings"] = [
                item if isinstance(item, SupportFinding) else SupportFinding(**item)
                for item in result.get("support_findings", [])
            ]
            updates["added_matter_risk"] = result["added_matter_risk"]
            updates["early_rejection_reason"] = str(result["early_rejection_reason"]).strip()
            updates["status"] = "completed"
            updates["progress"] = 64.0
            logger.info(f"支持依据核查完成，超范围风险: {updates['added_matter_risk']}")
        except Exception as e:
            logger.error(f"支持依据核查失败: {e}")
            updates["errors"] = [{
                "node_name": "support_basis_check",
                "error_message": str(e),
                "error_type": "support_basis_check_error",
            }]
            updates["status"] = "failed"

        return updates

    def _check_support_basis(self, added_features, prepared_materials):
        features = [self._to_dict(item) for item in (added_features or [])]
        spec_features = [
            feature
            for feature in features
            if str(feature.get("source_type", "")).strip().lower() == "spec"
        ]
        if not spec_features:
            return {
                "support_findings": [],
                "added_matter_risk": False,
                "early_rejection_reason": "",
            }

        prepared = self._to_dict(prepared_materials)
        original_patent = self._to_dict(prepared.get("original_patent", {}))
        original_data = self._to_dict(original_patent.get("data", {}))
        description = self._to_dict(original_data.get("description", {}))
        detailed_description = str(description.get("detailed_description", "")).strip()

        if not detailed_description:
            findings = [
                {
                    "feature_id": str(feature.get("feature_id", "")).strip(),
                    "feature_text": str(feature.get("feature_text", "")).strip(),
                    "support_found": False,
                    "support_basis": "",
                    "risk": "Not Found (Risk of New Matter)",
                }
                for feature in spec_features
            ]
            return {
                "support_findings": findings,
                "added_matter_risk": True,
                "early_rejection_reason": "原说明书缺少 detailed_description，无法证明新增特征存在原始记载，存在修改超范围风险。",
            }

        messages = [
            {"role": "system", "content": self._build_system_prompt()},
            {"role": "user", "content": self._build_user_prompt(spec_features, detailed_description)},
        ]
        response = self.llm_service.chat_completion_json(
            messages,
            temperature=0.05,
            thinking=True,
        )
        normalized = self._normalize_result(response)
        expected_ids = {
            str(feature.get("feature_id", "")).strip()
            for feature in spec_features
            if str(feature.get("feature_id", "")).strip()
        }
        actual_ids = {
            str(item.get("feature_id", "")).strip()
            for item in normalized.get("support_findings", [])
            if str(item.get("feature_id", "")).strip()
        }
        if actual_ids != expected_ids:
            raise ValueError(
                "support_basis_check 输出 feature_id 集合不匹配，"
                f"expected={sorted(expected_ids)}, actual={sorted(actual_ids)}"
            )
        return normalized

    def _build_system_prompt(self) -> str:
        return """你是专利法修改支持依据审查专家。你的任务是判断 source_type=spec 的新增特征是否在原说明书具体实施方式中有直接、明确支持。

判定规则：
1. 只能以说明书 detailed_description 为依据。
2. 允许语义一致，但必须达到“直接且明确可得”。
3. 不能仅靠推理补足未记载内容。
4. 任何关键新增特征无支持时，存在修改超范围风险。

输出要求：
- 只输出 JSON 对象，不得输出其他文本。
- 输出结构：
{
  "support_findings": [
    {
      "feature_id": "New_F1",
      "feature_text": "...",
      "support_found": true,
      "support_basis": "原说明书第[0045]段 ...",
      "risk": ""
    }
  ],
  "added_matter_risk": false,
  "early_rejection_reason": ""
}

字段约束：
- support_found 只能是 true/false。
- 若 support_found=false，risk 应明确写 Not Found (Risk of New Matter)。
- 若 added_matter_risk=true，必须给出 early_rejection_reason。"""

    def _build_user_prompt(
        self,
        features: List[Dict[str, Any]],
        detailed_description: str,
    ) -> str:
        simplified_features = [
            {
                "feature_id": str(item.get("feature_id", "")).strip(),
                "feature_text": str(item.get("feature_text", "")).strip(),
                "source_type": str(item.get("source_type", "")).strip(),
            }
            for item in features
        ]
        return f"""请核查以下新增特征在详细实施方式中的支持依据：

【新增特征（仅 spec）】
{json.dumps(simplified_features, ensure_ascii=False, indent=2)}

【说明书 detailed_description】
{detailed_description[:26000]}"""

    def _normalize_result(self, response: Dict[str, Any]) -> Dict[str, Any]:
        result = self._to_dict(response)
        if "support_findings" not in result:
            raise ValueError("support_basis_check 输出缺少 support_findings")
        findings_raw = result.get("support_findings")
        if not isinstance(findings_raw, list):
            raise ValueError("support_basis_check 输出格式错误：support_findings 不是列表")

        findings: List[Dict[str, Any]] = []
        for item in findings_raw:
            finding = self._to_dict(item)
            feature_id = str(finding.get("feature_id", "")).strip()
            feature_text = str(finding.get("feature_text", "")).strip()
            support_found_raw = finding.get("support_found")
            if not isinstance(support_found_raw, bool):
                raise ValueError("support_basis_check 输出非法 support_found，必须为布尔值")
            support_found = support_found_raw
            support_basis = str(finding.get("support_basis", "")).strip()
            risk = str(finding.get("risk", "")).strip()

            if not feature_id or not feature_text:
                raise ValueError("support_basis_check 输出非法 support_findings 项，缺少 feature_id 或 feature_text")

            findings.append({
                "feature_id": feature_id,
                "feature_text": feature_text,
                "support_found": support_found,
                "support_basis": support_basis,
                "risk": risk,
            })

        if "added_matter_risk" not in result:
            raise ValueError("support_basis_check 输出缺少 added_matter_risk")
        added_matter_risk_raw = result.get("added_matter_risk")
        if not isinstance(added_matter_risk_raw, bool):
            raise ValueError("support_basis_check 输出非法 added_matter_risk，必须为布尔值")
        added_matter_risk = added_matter_risk_raw

        if "early_rejection_reason" not in result:
            raise ValueError("support_basis_check 输出缺少 early_rejection_reason")
        early_rejection_reason = str(result.get("early_rejection_reason", "")).strip()

        return {
            "support_findings": findings,
            "added_matter_risk": added_matter_risk,
            "early_rejection_reason": early_rejection_reason,
        }

    def _state_get(self, state: Any, key: str, default=None):
        if isinstance(state, dict):
            return state.get(key, default)
        return getattr(state, key, default)

    def _to_dict(self, item: Any) -> Dict[str, Any]:
        if isinstance(item, dict):
            return item
        if hasattr(item, "model_dump"):
            return item.model_dump()
        if hasattr(item, "dict"):
            return item.dict()
        return {}
