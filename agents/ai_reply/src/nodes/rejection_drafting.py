"""
统一驳回正文生成节点
将核查阶段输出的驳回逻辑要点统一改写为正式审查意见正文
"""

import json
from typing import Any, Dict, List

from loguru import logger

from agents.common.utils.llm import get_llm_service
from agents.ai_reply.src.utils import get_node_cache


class RejectionDraftingNode:
    """统一驳回正文生成节点"""

    def __init__(self, config=None):
        self.config = config
        self.llm_service = get_llm_service()

    def __call__(self, state):
        logger.info("开始统一生成正式驳回正文")
        updates = {
            "current_node": "rejection_drafting",
            "status": "running",
            "progress": 92.0,
        }

        try:
            cache = get_node_cache(self.config, "rejection_drafting")
            drafted = cache.run_step(
                "draft_rejection_reasons_v1",
                self._draft_rejection_reasons,
                self._state_get(state, "disputes", []),
                self._state_get(state, "evidence_assessments", []),
            )
            if drafted:
                updates["drafted_rejection_reasons"] = drafted
                updates["status"] = "completed"
                updates["progress"] = 93.0
                logger.info(f"完成 {len(drafted)} 项正式驳回正文生成")
            else:
                updates["status"] = "completed"
                updates["progress"] = 93.0
                logger.info("无需要统一润色的驳回项")
        except Exception as e:
            logger.error(f"统一驳回正文生成失败: {e}")
            updates["errors"] = [{
                "node_name": "rejection_drafting",
                "error_message": str(e),
                "error_type": "rejection_drafting_error",
            }]
            updates["status"] = "failed"

        return updates

    def _draft_rejection_reasons(self, disputes: List[Any], evidence_assessments: List[Any]) -> Dict[str, str]:
        assessment_map = {
            str(self._item_get(item, "dispute_id", "")).strip(): self._to_dict(item)
            for item in evidence_assessments or []
            if str(self._item_get(item, "dispute_id", "")).strip()
        }

        drafting_items: List[Dict[str, Any]] = []
        for dispute in disputes or []:
            dispute_id = str(self._item_get(dispute, "dispute_id", "")).strip()
            if not dispute_id:
                continue
            assessment_item = assessment_map.get(dispute_id, {})
            assessment = self._to_dict(self._item_get(assessment_item, "assessment", {}))
            verdict = str(assessment.get("verdict", "")).strip()
            if verdict != "APPLICANT_CORRECT":
                continue

            rationale = str(assessment.get("examiner_rejection_rationale", "")).strip()
            if not rationale:
                raise ValueError(
                    f"rejection_drafting 数据非法: dispute_id={dispute_id} verdict=APPLICANT_CORRECT 但缺少 examiner_rejection_rationale"
                )

            drafting_items.append({
                "dispute_id": dispute_id,
                "claim_ids": self._normalize_claim_ids(self._item_get(dispute, "claim_ids", [])),
                "claim_text": str(self._item_get(assessment_item, "claim_text", "")).strip(),
                "feature_text": str(self._item_get(dispute, "feature_text", "")).strip(),
                "examiner_rejection_rationale": rationale,
                "examiner_opinion": self._to_dict(self._item_get(dispute, "examiner_opinion", {})),
                "applicant_opinion": self._to_dict(self._item_get(dispute, "applicant_opinion", {})),
                "evidence": self._compact_evidence(self._item_get(assessment_item, "evidence", [])),
            })

        if not drafting_items:
            return {}

        response = self.llm_service.invoke_text_json(
            messages=[
                {"role": "system", "content": self._build_system_prompt()},
                {"role": "user", "content": self._build_user_prompt(drafting_items)},
            ],
            task_kind="oar_rejection_drafting",
            temperature=0.1,
        )
        return self._normalize_llm_output(response, drafting_items)

    def _build_system_prompt(self) -> str:
        return """你是一位资深的中国国家知识产权局（CNIPA）专利实质审查员。
你的核心任务是将内部已经确立的“替代性驳回逻辑要点”转化为标准、严谨、可直接写入《审查意见通知书》（OA）正文的正式专业论述。

【背景与目标】
当前的场景是：申请人在意见陈述中指出了原审查逻辑的瑕疵（内部已认可申请人的主张），但基于客观事实，我们找到了**新的/完善后的替代性驳回逻辑**，相关权利要求依然应被驳回。
你需要直接输出基于新逻辑的驳回论述。
**极其重要的原则：在行文中绝对不要出现“采纳申请人意见”、“申请人意见属实”、“原审查确有不当”等妥协性或认可性的话语。你心里清楚原逻辑有瑕疵即可，正文必须直接用新的说理展开反驳，保持审查员的笃定与权威。**

【输入数据说明】
在用户输入中，你会收到一组 JSON 格式的争议项数据，每个争议项包含以下关键字段：
- claim_text / feature_text: 争议涉及的完整权利要求及具体争议特征。
- applicant_opinion: 申请人的答复意见（背景信息：内部认为此意见有理）。
- examiner_rejection_rationale: 重新确立的、用于继续驳回该特征的【核心逻辑要点】（这是你的主要改写对象）。
- evidence: 支撑上述新逻辑的对比文件引文或公知常识证据。

【撰写要求（生产级严控）】
1. **开门见山，直接反驳**：
   采用“提及意见 -> 直接引入新事实/新逻辑 -> 得出最终驳回结论”的结构，跳过任何肯定申请人的铺垫。
   范例句式：“关于申请人针对权利要求X的意见陈述，经审查，基于对比文件X（或结合公知常识）可知...，本领域技术人员为了解决...的技术问题，容易想到...。因此，该权利要求依然不具备突出的实质性特点和显著的进步（或新颖性）。”
2. **法言法语**：必须使用中国专利审查的标准术语（如“经审查”、“对比文件X公开了……”、“区别技术特征在于”、“本领域技术人员容易想到”等）。
3. **内容忠实度**：必须严格基于 `examiner_rejection_rationale` 的核心逻辑展开，并精准结合 `evidence` 中的证据内容。**绝不可凭空捏造技术事实或对比文件公开的内容**。
4. **禁止元话术**：**绝对禁止**使用“审查员认为”、“建议这样回复”、“我们可以主张”、“如果……则”等内部讨论语或商榷性语气。生成的文本必须是直接面向申请人的、不容置疑的最终论述。
5. **格式纯净**：直接输出说理正文，不要带有“审查意见：”、“回复：”等任何前缀或标题。

【输出格式】
你必须且只能输出合法的 JSON 格式数据（不要附带任何 Markdown 解释或代码块标记），结构如下：
{
  "items":[
    {
      "dispute_id": "DSP_1",
      "final_examiner_rejection_reason": "关于申请人对权利要求X提出的意见陈述，经审查，对比文件1实际上进一步公开了...（具体逻辑）...本领域技术人员在此基础上容易想到...因此，权利要求X依然不符合专利法第22条第3款的规定。"
    }
  ]
}"""

    def _build_user_prompt(self, drafting_items: List[Dict[str, Any]]) -> str:
        return (
             "待处理的争议项数据如下：\n"
            f"{json.dumps(drafting_items, ensure_ascii=False, indent=2)}"
        )

    def _normalize_llm_output(self, response: Dict[str, Any], drafting_items: List[Dict[str, Any]]) -> Dict[str, str]:
        output = self._to_dict(response)
        raw_items = output.get("items", [])
        if not isinstance(raw_items, list):
            raise ValueError("rejection_drafting 输出非法: items 必须为数组")

        expected_ids = [str(item.get("dispute_id", "")).strip() for item in drafting_items]
        result: Dict[str, str] = {}
        for item in raw_items:
            item_dict = self._to_dict(item)
            dispute_id = str(item_dict.get("dispute_id", "")).strip()
            if not dispute_id or dispute_id not in expected_ids:
                continue
            if dispute_id in result:
                raise ValueError(f"rejection_drafting 输出非法: dispute_id={dispute_id} 重复")
            final_reason = str(item_dict.get("final_examiner_rejection_reason", "")).strip()
            if not final_reason:
                raise ValueError(f"rejection_drafting 输出非法: dispute_id={dispute_id} 缺少 final_examiner_rejection_reason")
            result[dispute_id] = final_reason

        missing = [dispute_id for dispute_id in expected_ids if dispute_id not in result]
        if missing:
            raise ValueError(f"rejection_drafting 输出缺少 dispute_id: {missing}")
        return result

    def _compact_evidence(self, evidence_items: List[Any]) -> List[Dict[str, str]]:
        compacted: List[Dict[str, str]] = []
        for item in evidence_items or []:
            item_dict = self._to_dict(item)
            compacted.append({
                "doc_id": str(item_dict.get("doc_id", "")).strip(),
                "quote": str(item_dict.get("quote", "")).strip(),
                "analysis": str(item_dict.get("analysis", "")).strip(),
            })
        return compacted[:4]

    def _state_get(self, state: Any, key: str, default=None):
        if isinstance(state, dict):
            return state.get(key, default)
        return getattr(state, key, default)

    def _item_get(self, item: Any, key: str, default=None):
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)

    def _to_dict(self, item: Any) -> Dict[str, Any]:
        if isinstance(item, dict):
            return item
        if hasattr(item, "model_dump"):
            return item.model_dump()
        if hasattr(item, "dict"):
            return item.dict()
        return {}

    def _normalize_claim_ids(self, value: Any) -> List[str]:
        claim_ids: List[str] = []
        candidates = value if isinstance(value, list) else [value]
        for raw in candidates:
            text = str(raw or "").strip()
            if not text:
                continue
            for piece in text.replace("，", ",").split(","):
                part = piece.strip()
                if part and part not in claim_ids:
                    claim_ids.append(part)
        return claim_ids
