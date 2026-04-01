"""
基于上一轮OA的重组评述生成节点。
"""

import json
from typing import Any, Dict, List, Set

from loguru import logger

from agents.ai_reply.src.state import ReviewUnit
from agents.ai_reply.src.utils import get_node_cache
from agents.common.utils.llm import get_llm_service


class ClaimReviewDraftingNode:
    """生成第4部分重组评述单元。"""

    def __init__(self, config=None):
        self.config = config
        self.llm_service = get_llm_service()

    def __call__(self, state):
        logger.info("开始生成基于上一轮OA的重组评述")
        updates = {
            "current_node": "claim_review_drafting",
            "status": "running",
            "progress": 92.0,
        }

        try:
            cache = get_node_cache(self.config, "claim_review_drafting")
            review_units = cache.run_step(
                "draft_review_units_v2",
                self._draft_review_units,
                self._state_get(state, "claims_old_structured", []),
                self._state_get(state, "claims_effective_structured", []),
                self._state_get(state, "prepared_materials", {}),
                self._state_get(state, "added_features", []),
                self._state_get(state, "disputes", []),
                self._state_get(state, "evidence_assessments", []),
                self._state_get(state, "drafted_rejection_reasons", {}),
            )
            updates["review_units"] = [
                item if isinstance(item, ReviewUnit) else ReviewUnit(**item)
                for item in review_units
            ]
            updates["status"] = "completed"
            updates["progress"] = 94.0
            logger.info(f"完成 {len(updates['review_units'])} 个重组评述单元")
        except Exception as exc:
            logger.error(f"重组评述生成失败: {exc}")
            updates["errors"] = [{
                "node_name": "claim_review_drafting",
                "error_message": str(exc),
                "error_type": "claim_review_drafting_error",
            }]
            updates["status"] = "failed"

        return updates

    def _draft_review_units(
        self,
        claims_old_structured: List[Any],
        claims_effective_structured: List[Any],
        prepared_materials: Dict[str, Any],
        added_features: List[Any],
        disputes: List[Any],
        evidence_assessments: List[Any],
        drafted_rejection_reasons: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        old_claims = self._normalize_claims(claims_old_structured)
        effective_claims = self._normalize_claims(claims_effective_structured)
        if not effective_claims:
            return []

        old_map = {item["claim_id"]: item for item in old_claims}
        effective_map = {item["claim_id"]: item for item in effective_claims}

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
        dispute_by_feature_id = {
            str(item.get("source_feature_id", "")).strip(): item
            for item in normalized_disputes
            if str(item.get("source_feature_id", "")).strip()
        }
        assessment_by_dispute_id = {
            str(item.get("dispute_id", "")).strip(): item
            for item in normalized_assessments
            if str(item.get("dispute_id", "")).strip()
        }
        assessment_by_feature_id = {}
        for item in normalized_assessments:
            feature_id = str(item.get("source_feature_id", "")).strip()
            if feature_id:
                assessment_by_feature_id[feature_id] = item

        merge_target_by_source = self._build_merge_target_map(features, effective_map)
        unit_specs: List[Dict[str, Any]] = []
        unit_by_id: Dict[str, Dict[str, Any]] = {}
        independent_unit_by_anchor: Dict[str, Dict[str, Any]] = {}
        paragraph_order: Dict[str, int] = {}

        paragraph_candidates = []
        for index, paragraph in enumerate(paragraphs):
            paragraph_id = str(paragraph.get("paragraph_id", "")).strip() or f"P{index + 1}"
            paragraph["paragraph_id"] = paragraph_id
            paragraph_order[paragraph_id] = index
            paragraph_candidates.append(paragraph)

        for claim in effective_claims:
            if claim["claim_type"] != "independent":
                continue
            paragraph = self._find_primary_independent_paragraph(claim["claim_id"], paragraph_candidates, old_map)
            if not paragraph:
                continue
            paragraph_id = str(paragraph.get("paragraph_id", "")).strip()
            paragraph_claim_ids = self._paragraph_claim_ids(paragraph, old_map)
            unit = self._build_unit_spec(
                unit_id=paragraph_id,
                unit_type="evidence_restructured",
                source_paragraph_ids=[paragraph_id],
                display_claim_ids=[claim["claim_id"]],
                anchor_claim_id=claim["claim_id"],
                oa_materials=[self._paragraph_material(paragraph, [claim["claim_id"]], paragraph_claim_ids)],
                claim_snapshots=self._build_claim_snapshots([claim["claim_id"]], effective_map, old_map),
                paragraph_order=paragraph_order.get(paragraph_id, 0),
            )
            independent_unit_by_anchor[claim["claim_id"]] = unit
            unit_by_id[unit["unit_id"]] = unit
            unit_specs.append(unit)

        primary_paragraph_ids = {
            unit["unit_id"] for unit in independent_unit_by_anchor.values() if unit["unit_id"] in paragraph_order
        }

        for paragraph in paragraph_candidates:
            paragraph_id = str(paragraph.get("paragraph_id", "")).strip()
            if paragraph_id in primary_paragraph_ids:
                continue
            paragraph_claim_ids = self._paragraph_claim_ids(paragraph, old_map)
            if not paragraph_claim_ids:
                continue

            merged_source_ids = [claim_id for claim_id in paragraph_claim_ids if claim_id in merge_target_by_source]
            for source_claim_id in merged_source_ids:
                target_claim_id = merge_target_by_source[source_claim_id]
                if target_claim_id not in effective_map:
                    continue
                independent_unit = independent_unit_by_anchor.get(target_claim_id)
                if not independent_unit:
                    independent_unit = self._build_unit_spec(
                        unit_id=f"IND_{target_claim_id}",
                        unit_type="evidence_restructured",
                        source_paragraph_ids=[],
                        display_claim_ids=[target_claim_id],
                        anchor_claim_id=target_claim_id,
                        oa_materials=[],
                        claim_snapshots=self._build_claim_snapshots([target_claim_id], effective_map, old_map),
                        paragraph_order=paragraph_order.get(paragraph_id, 0),
                    )
                    independent_unit_by_anchor[target_claim_id] = independent_unit
                    unit_by_id[independent_unit["unit_id"]] = independent_unit
                    unit_specs.append(independent_unit)
                self._append_paragraph_to_unit(
                    independent_unit,
                    paragraph,
                    [source_claim_id],
                    paragraph_claim_ids,
                    paragraph_order.get(paragraph_id, 0),
                )
                self._append_unique(
                    independent_unit["source_summary"]["merged_source_claim_ids"],
                    source_claim_id,
                )

            residual_claim_ids = [
                claim_id
                for claim_id in paragraph_claim_ids
                if claim_id in effective_map and claim_id not in merge_target_by_source
            ]
            if not residual_claim_ids:
                continue
            anchor_claim_id = self._first_claim_id_by_effective_order(residual_claim_ids, effective_claims)
            residual_unit = self._build_unit_spec(
                unit_id=paragraph_id,
                unit_type="dependent_group_restructured",
                source_paragraph_ids=[paragraph_id],
                display_claim_ids=residual_claim_ids,
                anchor_claim_id=anchor_claim_id,
                oa_materials=[self._paragraph_material(paragraph, residual_claim_ids, paragraph_claim_ids)],
                claim_snapshots=self._build_claim_snapshots(residual_claim_ids, effective_map, old_map),
                paragraph_order=paragraph_order.get(paragraph_id, 0),
            )
            unit_by_id[residual_unit["unit_id"]] = residual_unit
            unit_specs.append(residual_unit)

        covered_effective_claim_ids = {
            claim_id
            for unit in unit_specs
            for claim_id in self._normalize_claim_ids(unit.get("display_claim_ids", []))
        }
        for claim in effective_claims:
            claim_id = claim["claim_id"]
            if claim["claim_type"] != "independent":
                continue
            if claim_id in covered_effective_claim_ids:
                continue
            if not self._claim_has_related_material(claim_id, normalized_disputes, features):
                continue
            new_unit = self._build_unit_spec(
                unit_id=f"IND_{claim_id}",
                unit_type="supplemented_new",
                source_paragraph_ids=[],
                display_claim_ids=[claim_id],
                anchor_claim_id=claim_id,
                oa_materials=[],
                claim_snapshots=self._build_claim_snapshots([claim_id], effective_map, old_map),
                paragraph_order=self._insertion_order_for_claim(claim_id, effective_claims, paragraph_candidates),
            )
            independent_unit_by_anchor[claim_id] = new_unit
            unit_by_id[new_unit["unit_id"]] = new_unit
            unit_specs.append(new_unit)
            covered_effective_claim_ids.add(claim_id)

        for claim in effective_claims:
            claim_id = claim["claim_id"]
            if claim_id in covered_effective_claim_ids:
                continue
            if not self._claim_has_related_material(claim_id, normalized_disputes, features):
                continue
            new_unit = self._build_unit_spec(
                unit_id=f"CLM_{claim_id}",
                unit_type="supplemented_new",
                source_paragraph_ids=[],
                display_claim_ids=[claim_id],
                anchor_claim_id=claim_id,
                oa_materials=[],
                claim_snapshots=self._build_claim_snapshots([claim_id], effective_map, old_map),
                paragraph_order=self._insertion_order_for_claim(claim_id, effective_claims, paragraph_candidates),
            )
            unit_by_id[new_unit["unit_id"]] = new_unit
            unit_specs.append(new_unit)

        drafting_inputs: List[Dict[str, Any]] = []
        finalized: Dict[str, Dict[str, Any]] = {}
        for unit in unit_specs:
            display_claim_ids = unit["display_claim_ids"]
            response_materials = self._collect_response_materials(
                display_claim_ids,
                normalized_disputes,
                assessment_by_dispute_id,
                drafted_map,
            )
            amendment_materials = self._collect_amendment_materials(
                display_claim_ids,
                features,
                dispute_by_feature_id,
                assessment_by_feature_id,
            )
            source_summary = self._to_dict(unit.get("source_summary", {}))
            source_summary["response_dispute_ids"] = [
                str(item.get("dispute_id", "")).strip()
                for item in response_materials
                if str(item.get("dispute_id", "")).strip()
            ]
            source_summary["added_feature_ids"] = [
                str(item.get("feature_id", "")).strip()
                for item in amendment_materials
                if str(item.get("feature_id", "")).strip()
            ]
            source_summary["amendment_feature_ids"] = [
                str(item.get("feature_id", "")).strip()
                for item in amendment_materials
                if str(item.get("feature_id", "")).strip()
            ]
            unit["source_summary"] = source_summary
            unit["review_before_text"] = self._build_direct_review_text(unit.get("oa_materials", []))

            if not unit["oa_materials"] and not response_materials and not amendment_materials:
                continue

            if not self._should_use_llm(unit, response_materials, amendment_materials):
                finalized[unit["unit_id"]] = self._finalize_unit(
                    unit,
                    self._build_direct_review_text(unit["oa_materials"]),
                )
                continue

            drafting_inputs.append(
                {
                    "unit_id": unit["unit_id"],
                    "unit_type": unit["unit_type"],
                    "title": unit["title"],
                    "source_paragraph_ids": unit["source_paragraph_ids"],
                    "display_claim_ids": display_claim_ids,
                    "anchor_claim_id": unit["anchor_claim_id"],
                    "source_summary": source_summary,
                    "review_before_text": unit.get("review_before_text", ""),
                    "claim_snapshots": unit["claim_snapshots"],
                    "oa_materials": unit["oa_materials"],
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

        unit_specs.sort(key=lambda item: (float(item.get("paragraph_order", 0)), self._claim_sort_key(item.get("anchor_claim_id", ""))))
        return [finalized[unit["unit_id"]] for unit in unit_specs if unit["unit_id"] in finalized]

    def _should_use_llm(
        self,
        unit: Dict[str, Any],
        response_materials: List[Dict[str, Any]],
        amendment_materials: List[Dict[str, Any]],
    ) -> bool:
        unit_type = str(unit.get("unit_type", "")).strip()
        if unit_type == "supplemented_new":
            return True
        if unit_type == "dependent_group_restructured":
            return True
        if unit_type == "evidence_restructured":
            source_summary = self._to_dict(unit.get("source_summary", {}))
            return bool(
                response_materials
                or amendment_materials
                or source_summary.get("merged_source_claim_ids", [])
            )
        return False

    def _build_direct_review_text(self, oa_materials: List[Dict[str, Any]]) -> str:
        contents: List[str] = []
        for item in oa_materials or []:
            content = str(item.get("content", "")).strip()
            if content and content not in contents:
                contents.append(content)
        return "\n".join(contents) if contents else "当前未提取到可复用的审查评述。"

    def _build_merge_target_map(
        self,
        features: List[Dict[str, Any]],
        effective_map: Dict[str, Dict[str, Any]],
    ) -> Dict[str, str]:
        result: Dict[str, str] = {}
        for feature in features:
            if str(feature.get("source_type", "")).strip() != "claim":
                continue
            target_claim_ids = self._normalize_claim_ids(feature.get("target_claim_ids", []))
            if not target_claim_ids:
                continue
            anchor_claim_id = self._resolve_anchor_claim_id(target_claim_ids[0], effective_map)
            if not anchor_claim_id:
                continue
            for source_claim_id in self._normalize_claim_ids(feature.get("source_claim_ids", [])):
                result[source_claim_id] = anchor_claim_id
        return result

    def _find_primary_independent_paragraph(
        self,
        claim_id: str,
        paragraphs: List[Dict[str, Any]],
        old_map: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        for paragraph in paragraphs:
            paragraph_claim_ids = self._paragraph_claim_ids(paragraph, old_map)
            if paragraph_claim_ids == [claim_id]:
                return paragraph
        return {}

    def _paragraph_claim_ids(
        self,
        paragraph: Dict[str, Any],
        old_map: Dict[str, Dict[str, Any]],
    ) -> List[str]:
        return [
            claim_id
            for claim_id in self._normalize_claim_ids(paragraph.get("claim_ids", []))
            if claim_id in old_map
        ]

    def _append_paragraph_to_unit(
        self,
        unit: Dict[str, Any],
        paragraph: Dict[str, Any],
        focused_claim_ids: List[str],
        original_claim_ids: List[str],
        paragraph_order: float,
    ) -> None:
        paragraph_id = str(paragraph.get("paragraph_id", "")).strip()
        self._append_unique(unit["source_paragraph_ids"], paragraph_id)
        self._append_unique(unit["source_summary"]["source_paragraph_ids"], paragraph_id)
        unit["oa_materials"].append(self._paragraph_material(paragraph, focused_claim_ids, original_claim_ids))
        unit["paragraph_order"] = min(float(unit.get("paragraph_order", paragraph_order)), float(paragraph_order))

    def _append_unique(self, target: List[str], value: str) -> None:
        text = str(value or "").strip()
        if text and text not in target:
            target.append(text)

    def _build_unit_spec(
        self,
        unit_id: str,
        unit_type: str,
        source_paragraph_ids: List[str],
        display_claim_ids: List[str],
        anchor_claim_id: str,
        oa_materials: List[Dict[str, Any]],
        claim_snapshots: List[Dict[str, Any]],
        paragraph_order: float,
    ) -> Dict[str, Any]:
        return {
            "unit_id": unit_id,
            "unit_type": unit_type,
            "source_paragraph_ids": source_paragraph_ids,
            "display_claim_ids": display_claim_ids,
            "anchor_claim_id": anchor_claim_id,
            "title": self._build_unit_title(display_claim_ids),
            "review_before_text": self._build_direct_review_text(oa_materials),
            "oa_materials": oa_materials,
            "claim_snapshots": claim_snapshots,
            "source_summary": {
                "source_paragraph_ids": list(source_paragraph_ids),
                "merged_source_claim_ids": [],
                "added_feature_ids": [],
            },
            "paragraph_order": paragraph_order,
        }

    def _paragraph_material(
        self,
        paragraph: Dict[str, Any],
        focused_claim_ids: List[str],
        original_claim_ids: List[str],
    ) -> Dict[str, Any]:
        return {
            "paragraph_id": str(paragraph.get("paragraph_id", "")).strip(),
            "content": str(paragraph.get("content", "")).strip(),
            "focused_claim_ids": focused_claim_ids,
            "original_claim_ids": original_claim_ids,
        }

    def _build_claim_snapshots(
        self,
        claim_ids: List[str],
        effective_map: Dict[str, Dict[str, Any]],
        old_map: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        snapshots: List[Dict[str, Any]] = []
        for claim_id in claim_ids:
            claim = effective_map.get(claim_id, {})
            if not claim:
                continue
            old_claim = old_map.get(claim_id, {})
            snapshots.append(
                {
                    "claim_id": claim_id,
                    "claim_before_text": str(old_claim.get("claim_text", "")).strip(),
                    "claim_text": str(claim.get("claim_text", "")).strip(),
                    "claim_type": str(claim.get("claim_type", "")).strip() or "unknown",
                }
            )
        return snapshots

    def _collect_response_materials(
        self,
        claim_ids: List[str],
        disputes: List[Dict[str, Any]],
        assessment_by_dispute_id: Dict[str, Dict[str, Any]],
        drafted_map: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        materials: List[Dict[str, Any]] = []
        claim_id_set = set(claim_ids)
        for dispute in disputes:
            if str(dispute.get("origin", "response_dispute")).strip() != "response_dispute":
                continue
            dispute_claim_ids = self._normalize_claim_ids(dispute.get("claim_ids", []))
            if not claim_id_set.intersection(dispute_claim_ids):
                continue
            dispute_id = str(dispute.get("dispute_id", "")).strip()
            assessment_item = assessment_by_dispute_id.get(dispute_id, {})
            assessment = self._to_dict(assessment_item.get("assessment", {}))
            materials.append(
                {
                    "dispute_id": dispute_id,
                    "claim_ids": dispute_claim_ids,
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
        claim_ids: List[str],
        features: List[Dict[str, Any]],
        dispute_by_feature_id: Dict[str, Dict[str, Any]],
        assessment_by_feature_id: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        materials: List[Dict[str, Any]] = []
        claim_id_set = set(claim_ids)
        for feature in features:
            feature_id = str(feature.get("feature_id", "")).strip()
            target_claim_ids = self._normalize_claim_ids(feature.get("target_claim_ids", []))
            if not feature_id or not claim_id_set.intersection(target_claim_ids):
                continue
            dispute = dispute_by_feature_id.get(feature_id, {})
            dispute_claim_ids = self._normalize_claim_ids(dispute.get("claim_ids", [])) or target_claim_ids
            assessment_item = assessment_by_feature_id.get(feature_id, {})
            assessment = self._to_dict(assessment_item.get("assessment", {}))
            materials.append(
                {
                    "feature_id": feature_id,
                    "claim_ids": dispute_claim_ids,
                    "feature_text": str(feature.get("feature_text", "")).strip(),
                    "feature_before_text": str(feature.get("feature_before_text", "")).strip(),
                    "feature_after_text": str(feature.get("feature_after_text", "")).strip(),
                    "source_type": str(feature.get("source_type", "")).strip(),
                    "source_claim_ids": self._normalize_claim_ids(feature.get("source_claim_ids", [])),
                    "target_claim_ids": target_claim_ids,
                    "assessment_reasoning": str(assessment.get("reasoning", "")).strip(),
                    "verdict": str(assessment.get("verdict", "")).strip(),
                    "examiner_rejection_rationale": str(assessment.get("examiner_rejection_rationale", "")).strip(),
                }
            )
        return materials

    def _claim_has_related_material(
        self,
        claim_id: str,
        disputes: List[Dict[str, Any]],
        features: List[Dict[str, Any]],
    ) -> bool:
        for dispute in disputes:
            if claim_id in self._normalize_claim_ids(dispute.get("claim_ids", [])):
                return True
        for feature in features:
            if claim_id in self._normalize_claim_ids(feature.get("target_claim_ids", [])):
                return True
        return False

    def _resolve_anchor_claim_id(
        self,
        claim_id: str,
        effective_map: Dict[str, Dict[str, Any]],
    ) -> str:
        current = effective_map.get(claim_id, {})
        visited: Set[str] = set()
        while current:
            current_id = str(current.get("claim_id", "")).strip()
            if not current_id or current_id in visited:
                return current_id
            visited.add(current_id)
            claim_type = str(current.get("claim_type", "")).strip()
            parent_ids = self._normalize_claim_ids(current.get("parent_claim_ids", []))
            if claim_type == "independent" or not parent_ids:
                return current_id
            parent_id = parent_ids[0]
            current = effective_map.get(parent_id, {})
        return claim_id

    def _insertion_order_for_claim(
        self,
        claim_id: str,
        effective_claims: List[Dict[str, Any]],
        negative_paragraphs: List[Dict[str, Any]],
    ) -> float:
        last_paragraph_order = float(len(negative_paragraphs) + 1)
        for index, claim in enumerate(effective_claims):
            if claim["claim_id"] == claim_id:
                return last_paragraph_order + (index / 100.0)
        return last_paragraph_order + 9.0

    def _first_claim_id_by_effective_order(
        self,
        claim_ids: List[str],
        effective_claims: List[Dict[str, Any]],
    ) -> str:
        wanted = set(claim_ids)
        for claim in effective_claims:
            if claim["claim_id"] in wanted:
                return claim["claim_id"]
        return claim_ids[0] if claim_ids else ""

    def _build_unit_title(self, claim_ids: List[str]) -> str:
        label = "、".join(f"权利要求{claim_id}" for claim_id in claim_ids if claim_id)
        return label or "补充评述"

    def _build_system_prompt(self) -> str:
        return """你是一名资深的中国专利实质审查员。你的任务是基于“上一轮审查意见（OA）”、“申请人的答复/修改”以及“审查员评估结论”，为下一轮OA撰写正式的正文评述。

【核心工作原则】
1. 绝对忠实于素材：必须严格基于提供的 oa_materials、response_materials 和 amendment_materials 进行重写或扩写。严禁捏造任何未提及的技术特征、对比文件（如对比文件1、2等）、法律依据或审查结论。
2. 逻辑严密连贯：输出的必须是最终可直接写入OA通知书的正文段落，语言必须客观、严谨、专业，符合中国专利审查规范。
3. 纯净输出：只需输出每个评述单元的评述正文。不要输出单元标题，不要输出“审查员认为”之类的开场白，整合成一段连贯流畅的文本（避免生硬的要点罗列）。

【针对不同单元类型的处理约束】
- type = "evidence_restructured" (独权主卡重组)：
  该单元代表某个独权体系的唯一主卡。你必须以 oa_materials 为评述骨架，将原独权评述、并入从权评述以及 response/amendment 评估素材整合成一段新的正式评述。若 oa_materials 中同时包含独权与并入从权的内容，必须自然融合，不得拆开写。
- type = "dependent_group_restructured" (残余从权组重组)：
  原OA可能将多个从权放在一起评述，而其中部分从权已经并入独权主卡。你必须只保留当前 `display_claim_ids` 对应的剩余从权逻辑，剔除已被抽走的从权内容，生成一段只针对剩余从权组的正式评述。
- type = "supplemented_new" (新增或修改特征补充)：
  原OA中没有该评述。你必须完全依赖 amendment_materials 或 response_materials 中的审查员评估结论（assessment_reasoning 和 verdict）来撰写。清晰指出新增特征是什么，以及它为何不能/能够克服先前的缺陷。

【输出格式约束】
必须输出纯净、合法的 JSON 对象。严禁使用 Markdown 代码块包裹（不要输出 ```json），直接输出 JSON 文本。为保证推理质量，请先在内部字段给出简短分析，再输出最终文本。
JSON 格式规范如下：
{
  "items": [
    {
      "unit_id": "必须与输入的 unit_id 完全一致",
      "rationale": "简要陈述你的处理思路（限50字内，辅助生成高质量文本）",
      "review_text": "最终的评述正文。直接是一段完整的话，不加任何标题。"
    }
  ]
}"""

    def _build_user_prompt(self, drafting_inputs: List[Dict[str, Any]]) -> str:
        # 在用户提示词中增加对输入数据结构的简要说明，帮助大模型更好地理解上下文
        return (
            "请作为专利审查员，仔细阅读以下来自上一轮审查和本轮修改评估的素材。\n"
            "请严格按照 unit_id 逐条生成正式评述，并组装成要求的 JSON 格式。\n"
            "【注意】：如果素材中提到“克服了缺陷”或“具备创造性”，你的评述结论必须与其一致；如果结论是“未克服”，请阐明驳回理由。\n\n"
            "=== 待处理的评述单元素材 ===\n"
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
            str(item.get("unit_id", "")).strip(): item
            for item in drafting_inputs
            if str(item.get("unit_id", "")).strip()
        }
        result: Dict[str, Dict[str, Any]] = {}
        for item in raw_items:
            item_dict = self._to_dict(item)
            unit_id = str(item_dict.get("unit_id", "")).strip()
            if not unit_id or unit_id not in input_map:
                continue
            if unit_id in result:
                raise ValueError(f"claim_review_drafting 输出非法: unit_id={unit_id} 重复")
            review_text = str(item_dict.get("review_text", "")).strip()
            if not review_text:
                raise ValueError(f"claim_review_drafting 输出非法: unit_id={unit_id} 缺少 review_text")
            result[unit_id] = self._finalize_unit(input_map[unit_id], review_text)

        missing = [unit_id for unit_id in input_map if unit_id not in result]
        if missing:
            raise ValueError(f"claim_review_drafting 输出缺少 unit_id: {missing}")
        return result

    def _finalize_unit(self, unit: Dict[str, Any], review_text: str) -> Dict[str, Any]:
        return {
            "unit_id": str(unit.get("unit_id", "")).strip(),
            "unit_type": str(unit.get("unit_type", "")).strip() or "evidence_restructured",
            "source_paragraph_ids": self._normalize_claim_ids(unit.get("source_paragraph_ids", []), keep_non_digit=True),
            "display_claim_ids": self._normalize_claim_ids(unit.get("display_claim_ids", [])),
            "anchor_claim_id": str(unit.get("anchor_claim_id", "")).strip(),
            "title": str(unit.get("title", "")).strip() or self._build_unit_title(unit.get("display_claim_ids", [])),
            "review_before_text": str(unit.get("review_before_text", "")).strip(),
            "review_text": review_text,
            "claim_snapshots": [self._to_dict(item) for item in (unit.get("claim_snapshots", []) or [])],
            "source_summary": self._to_dict(unit.get("source_summary", {})),
        }

    def _normalize_claims(self, claims: List[Any]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for item in claims or []:
            claim = self._to_dict(item)
            claim_id = str(claim.get("claim_id", "")).strip()
            if not claim_id:
                continue
            normalized.append(
                {
                    "claim_id": claim_id,
                    "claim_text": str(claim.get("claim_text", "")).strip(),
                    "claim_type": str(claim.get("claim_type", "")).strip() or "unknown",
                    "parent_claim_ids": self._normalize_claim_ids(claim.get("parent_claim_ids", [])),
                }
            )
        normalized.sort(key=lambda item: self._claim_sort_key(item["claim_id"]))
        return normalized

    def _claim_sort_key(self, value: str) -> tuple[int, str]:
        text = str(value or "").strip()
        return (int(text), text) if text.isdigit() else (10**9, text)

    def _normalize_claim_ids(self, value: Any, keep_non_digit: bool = False) -> List[str]:
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
                if part.isdigit() or keep_non_digit:
                    if part not in claim_ids:
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
