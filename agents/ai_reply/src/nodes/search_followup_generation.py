"""
补检/检索建议生成节点
基于现有结构化事实汇总出报告中的补检建议与检索要素表。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from loguru import logger

from agents.ai_reply.src.state import SearchFollowupSection
from agents.ai_reply.src.utils import PipelineCancelled, ensure_not_cancelled, get_node_cache
from agents.ai_reply.src.retrieval_followup import should_run_followup_by_assessment
from agents.ai_search.src.subagents.search_elements.normalize import (
    normalize_date_text,
    normalize_search_elements_payload,
)
from agents.common.utils.llm import get_llm_service


class SearchFollowupGenerationNode:
    """补检/检索建议生成节点"""

    CONFIDENCE_THRESHOLD = 0.6

    def __init__(self, config=None):
        self.config = config
        self.llm_service = get_llm_service()

    def __call__(self, state):
        logger.info("开始生成补检/检索建议")

        updates = {
            "current_node": "search_followup_generation",
            "status": "running",
            "progress": 93.0,
        }

        try:
            ensure_not_cancelled(self.config)
            cache = get_node_cache(self.config, "search_followup_generation")
            section = cache.run_step(
                "generate_search_followup_v1",
                self._generate_section,
                self._state_get(state, "topup_tasks", []),
                self._state_get(state, "evidence_assessments", []),
                self._state_get(state, "prepared_materials", {}),
                self._state_get(state, "claims_effective_structured", []),
                self._state_get(state, "disputes", []),
            )

            updates["search_followup_section"] = (
                section if isinstance(section, SearchFollowupSection) else SearchFollowupSection(**section)
            )
            updates["status"] = "completed"
            updates["progress"] = 94.0
            logger.info(
                "补检/检索建议生成完成: "
                f"needed={bool(self._item_get(updates['search_followup_section'], 'needed', False))}"
            )
        except PipelineCancelled as exc:
            logger.warning(f"补检/检索建议生成已取消: {exc}")
            updates["errors"] = [{
                "node_name": "search_followup_generation",
                "error_message": str(exc),
                "error_type": "cancelled",
            }]
            updates["status"] = "cancelled"
        except Exception as exc:
            logger.error(f"补检/检索建议生成失败: {exc}")
            updates["errors"] = [{
                "node_name": "search_followup_generation",
                "error_message": str(exc),
                "error_type": "search_followup_generation_error",
            }]
            updates["status"] = "failed"

        return updates

    def _generate_section(
        self,
        topup_tasks: List[Any],
        evidence_assessments: List[Any],
        prepared_materials: Dict[str, Any],
        claims_effective_structured: List[Any],
        disputes: List[Any],
    ) -> Dict[str, Any]:
        context = self._build_context(
            topup_tasks=topup_tasks,
            evidence_assessments=evidence_assessments,
            prepared_materials=prepared_materials,
            claims_effective_structured=claims_effective_structured,
            disputes=disputes,
        )
        if not context["needed"]:
            return self._build_not_needed_section(context)

        try:
            parsed = self.llm_service.invoke_text_json(
                messages=[
                    {"role": "system", "content": self._build_system_prompt()},
                    {"role": "user", "content": self._build_user_prompt(context)},
                ],
                task_kind="oar_search_followup_generation",
                temperature=0.05,
            )
        except Exception as exc:
            logger.warning(f"补检/检索建议 LLM 生成失败，回退到规则兜底: {exc}")
            return self._build_fallback_needed_section(context)

        return self._normalize_section(parsed, context)

    def _build_context(
        self,
        *,
        topup_tasks: List[Any],
        evidence_assessments: List[Any],
        prepared_materials: Dict[str, Any],
        claims_effective_structured: List[Any],
        disputes: List[Any],
    ) -> Dict[str, Any]:
        prepared = self._to_dict(prepared_materials)
        claims = [self._to_dict(item) for item in (claims_effective_structured or [])]
        evidence_map = {
            str(self._item_get(item, "dispute_id", "")).strip(): self._to_dict(item)
            for item in (evidence_assessments or [])
            if str(self._item_get(item, "dispute_id", "")).strip()
        }

        dispute_map = {
            str(self._item_get(item, "dispute_id", "")).strip(): self._to_dict(item)
            for item in (disputes or [])
            if str(self._item_get(item, "dispute_id", "")).strip()
        }

        context = {
            "application_number": self._extract_application_number(prepared),
            "applicants": self._extract_applicants(prepared),
            "filing_date": self._extract_filing_date(prepared),
            "priority_date": self._extract_priority_date(prepared),
            "current_notice_round": self._extract_current_notice_round(prepared),
            "original_patent_excerpt": self._extract_original_patent_excerpt(prepared),
            "comparison_document_ids": self._extract_comparison_document_ids(prepared),
            "subject_anchor": self._extract_subject_anchor(prepared, claims),
            "candidates": [],
            "trigger_reasons": [],
            "source_dispute_ids": [],
            "source_feature_ids": [],
        }

        seen_keys: set[tuple[str, str]] = set()
        trigger_reasons: List[str] = []
        source_dispute_ids: List[str] = []
        source_feature_ids: List[str] = []

        for item in topup_tasks or []:
            task = self._to_dict(item)
            feature_id = str(task.get("task_id", "")).strip()
            dispute_id = f"TOPUP_{feature_id}" if feature_id else ""
            evidence = evidence_map.get(dispute_id, {})
            candidate_reasons = ["存在新增特征需补充检索验证"]
            candidate_key = ("feature", feature_id or self._safe_text(task.get("search_feature_text") or task.get("feature_text")))
            if candidate_key in seen_keys:
                continue
            seen_keys.add(candidate_key)
            context["candidates"].append(
                self._build_candidate(
                    source_kind="topup_task",
                    source_dispute_id=dispute_id,
                    source_feature_id=feature_id,
                    claim_ids=self._normalize_claim_ids(task.get("claim_ids", [])),
                    feature_text=self._safe_text(task.get("feature_text")),
                    search_feature_text=self._safe_text(task.get("search_feature_text")) or self._safe_text(task.get("feature_text")),
                    claim_text=self._collect_claim_text(claims, task.get("claim_ids", [])),
                    candidate_reasons=candidate_reasons,
                    evidence_assessment=evidence,
                    dispute=dispute_map.get(dispute_id, {}),
                )
            )
            trigger_reasons = self._merge_unique(trigger_reasons, candidate_reasons)
            source_dispute_ids = self._merge_unique(source_dispute_ids, [dispute_id] if dispute_id else [])
            source_feature_ids = self._merge_unique(source_feature_ids, [feature_id] if feature_id else [])

        for dispute in disputes or []:
            dispute_dict = self._to_dict(dispute)
            dispute_id = self._safe_text(dispute_dict.get("dispute_id"))
            if not dispute_id:
                continue
            evidence = evidence_map.get(dispute_id, {})
            candidate_reasons = self._assessment_trigger_reasons(evidence)
            if not candidate_reasons:
                continue
            candidate_key = ("dispute", dispute_id)
            if candidate_key in seen_keys:
                continue
            seen_keys.add(candidate_key)
            feature_id = self._safe_text(dispute_dict.get("source_feature_id"))
            context["candidates"].append(
                self._build_candidate(
                    source_kind=str(dispute_dict.get("origin") or "response_dispute").strip() or "response_dispute",
                    source_dispute_id=dispute_id,
                    source_feature_id=feature_id,
                    claim_ids=self._normalize_claim_ids(dispute_dict.get("claim_ids", [])),
                    feature_text=self._safe_text(dispute_dict.get("feature_text")),
                    search_feature_text=self._safe_text(dispute_dict.get("feature_text")),
                    claim_text=self._collect_claim_text(claims, dispute_dict.get("claim_ids", [])),
                    candidate_reasons=candidate_reasons,
                    evidence_assessment=evidence,
                    dispute=dispute_dict,
                )
            )
            trigger_reasons = self._merge_unique(trigger_reasons, candidate_reasons)
            source_dispute_ids = self._merge_unique(source_dispute_ids, [dispute_id])
            source_feature_ids = self._merge_unique(source_feature_ids, [feature_id] if feature_id else [])

        context["trigger_reasons"] = trigger_reasons
        context["source_dispute_ids"] = source_dispute_ids
        context["source_feature_ids"] = source_feature_ids
        context["needed"] = bool(context["candidates"])
        context["default_objective"] = self._build_default_objective(context)
        return context

    def _build_candidate(
        self,
        *,
        source_kind: str,
        source_dispute_id: str,
        source_feature_id: str,
        claim_ids: List[str],
        feature_text: str,
        search_feature_text: str,
        claim_text: str,
        candidate_reasons: List[str],
        evidence_assessment: Dict[str, Any],
        dispute: Dict[str, Any],
    ) -> Dict[str, Any]:
        assessment = self._to_dict(self._item_get(evidence_assessment, "assessment", {}))
        trace = self._to_dict(self._item_get(evidence_assessment, "trace", {}))
        examiner_opinion = self._to_dict(self._item_get(dispute, "examiner_opinion", {}))
        applicant_opinion = self._to_dict(self._item_get(dispute, "applicant_opinion", {}))
        return {
            "source_kind": source_kind,
            "source_dispute_id": source_dispute_id,
            "source_feature_id": source_feature_id,
            "claim_ids": claim_ids,
            "claim_text": claim_text,
            "feature_text": feature_text,
            "search_feature_text": search_feature_text or feature_text,
            "trigger_reasons": candidate_reasons,
            "assessment": {
                "verdict": self._safe_text(assessment.get("verdict")),
                "confidence": self._safe_float(assessment.get("confidence")),
                "reasoning": self._safe_text(assessment.get("reasoning")),
            },
            "trace": {
                "used_doc_ids": self._normalize_string_list(trace.get("used_doc_ids")),
                "missing_doc_ids": self._normalize_string_list(trace.get("missing_doc_ids")),
                "has_followup_retrieval": bool(trace.get("followup_retrieval") or trace.get("followup_local_retrieval")),
            },
            "examiner_reasoning": self._safe_text(examiner_opinion.get("reasoning")),
            "applicant_reasoning": self._safe_text(applicant_opinion.get("reasoning")),
            "core_conflict": self._safe_text(applicant_opinion.get("core_conflict")),
        }

    def _assessment_trigger_reasons(self, evidence_assessment: Dict[str, Any]) -> List[str]:
        if not evidence_assessment:
            return []

        assessment = self._to_dict(self._item_get(evidence_assessment, "assessment", {}))
        trace = self._to_dict(self._item_get(evidence_assessment, "trace", {}))
        evidence = self._item_get(evidence_assessment, "evidence", []) or []
        used_doc_ids = self._normalize_string_list(trace.get("used_doc_ids"))

        reasons: List[str] = []
        verdict = self._safe_text(assessment.get("verdict"))
        confidence = self._safe_float(assessment.get("confidence"))
        if verdict == "INCONCLUSIVE":
            reasons.append("现有核查结论暂不确定")
        if confidence < self.CONFIDENCE_THRESHOLD:
            reasons.append("现有核查置信度偏低")
        if should_run_followup_by_assessment(
            assessment,
            used_doc_ids=used_doc_ids,
            evidence_cards=evidence if isinstance(evidence, list) else [],
            confidence_threshold=self.CONFIDENCE_THRESHOLD,
        ) and not used_doc_ids:
            reasons.append("当前证据支撑不足")
        if self._normalize_string_list(trace.get("missing_doc_ids")):
            reasons.append("存在缺失或不可用的对比文件")
        if (trace.get("followup_retrieval") or trace.get("followup_local_retrieval")) and (
            verdict == "INCONCLUSIVE" or confidence < self.CONFIDENCE_THRESHOLD
        ):
            reasons.append("补充检索后仍未形成稳定证据闭环")
        return self._merge_unique([], reasons)

    def _build_system_prompt(self) -> str:
        return """你是一位中国专利审查与现有技术检索专家。你的唯一任务是根据系统提供的结构化事实，生成一份“补检/检索建议”数据。

