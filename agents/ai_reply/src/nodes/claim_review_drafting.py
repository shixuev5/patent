"""
逐条权利要求评述生成节点
先按规则聚合每条当前生效权利要求的素材，再交给 LLM 整理为正式评述。
"""

import json
from typing import Any, Dict, List

from loguru import logger

from agents.ai_reply.src.state import ClaimReviewItem
from agents.ai_reply.src.utils import get_node_cache
from agents.common.utils.llm import get_llm_service


class ClaimReviewDraftingNode:
    """逐条权利要求评述生成节点"""

    def __init__(self, config=None):
        self.config = config
        self.llm_service = get_llm_service()

    def __call__(self, state):
        logger.info("开始生成逐条权利要求评述")
        updates = {
            "current_node": "claim_review_drafting",
            "status": "running",
            "progress": 92.0,
        }

        try:
            cache = get_node_cache(self.config, "claim_review_drafting")
            claim_reviews = cache.run_step(
                "draft_claim_reviews_v2",
                self._draft_claim_reviews,
                self._state_get(state, "claims_effective_structured", []),
                self._state_get(state, "prepared_materials", {}),
                self._state_get(state, "added_features", []),
                self._state_get(state, "disputes", []),
                self._state_get(state, "evidence_assessments", []),
                self._state_get(state, "drafted_rejection_reasons", {}),
            )
            updates["claim_reviews"] = [
                item if isinstance(item, ClaimReviewItem) else ClaimReviewItem(**item)
                for item in claim_reviews
            ]
            updates["status"] = "completed"
            updates["progress"] = 94.0
            logger.info(f"完成 {len(updates['claim_reviews'])} 条权利要求评述")
        except Exception as exc:
            logger.error(f"逐条权利要求评述生成失败: {exc}")
            updates["errors"] = [{
                "node_name": "claim_review_drafting",
                "error_message": str(exc),
                "error_type": "claim_review_drafting_error",
            }]
            updates["status"] = "failed"

        return updates

    def _draft_claim_reviews(
        self,
        claims_effective_structured: List[Any],
        prepared_materials: Dict[str, Any],
        added_features: List[Any],
        disputes: List[Any],
        evidence_assessments: List[Any],
        drafted_rejection_reasons: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        claims = self._normalize_claims(claims_effective_structured)
        if not claims:
            return []

        prepared = self._to_dict(prepared_materials)
        office_action = self._to_dict(prepared.get("office_action", {}))
        paragraphs = [self._to_dict(item) for item in (office_action.get("paragraphs", []) or [])]
        features = [self._to_dict(item) for item in (added_features or [])]
        normalized_disputes = [self._to_dict(item) for item in (disputes or [])]
        normalized_assessments = [self._to_dict(item) for item in (evidence_assessments or [])]
        drafted_map = {
            str(key).strip(): str(value).strip()
            for key, value in self._to_dict(drafted_rejection_reasons).items()
            if str(key).strip()
        }

        feature_map = {
            str(item.get("feature_id", "")).strip(): item
            for item in features
            if str(item.get("feature_id", "")).strip()
        }
        assessment_by_dispute_id = {
            str(item.get("dispute_id", "")).strip(): item
            for item in normalized_assessments
            if str(item.get("dispute_id", "")).strip()
        }

        drafting_inputs: List[Dict[str, Any]] = []
        finalized: Dict[str, Dict[str, Any]] = {}
        for claim in claims:
            claim_id = str(claim.get("claim_id", "")).strip()
            claim_text = str(claim.get("claim_text", "")).strip()

            oa_materials = self._collect_oa_materials(claim_id, paragraphs)
            response_materials = self._collect_response_materials(
                claim_id,
                normalized_disputes,
                assessment_by_dispute_id,
                drafted_map,
            )
            amendment_materials = self._collect_amendment_materials(
                claim_id,
                normalized_disputes,
                assessment_by_dispute_id,
                feature_map,
            )

            review_mode = self._resolve_review_mode(oa_materials, response_materials, amendment_materials)
            source_summary = {
                "oa_paragraph_ids": [str(item.get("paragraph_id", "")).strip() for item in oa_materials],
                "response_dispute_ids": [str(item.get("dispute_id", "")).strip() for item in response_materials],
                "amendment_feature_ids": [str(item.get("feature_id", "")).strip() for item in amendment_materials],
            }

            if not oa_materials and not response_materials and not amendment_materials:
                finalized[claim_id] = {
                    "claim_id": claim_id,
                    "claim_text": claim_text,
                    "review_mode": review_mode,
                    "review_text": "当前未提取到可复用的权利要求评述。",
                    "source_summary": source_summary,
                }
                continue

            drafting_inputs.append(
                {
                    "claim_id": claim_id,
                    "claim_text": claim_text,
                    "review_mode": review_mode,
                    "source_summary": source_summary,
                    "oa_materials": oa_materials,
                    "response_materials": response_materials,
                    "amendment_materials": amendment_materials,
                }
            )

        if drafting_inputs:
            response = self.llm_service.invoke_text_json(
                messages=[
                    {"role": "system", "content": self._build_system_prompt()},
                    {"role": "user", "content": self._build_user_prompt(drafting_inputs)},
                ],
                task_kind="oar_claim_review_drafting",
                temperature=0.1,
            )
            finalized.update(self._normalize_llm_output(response, drafting_inputs))

        results: List[Dict[str, Any]] = []
        for claim in claims:
            claim_id = str(claim.get("claim_id", "")).strip()
            item = finalized.get(claim_id)
            if not item:
                item = {
                    "claim_id": claim_id,
                    "claim_text": str(claim.get("claim_text", "")).strip(),
                    "review_mode": "reused_oa",
                    "review_text": "当前未提取到可复用的权利要求评述。",
                    "source_summary": {
                        "oa_paragraph_ids": [],
                        "response_dispute_ids": [],
                        "amendment_feature_ids": [],
                    },
                }
            results.append(item)
        return results

    def _collect_oa_materials(self, claim_id: str, paragraphs: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        materials: List[Dict[str, str]] = []
        for paragraph in paragraphs:
            if str(paragraph.get("evaluation", "")).strip() != "negative":
                continue
            claim_ids = [str(item).strip() for item in (paragraph.get("claim_ids", []) or []) if str(item).strip()]
            if claim_id not in claim_ids:
                continue
            content = str(paragraph.get("content", "")).strip()
            if not content:
                continue
            materials.append(
                {
                    "paragraph_id": str(paragraph.get("paragraph_id", "")).strip(),
                    "content": content,
                    "issue_types": ",".join(str(item).strip() for item in (paragraph.get("issue_types", []) or [])),
                }
            )
        return materials

    def _collect_response_materials(
        self,
        claim_id: str,
        disputes: List[Dict[str, Any]],
        assessment_by_dispute_id: Dict[str, Dict[str, Any]],
        drafted_map: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        materials: List[Dict[str, Any]] = []
        for dispute in disputes:
            if str(dispute.get("origin", "response_dispute")).strip() != "response_dispute":
                continue
            dispute_claim_ids = self._normalize_claim_ids(dispute.get("claim_ids", []))
            if claim_id not in dispute_claim_ids:
                continue

            dispute_id = str(dispute.get("dispute_id", "")).strip()
            assessment_item = assessment_by_dispute_id.get(dispute_id, {})
            assessment = self._to_dict(assessment_item.get("assessment", {}))
            materials.append(
                {
                    "dispute_id": dispute_id,
                    "feature_text": str(dispute.get("feature_text", "")).strip(),
                    "applicant_reasoning": str(
                        self._to_dict(dispute.get("applicant_opinion", {})).get("reasoning", "")
                    ).strip(),
                    "assessment_reasoning": str(assessment.get("reasoning", "")).strip(),
                    "verdict": str(assessment.get("verdict", "")).strip(),
                    "final_examiner_rejection_reason": drafted_map.get(dispute_id, ""),
                }
            )
        return materials

    def _collect_amendment_materials(
        self,
        claim_id: str,
        disputes: List[Dict[str, Any]],
        assessment_by_dispute_id: Dict[str, Dict[str, Any]],
        feature_map: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        materials: List[Dict[str, Any]] = []
        for dispute in disputes:
            if str(dispute.get("origin", "")).strip() != "amendment_review":
                continue
            dispute_claim_ids = self._normalize_claim_ids(dispute.get("claim_ids", []))
            if claim_id not in dispute_claim_ids:
                continue

            dispute_id = str(dispute.get("dispute_id", "")).strip()
            feature_id = str(dispute.get("source_feature_id", "")).strip()
            assessment_item = assessment_by_dispute_id.get(dispute_id, {})
            assessment = self._to_dict(assessment_item.get("assessment", {}))
            feature = feature_map.get(feature_id, {})
            evidence_items = []
            for evidence in assessment_item.get("evidence", []) or []:
                evidence_dict = self._to_dict(evidence)
                evidence_items.append(
                    {
                        "doc_id": str(evidence_dict.get("doc_id", "")).strip(),
                        "quote": str(evidence_dict.get("quote", "")).strip(),
                        "analysis": str(evidence_dict.get("analysis", "")).strip(),
                    }
                )

            materials.append(
                {
                    "feature_id": feature_id,
                    "feature_text": str(dispute.get("feature_text", "")).strip(),
                    "source_type": str(feature.get("source_type", "")).strip(),
                    "source_claim_ids": self._normalize_claim_ids(feature.get("source_claim_ids", [])),
                    "target_claim_ids": self._normalize_claim_ids(feature.get("target_claim_ids", [])),
                    "assessment_reasoning": str(assessment.get("reasoning", "")).strip(),
                    "verdict": str(assessment.get("verdict", "")).strip(),
                    "examiner_rejection_rationale": str(assessment.get("examiner_rejection_rationale", "")).strip(),
                    "evidence": evidence_items[:3],
                }
            )
        return materials

    def _resolve_review_mode(
        self,
        oa_materials: List[Dict[str, Any]],
        response_materials: List[Dict[str, Any]],
        amendment_materials: List[Dict[str, Any]],
    ) -> str:
        if amendment_materials and response_materials:
            return "mixed"
        if amendment_materials:
            return "amendment_based"
        if response_materials:
            return "response_based"
        if oa_materials:
            return "reused_oa"
        return "reused_oa"

    def _build_system_prompt(self) -> str:
        return """你是一位资深的中国国家知识产权局专利实质审查员。你的任务是基于系统已经整理好的素材，为每一条当前生效权利要求生成一段正式、完整、可直接写入审查意见的评述文本。

【核心约束】
1. 只能依据输入素材重写，不得新增技术事实、对比文件、证据映射或法律判断。
2. 如果素材包含 `oa_materials`，应优先保留其原有评述结论和审查逻辑。
3. 如果素材包含 `amendment_materials`，应明确说明修改后特征对新颖性/创造性判断的影响。
4. 如果素材同时包含 `response_materials`，正文后半段再回应申请人的意见陈述。
5. 每条权利要求只输出一段评述，不拆成多段，不输出标题。
6. 行文必须是正式审查口吻，禁止“建议”“可以认为”“如果”等元话术。

【输出格式】
只能输出合法 JSON：
{
  "items": [
    {
      "claim_id": "1",
      "review_text": "关于权利要求1，……"
    }
  ]
}"""

    def _build_user_prompt(self, drafting_inputs: List[Dict[str, Any]]) -> str:
        return (
            "请按 claim_id 逐条生成正式评述，仅允许重组以下素材，不得新增事实：\n"
            f"{json.dumps(drafting_inputs, ensure_ascii=False, indent=2)}"
        )

    def _normalize_llm_output(
        self,
        response: Dict[str, Any],
        drafting_inputs: List[Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        output = self._to_dict(response)
        raw_items = output.get("items", [])
        if not isinstance(raw_items, list):
            raise ValueError("claim_review_drafting 输出非法: items 必须为数组")

        input_map = {
            str(item.get("claim_id", "")).strip(): item
            for item in drafting_inputs
            if str(item.get("claim_id", "")).strip()
        }
        result: Dict[str, Dict[str, Any]] = {}
        for item in raw_items:
            item_dict = self._to_dict(item)
            claim_id = str(item_dict.get("claim_id", "")).strip()
            if not claim_id or claim_id not in input_map:
                continue
            if claim_id in result:
                raise ValueError(f"claim_review_drafting 输出非法: claim_id={claim_id} 重复")
            review_text = str(item_dict.get("review_text", "")).strip()
            if not review_text:
                raise ValueError(f"claim_review_drafting 输出非法: claim_id={claim_id} 缺少 review_text")

            input_item = input_map[claim_id]
            result[claim_id] = {
                "claim_id": claim_id,
                "claim_text": str(input_item.get("claim_text", "")).strip(),
                "review_mode": str(input_item.get("review_mode", "")).strip() or "reused_oa",
                "review_text": review_text,
                "source_summary": self._to_dict(input_item.get("source_summary", {})),
            }

        missing = [claim_id for claim_id in input_map if claim_id not in result]
        if missing:
            raise ValueError(f"claim_review_drafting 输出缺少 claim_id: {missing}")
        return result

    def _normalize_claims(self, claims: List[Any]) -> List[Dict[str, str]]:
        normalized: List[Dict[str, str]] = []
        for item in claims or []:
            claim = self._to_dict(item)
            claim_id = str(claim.get("claim_id", "")).strip()
            if not claim_id:
                continue
            normalized.append(
                {
                    "claim_id": claim_id,
                    "claim_text": str(claim.get("claim_text", "")).strip(),
                }
            )
        return normalized

    def _normalize_claim_ids(self, value: Any) -> List[str]:
        claim_ids: List[str] = []
        candidates = value if isinstance(value, list) else [value]
        for raw in candidates:
            text = str(raw or "").strip()
            if not text:
                continue
            for piece in text.replace("，", ",").split(","):
                part = piece.strip()
                if not part:
                    continue
                if part.isdigit() and part not in claim_ids:
                    claim_ids.append(part)
        return claim_ids

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
