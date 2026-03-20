"""
修改支持依据核查节点
使用大模型判断新增特征在原始申请文件中的支持情况，识别修改超范围风险
"""

import json
from typing import Any, Dict, List

from loguru import logger

from agents.common.utils.llm import get_llm_service
from agents.ai_reply.src.state import SupportFinding
from agents.ai_reply.src.utils import get_node_cache


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
                "check_support_basis_v4",
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
        specification_context = self._build_specification_context(description)

        if not specification_context:
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
                "early_rejection_reason": "原说明书缺少可用的支持性文本上下文，无法证明新增特征存在原始记载，存在修改超范围风险。",
            }

        messages = [
            {"role": "system", "content": self._build_system_prompt()},
            {"role": "user", "content": self._build_user_prompt(spec_features, specification_context)},
        ]
        response = self.llm_service.invoke_text_json(
            messages=messages,
            task_kind="oar_support_basis_check",
            temperature=0.05,
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
        return """你是一位资深的专利审查专家。你的任务是根据《专利法》关于“修改超范围”的审查标准，判断申请人引入的“新增特征”是否在原说明书中有直接、明确的支持依据。

【核心判定标准】
修改的内容必须是原说明书明确记载的内容，或者是所属技术领域的技术人员通过原说明书记载的内容**直接、毫无疑义地推导**出的内容。

【具体审查规则】
1. **严格限定范围**：只能以用户提供的【说明书相关文本】为依据，绝对禁止引入外部常识或主观推理来补足未记载的技术特征。
2. **支持的情形（support_found = true）**：
   - 原文字义支持：说明书中有完全相同的文字。
   - 同义替换：使用了本领域公知且毫无歧义的同义词。
   - 直接推导：本领域技术人员阅读后必然得出的唯一结论。
3. **超范围的情形（support_found = false，存在风险）**：
   - **上位概括**：将原说明书中的具体概念（如“铜”）替换为上位概念（如“金属”），且原说明书未暗示其他可能。
   - **特征剥离**：将具体实施例中相互紧密关联、配合工作的多个特征剥离，仅提取其中一个单独作为新增特征。
   - **新的组合**：将原本分别记载在不同实施例中、毫无关联的特征生硬拼凑在一起。
   - **数值范围拼凑**：提取原说明书中未明确结合过的数值端点形成新的范围。

【输出要求】
- 必须严格输出纯 JSON 对象，不要输出 Markdown 代码块标签（如 ```json），不要包含任何额外的解释文本。
- 必须遵循以下 JSON Schema：
{
  "support_findings":[
    {
      "feature_id": "New_F1",
      "feature_text": "新增特征原文",
      "reasoning": "简要分析过程：说明书对应段落是怎么描述的？是否属于同义替换或直接推导？是否存在特征剥离或上位概括等超范围情形？",
      "support_found": true或false,
      "support_basis": "如果支持，务必提取说明书中的【原句】或【段落号】作为直接证据；如果不支持，填空字符串",
      "risk": "如果 support_found 为 false，固定填入 'Not Found (Risk of New Matter)'；如果为 true，填空字符串"
    }
  ],
  "added_matter_risk": true或false, 
  "early_rejection_reason": "如果 added_matter_risk 为 true，请一句话概括为何存在超范围风险（例如：'特征X属于将实施例中的关联特征强行剥离，缺乏修改支持'）；如果为 false，填空字符串"
}

【字段约束】
- added_matter_risk：只要 support_findings 中有任何一个特征的 support_found 为 false，此项必须为 true。"""

    def _build_user_prompt(
        self,
        features: List[Dict[str, Any]],
        specification_context: str,
    ) -> str:
        simplified_features =[
            {
                "feature_id": str(item.get("feature_id", "")).strip(),
                "feature_text": str(item.get("feature_text", "")).strip(),
                "source_type": str(item.get("source_type", "")).strip(),
            }
            for item in features
        ]
        return f"""请对以下新增特征逐一进行支持依据核查，并按要求输出 JSON 结果。

【待核查的新增特征（仅 spec）】
{json.dumps(simplified_features, ensure_ascii=False, indent=2)}

=========================
【说明书相关文本】
（注：请仔细检索以下文本寻找直接、明确的支持依据）
{specification_context}"""

    def _build_specification_context(self, description: Dict[str, Any]) -> str:
        sections = [
            ("发明内容", str(description.get("summary_of_invention", "")).strip()),
            ("有益效果/技术效果", str(description.get("technical_effect", "")).strip()),
            ("附图说明", str(description.get("brief_description_of_drawings", "")).strip()),
            ("具体实施方式", str(description.get("detailed_description", "")).strip()),
        ]
        return "\n\n".join(
            f"【{title}】\n{content}"
            for title, content in sections
            if content
        ).strip()

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
            reasoning = str(finding.get("reasoning", "")).strip()

            if not feature_id or not feature_text:
                raise ValueError("support_basis_check 输出非法 support_findings 项，缺少 feature_id 或 feature_text")

            findings.append({
                "feature_id": feature_id,
                "feature_text": feature_text,
                "reasoning": reasoning,
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