【核心目标】
1. 围绕当前未闭环争点或新增特征，提炼本轮补检目标。
2. 输出可执行的检索要素表，供人工补检或后续 AI 检索继续使用。
3. 明确缺口摘要与建议边界，但不得输出正式审查意见口吻。

【强制约束】
1. 只能基于输入事实生成，不得捏造申请人、日期、证据、技术效果或未出现的特征。
2. 不输出法律结论型建议，例如“建议维持驳回”“建议授权”。
3. 不输出正式通知书正文，不要使用命令式审查意见语气。
4. 若目标或有效检索要素不足，返回 status=\"needs_answer\"，并在 missing_items 中说明缺失项。
5. search_elements 仅输出结构化检索要素，不要扩写成长段说明。
6. `search_elements` 的 `block_id` 必须遵守以下约束：
   - `Block A`：最多 1 个，用于技术主题/应用场景锚点；如输入给出了 `subject_anchor`，优先围绕它生成或复用。
   - `Block B1..Bn`：本轮补检主体，围绕未闭环争点或新增特征逐项展开。
   - `Block C`：仅在确有必要时输出，用于辅助限定或降噪。
   - `Block E`：仅在效果词具有明确检索意义时输出，不是必选项。
7. 关键词扩展请参考高质量专利检索矩阵规则：
   - 中文扩展使用规范词、行业俗称、上下位概念或结构/功能等效替换。
   - 英文扩展使用专利英语词形变体，必要时可用截词符 `*`。
   - 单个词项中严禁写入 `OR/AND/NOT`。
   - 关键词必须是“词”或“短语”，不得输出整句。

【输出 JSON 结构】
{
  "status": "complete | needs_answer",
  "objective": "字符串",
  "applicants": ["申请人"],
  "filing_date": "YYYY-MM-DD 或空字符串",
  "priority_date": "YYYY-MM-DD 或空字符串",
  "missing_items": ["缺失项"],
  "gap_summaries": [
    {
      "claim_ids": ["1"],
      "feature_text": "关联特征",
      "gap_type": "insufficient_evidence | inconclusive | topup_feature | missing_document",
      "gap_summary": "为什么还需要补检",
      "source_dispute_id": "争议项编号",
      "source_feature_id": "修改项编号"
    }
  ],
  "search_elements": [
    {
      "element_name": "要素名称",
      "keywords_zh": ["中文词"],
      "keywords_en": ["英文词"],
      "block_id": "",
      "notes": "备注"
    }
  ],
  "suggested_constraints": {
    "notes": ["边界说明"],
    "comparison_document_ids": ["D1"]
  }
}
"""

    def _build_user_prompt(self, context: Dict[str, Any]) -> str:
        payload = {
            "task": "为 AI 答复报告生成补检/检索建议。",
            "source_context": {
                "application_number": context.get("application_number"),
                "applicants": context.get("applicants"),
                "filing_date": context.get("filing_date"),
                "priority_date": context.get("priority_date"),
                "current_notice_round": context.get("current_notice_round"),
                "comparison_document_ids": context.get("comparison_document_ids"),
                "subject_anchor": context.get("subject_anchor"),
            },
            "default_objective": context.get("default_objective"),
            "trigger_reasons": context.get("trigger_reasons"),
            "original_patent_excerpt": context.get("original_patent_excerpt"),
            "candidates": context.get("candidates"),
        }
        return (
            "请基于以下上下文，输出答复专用的补检/检索建议 JSON。"
            "重点是补检目标、缺口摘要和检索要素表，不要输出任何 JSON 之外的说明。\n\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )

    def _normalize_section(self, payload: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        normalized_search = normalize_search_elements_payload(
            {
                "status": payload.get("status"),
                "objective": self._safe_text(payload.get("objective")) or context.get("default_objective", ""),
                "applicants": payload.get("applicants") or context.get("applicants") or [],
                "filing_date": payload.get("filing_date") or context.get("filing_date"),
                "priority_date": payload.get("priority_date") or context.get("priority_date"),
                "missing_items": payload.get("missing_items") or [],
                "search_elements": payload.get("search_elements") or [],
            }
        )
        normalized_elements = self._normalize_followup_search_elements(
            normalized_search["search_elements"],
            context,
        )
        return {
            "needed": True,
            "status": normalized_search["status"],
            "objective": normalized_search["objective"],
            "applicants": normalized_search["applicants"],
            "filing_date": normalized_search["filing_date"],
            "priority_date": normalized_search["priority_date"],
            "missing_items": normalized_search["missing_items"],
            "trigger_reasons": context.get("trigger_reasons", []),
            "gap_summaries": self._normalize_gap_summaries(payload.get("gap_summaries"), context),
            "search_elements": normalized_elements,
            "suggested_constraints": self._normalize_suggested_constraints(payload.get("suggested_constraints"), context),
            "source_dispute_ids": context.get("source_dispute_ids", []),
            "source_feature_ids": context.get("source_feature_ids", []),
        }

    def _build_not_needed_section(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "needed": False,
            "status": "complete",
            "objective": "",
            "applicants": context.get("applicants", []),
            "filing_date": context.get("filing_date"),
            "priority_date": context.get("priority_date"),
            "missing_items": [],
            "trigger_reasons": [],
            "gap_summaries": [],
            "search_elements": [],
            "suggested_constraints": self._base_constraints(context),
            "source_dispute_ids": [],
            "source_feature_ids": [],
        }

    def _build_fallback_needed_section(self, context: Dict[str, Any]) -> Dict[str, Any]:
        fallback_elements = []
        for candidate in context.get("candidates", []):
            feature_text = self._safe_text(candidate.get("search_feature_text")) or self._safe_text(candidate.get("feature_text"))
            if not feature_text:
                continue
            fallback_elements.append(
                {
                    "element_name": feature_text[:80],
                    "keywords_zh": [feature_text],
                    "keywords_en": [],
                    "block_id": "",
                    "notes": "由现有未闭环争点自动回退生成",
                }
            )
        normalized_search = normalize_search_elements_payload(
            {
                "status": "complete",
                "objective": context.get("default_objective", ""),
                "applicants": context.get("applicants", []),
                "filing_date": context.get("filing_date"),
                "priority_date": context.get("priority_date"),
                "missing_items": [],
                "search_elements": fallback_elements,
            }
        )
        normalized_elements = self._normalize_followup_search_elements(
            normalized_search["search_elements"],
            context,
        )
        return {
            "needed": True,
            "status": normalized_search["status"],
            "objective": normalized_search["objective"],
            "applicants": normalized_search["applicants"],
            "filing_date": normalized_search["filing_date"],
            "priority_date": normalized_search["priority_date"],
            "missing_items": normalized_search["missing_items"],
            "trigger_reasons": context.get("trigger_reasons", []),
            "gap_summaries": self._fallback_gap_summaries(context),
            "search_elements": normalized_elements,
            "suggested_constraints": self._base_constraints(context),
            "source_dispute_ids": context.get("source_dispute_ids", []),
            "source_feature_ids": context.get("source_feature_ids", []),
        }

    def _normalize_gap_summaries(self, raw_items: Any, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        items = raw_items if isinstance(raw_items, list) else []
        normalized: List[Dict[str, Any]] = []
        for item in items:
            item_dict = self._to_dict(item)
            normalized.append(
                {
                    "claim_ids": self._normalize_claim_ids(item_dict.get("claim_ids", [])),
                    "feature_text": self._safe_text(item_dict.get("feature_text")),
                    "gap_type": self._safe_text(item_dict.get("gap_type")),
                    "gap_summary": self._safe_text(item_dict.get("gap_summary")),
                    "source_dispute_id": self._safe_text(item_dict.get("source_dispute_id")),
                    "source_feature_id": self._safe_text(item_dict.get("source_feature_id")),
                }
            )
        return normalized or self._fallback_gap_summaries(context)

    def _fallback_gap_summaries(self, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        outputs: List[Dict[str, Any]] = []
        for candidate in context.get("candidates", []):
            outputs.append(
                {
                    "claim_ids": self._normalize_claim_ids(candidate.get("claim_ids", [])),
                    "feature_text": self._safe_text(candidate.get("feature_text")),
                    "gap_type": self._infer_gap_type(candidate.get("trigger_reasons", []), candidate.get("source_kind", "")),
                    "gap_summary": self._safe_text(candidate.get("assessment", {}).get("reasoning"))
                    or "当前证据链尚未闭环，建议围绕该特征继续补检。",
                    "source_dispute_id": self._safe_text(candidate.get("source_dispute_id")),
                    "source_feature_id": self._safe_text(candidate.get("source_feature_id")),
                }
            )
        return outputs

    def _normalize_suggested_constraints(self, raw_constraints: Any, context: Dict[str, Any]) -> Dict[str, Any]:
        constraints = raw_constraints if isinstance(raw_constraints, dict) else {}
        normalized = dict(constraints)
        normalized.update(self._base_constraints(context))
        notes = normalized.get("notes")
        normalized["notes"] = self._merge_unique(
            self._normalize_string_list(notes),
            [
                f"请优先围绕当前未闭环争点对应的权利要求与新增特征补强证据。",
            ],
        )
        return normalized

    def _base_constraints(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "application_number": context.get("application_number") or "",
            "current_notice_round": context.get("current_notice_round") or 0,
            "applicants": context.get("applicants") or [],
            "filing_date": context.get("filing_date"),
            "priority_date": context.get("priority_date"),
            "comparison_document_ids": context.get("comparison_document_ids") or [],
        }

    def _build_default_objective(self, context: Dict[str, Any]) -> str:
        application_number = self._safe_text(context.get("application_number"))
        if application_number:
            return f"围绕申请号 {application_number} 当前答复中尚未闭环的争点和新增特征补充检索可用证据。"
        return "围绕当前答复中尚未闭环的争点和新增特征补充检索可用证据。"

    def _normalize_followup_search_elements(
        self,
        raw_elements: List[Dict[str, Any]],
        context: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        has_block_a = False
        next_b_index = 1

        for item in raw_elements or []:
            item_dict = self._to_dict(item)
            if not item_dict:
                continue
            block_id = self._normalize_block_id(item_dict.get("block_id"), next_b_index)
            normalized_item = dict(item_dict)
            normalized_item["block_id"] = block_id
            normalized.append(normalized_item)
            if block_id == "A":
                has_block_a = True
            if block_id.startswith("B"):
                try:
                    next_b_index = max(next_b_index, int(block_id[1:]) + 1)
                except Exception:
                    next_b_index += 1

        if not has_block_a:
            block_a_item = self._build_subject_anchor_element(context)
            if block_a_item:
                normalized.insert(0, block_a_item)

        return normalized

    def _normalize_block_id(self, raw_block_id: Any, next_b_index: int) -> str:
        block_id = self._safe_text(raw_block_id).upper()
        if block_id == "A":
            return "A"
        if block_id == "C":
            return "C"
        if block_id == "E":
            return "E"
        if block_id == "B":
            return f"B{next_b_index}"
        if block_id.startswith("B") and block_id[1:].isdigit():
            return block_id
        return f"B{next_b_index}"

    def _build_subject_anchor_element(self, context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        subject_anchor = self._safe_text(context.get("subject_anchor"))
        if not subject_anchor:
            return None
        return {
            "element_name": subject_anchor,
            "keywords_zh": [subject_anchor],
            "keywords_en": [],
            "block_id": "A",
            "notes": "技术主题锚点",
        }

    def _extract_application_number(self, prepared_materials: Dict[str, Any]) -> str:
        office_action = self._to_dict(prepared_materials.get("office_action", {}))
        if self._safe_text(office_action.get("application_number")):
            return self._safe_text(office_action.get("application_number"))
        original_patent = self._to_dict(prepared_materials.get("original_patent", {}))
        return self._safe_text(original_patent.get("application_number"))

    def _extract_applicants(self, prepared_materials: Dict[str, Any]) -> List[str]:
        original_patent = self._to_dict(prepared_materials.get("original_patent", {}))
        patent_data = self._to_dict(original_patent.get("data", {}))
        bibliographic_data = self._to_dict(patent_data.get("bibliographic_data", {}))
        applicants = bibliographic_data.get("applicants")
        outputs: List[str] = []
        if isinstance(applicants, str):
            applicants = [applicants]
        for item in applicants or []:
            text = self._safe_text(item.get("name") if isinstance(item, dict) else item)
            if text and text not in outputs:
                outputs.append(text)
        return outputs

    def _extract_filing_date(self, prepared_materials: Dict[str, Any]) -> Optional[str]:
        original_patent = self._to_dict(prepared_materials.get("original_patent", {}))
        patent_data = self._to_dict(original_patent.get("data", {}))
        bibliographic_data = self._to_dict(patent_data.get("bibliographic_data", {}))
        for value in [
            patent_data.get("application_date"),
            bibliographic_data.get("application_date"),
            bibliographic_data.get("filing_date"),
        ]:
            normalized = normalize_date_text(value)
            if normalized:
                return normalized
        return None

    def _extract_priority_date(self, prepared_materials: Dict[str, Any]) -> Optional[str]:
        original_patent = self._to_dict(prepared_materials.get("original_patent", {}))
        patent_data = self._to_dict(original_patent.get("data", {}))
        bibliographic_data = self._to_dict(patent_data.get("bibliographic_data", {}))
        for value in [
            patent_data.get("priority_date"),
            patent_data.get("application_date"),
            bibliographic_data.get("priority_date"),
            bibliographic_data.get("application_date"),
        ]:
            normalized = normalize_date_text(value)
            if normalized:
                return normalized
        return None

    def _extract_current_notice_round(self, prepared_materials: Dict[str, Any]) -> int:
        office_action = self._to_dict(prepared_materials.get("office_action", {}))
        try:
            return int(office_action.get("current_notice_round", 0) or 0)
        except Exception:
            return 0

    def _extract_original_patent_excerpt(self, prepared_materials: Dict[str, Any]) -> str:
        original_patent = self._to_dict(prepared_materials.get("original_patent", {}))
        patent_data = self._to_dict(original_patent.get("data", {}))
        description = self._to_dict(patent_data.get("description", {}))
        detailed = self._safe_text(description.get("detailed_description"))
        if detailed:
            return detailed[:1200]
        abstract = self._safe_text(patent_data.get("abstract"))
        claims = patent_data.get("claims") if isinstance(patent_data.get("claims"), list) else []
        claim_texts: List[str] = []
        for item in claims[:5]:
            claim = self._to_dict(item)
            text = self._safe_text(claim.get("claim_text"))
            if text:
                claim_texts.append(text)
        return "\n".join([part for part in [abstract, "\n".join(claim_texts)] if part])[:1200]

    def _extract_subject_anchor(
        self,
        prepared_materials: Dict[str, Any],
        claims_effective_structured: List[Dict[str, Any]],
    ) -> str:
        original_patent = self._to_dict(prepared_materials.get("original_patent", {}))
        patent_data = self._to_dict(original_patent.get("data", {}))
        bibliographic_data = self._to_dict(patent_data.get("bibliographic_data", {}))

        candidates: List[str] = [
            self._safe_text(bibliographic_data.get("invention_title")),
            self._safe_text(patent_data.get("title")),
            self._safe_text(patent_data.get("abstract")),
        ]
        for item in claims_effective_structured or []:
            claim = self._to_dict(item)
            claim_id = self._safe_text(claim.get("claim_id"))
            if claim_id == "1":
                candidates.append(self._safe_text(claim.get("claim_text")))
                break
        for candidate in candidates:
            anchor = self._derive_subject_anchor_text(candidate)
            if anchor:
                return anchor
        return ""

    def _derive_subject_anchor_text(self, text: str) -> str:
        cleaned = self._safe_text(text)
        if not cleaned:
            return ""
        if len(cleaned) <= 24 and "，" not in cleaned and "," not in cleaned and "。" not in cleaned:
            return cleaned

        patterns = [
            r"一种([^，。；：,:]{2,24}?)(?:，|。|；|：|,|:|包括|其特征在于)",
            r"一类([^，。；：,:]{2,24}?)(?:，|。|；|：|,|:|包括|其特征在于)",
        ]
        for pattern in patterns:
            match = re.search(pattern, cleaned)
            if match:
                return f"一种{match.group(1).strip()}" if pattern.startswith("一种") else f"一类{match.group(1).strip()}"
        return cleaned[:24]

    def _extract_comparison_document_ids(self, prepared_materials: Dict[str, Any]) -> List[str]:
        outputs: List[str] = []
        for item in prepared_materials.get("comparison_documents", []) or []:
            doc_id = self._safe_text(self._item_get(item, "document_id"))
            if doc_id and doc_id not in outputs:
                outputs.append(doc_id)
        return outputs

    def _collect_claim_text(self, claims: List[Dict[str, Any]], claim_ids: Any) -> str:
        normalized_ids = self._normalize_claim_ids(claim_ids)
        if not normalized_ids:
            return ""
        claim_map = {
            self._safe_text(item.get("claim_id")): self._safe_text(item.get("claim_text"))
            for item in claims
            if self._safe_text(item.get("claim_id"))
        }
        texts: List[str] = []
        for claim_id in normalized_ids:
            text = claim_map.get(claim_id, "")
            if text:
                texts.append(f"权利要求{claim_id}: {text}")
        return "\n".join(texts)

    def _infer_gap_type(self, reasons: List[str], source_kind: str) -> str:
        reason_text = " ".join(reasons)
        if "缺失" in reason_text or "不可用" in reason_text:
            return "missing_document"
        if source_kind == "topup_task":
            return "topup_feature"
        if "暂不确定" in reason_text:
            return "inconclusive"
        return "insufficient_evidence"

    def _normalize_claim_ids(self, value: Any) -> List[str]:
        outputs: List[str] = []
        candidates = value if isinstance(value, list) else [value]
        for raw in candidates:
            text = self._safe_text(raw)
            if not text:
                continue
            for part in text.replace("权利要求", "").replace("，", ",").split(","):
                normalized = self._safe_text(part)
                if normalized.isdigit() and normalized not in outputs:
                    outputs.append(normalized)
        return outputs

    def _normalize_string_list(self, values: Any) -> List[str]:
        outputs: List[str] = []
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            return outputs
        for item in values:
            text = self._safe_text(item)
            if text and text not in outputs:
                outputs.append(text)
        return outputs

    def _merge_unique(self, target: List[str], values: List[str]) -> List[str]:
        merged = list(target)
        for value in values:
            text = self._safe_text(value)
            if text and text not in merged:
                merged.append(text)
        return merged

    def _safe_text(self, value: Any) -> str:
        return str(value or "").strip()

    def _safe_float(self, value: Any) -> float:
        try:
            return float(value or 0.0)
        except Exception:
            return 0.0

    def _state_get(self, state: Any, key: str, default=None):
        if isinstance(state, dict):
            return state.get(key, default)
        return getattr(state, key, default)

    def _item_get(self, item: Any, key: str, default=None):
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)

    def _to_dict(self, value: Any) -> Dict[str, Any]:
        if isinstance(value, dict):
            return value
        if hasattr(value, "model_dump"):
            return value.model_dump()
        if hasattr(value, "dict"):
            return value.dict()
        return {}
