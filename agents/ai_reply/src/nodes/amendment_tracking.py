"""
修改差异分析节点
拆分输出新旧权项对齐、实质性修改和结构性调整。
"""

import json
import re
from typing import Any, Dict, List

from loguru import logger

from agents.common.utils.llm import get_llm_service
from agents.ai_reply.src.state import (
    ClaimAlignment,
    StructuralAdjustment,
    StructuredClaim,
    SubstantiveAmendment,
)
from agents.ai_reply.src.utils import PipelineCancelled, ensure_not_cancelled, get_node_cache


class AmendmentTrackingNode:
    """修改差异分析节点（LLM主判实质修改，规则识别结构调整）"""

    def __init__(self, config=None):
        self.config = config
        self.llm_service = get_llm_service()

    def __call__(self, state):
        logger.info("开始修改差异分析")
        updates = {
            "current_node": "amendment_tracking",
            "status": "running",
            "progress": 56.0,
        }

        try:
            ensure_not_cancelled(self.config)
            cache = get_node_cache(self.config, "amendment_tracking")
            result = cache.run_step(
                "track_amendment_v11",
                self._track_amendment,
                self._state_get(state, "prepared_materials", {}),
                self._state_get(state, "claims_previous_structured", []),
                self._state_get(state, "claims_current_structured", []),
            )

            updates["claims_old_structured"] = [
                item if isinstance(item, StructuredClaim) else StructuredClaim(**item)
                for item in result.get("claims_old_structured", [])
            ]
            updates["claims_effective_structured"] = [
                item if isinstance(item, StructuredClaim) else StructuredClaim(**item)
                for item in result.get("claims_effective_structured", [])
            ]
            updates["claims_old_source"] = str(result.get("claims_old_source", "")).strip()
            updates["claims_old_source_reason"] = str(result.get("claims_old_source_reason", "")).strip()
            updates["claim_alignments"] = [
                item if isinstance(item, ClaimAlignment) else ClaimAlignment(**item)
                for item in result.get("claim_alignments", [])
            ]
            updates["has_claim_amendment"] = bool(result.get("has_claim_amendment", False))
            updates["substantive_amendments"] = [
                item if isinstance(item, SubstantiveAmendment) else SubstantiveAmendment(**item)
                for item in result.get("substantive_amendments", [])
            ]
            updates["structural_adjustments"] = [
                item if isinstance(item, StructuralAdjustment) else StructuralAdjustment(**item)
                for item in result.get("structural_adjustments", [])
            ]
            updates["status"] = "completed"
            updates["progress"] = 60.0
            logger.info(
                "修改差异分析完成，实质修改数: {}，结构调整数: {}".format(
                    len(updates["substantive_amendments"]),
                    len(updates["structural_adjustments"]),
                )
            )
        except PipelineCancelled as e:
            logger.warning(f"修改差异分析节点已取消: {e}")
            updates["errors"] = [{
                "node_name": "amendment_tracking",
                "error_message": str(e),
                "error_type": "cancelled",
            }]
            updates["status"] = "cancelled"
        except Exception as e:
            logger.error(f"修改差异分析失败: {e}")
            updates["errors"] = [{
                "node_name": "amendment_tracking",
                "error_message": str(e),
                "error_type": "amendment_tracking_error",
            }]
            updates["status"] = "failed"

        return updates

    def _track_amendment(self, prepared_materials, previous_claims, current_claims) -> Dict[str, Any]:
        prepared = self._to_dict(prepared_materials)
        previous_claims_list = [self._to_dict(item) for item in (previous_claims or [])]
        current_claims_list = [self._to_dict(item) for item in (current_claims or [])]
        current_notice_round = self._extract_current_notice_round(prepared)
        old_claims, claims_old_source, claims_old_source_reason = self._resolve_old_claims(
            prepared,
            previous_claims_list,
            current_notice_round,
        )
        effective_claims = current_claims_list or old_claims

        if not current_claims_list:
            return {
                "claims_old_structured": old_claims,
                "claims_effective_structured": effective_claims,
                "claims_old_source": claims_old_source,
                "claims_old_source_reason": claims_old_source_reason,
                "claim_alignments": [],
                "has_claim_amendment": False,
                "substantive_amendments": [],
                "structural_adjustments": [],
            }

        claim_alignments = self._build_claim_alignments(old_claims, effective_claims)
        changed_claims_pairs = self._build_changed_claim_pairs(old_claims, effective_claims, claim_alignments)
        full_old_claims_context = {
            str(claim.get("claim_id", "")): str(claim.get("claim_text", "")).strip()
            for claim in old_claims
            if str(claim.get("claim_id", "")).strip()
        }

        substantive_amendments: List[Dict[str, Any]] = []
        if changed_claims_pairs:
            payload = {
                "summary": {
                    "old_claim_count": len(old_claims),
                    "new_claim_count": len(effective_claims),
                    "changed_claim_count": len(changed_claims_pairs),
                },
                "changed_claim_ids": [item["claim_id"] for item in changed_claims_pairs],
                "changed_claims_pairs": changed_claims_pairs,
                "full_old_claims_context": full_old_claims_context,
            }
            response = self.llm_service.invoke_text_json(
                messages=[
                    {"role": "system", "content": self._build_system_prompt()},
                    {"role": "user", "content": self._build_user_prompt(payload)},
                ],
                task_kind="oar_amendment_tracking",
                temperature=0.05,
            )
            normalized = self._normalize_tracking_result(response)
            substantive_amendments = normalized["substantive_amendments"]

        claim_alignments = self._refresh_alignment_reasons(claim_alignments, old_claims, substantive_amendments)
        structural_adjustments = self._extract_structural_adjustments(
            old_claims,
            effective_claims,
            claim_alignments,
        )
        has_claim_amendment = bool(substantive_amendments or structural_adjustments)

        return {
            "claims_old_structured": old_claims,
            "claims_effective_structured": effective_claims,
            "claims_old_source": claims_old_source,
            "claims_old_source_reason": claims_old_source_reason,
            "claim_alignments": claim_alignments,
            "has_claim_amendment": has_claim_amendment,
            "substantive_amendments": substantive_amendments,
            "structural_adjustments": structural_adjustments,
        }

    def _build_system_prompt(self) -> str:
        return """你是资深的中国专利代理师和专利局审查员，精通《专利审查指南》。
你的唯一任务是：精准比对【修改前和修改后的权利要求】，提取其中的“实质性技术修改”。

### 核心定义
你需要从修改后的权利要求中，提取出新增的或发生实质性改变的技术特征。必须完全忽略：
1. 纯粹的结构性调整（如：删除某权项导致的编号顺延、引用关系联动变化）。
2. 纯粹的文字润色（如：错别字修改、标点符号修改、语序调整）。
3. 不改变技术实质的同义词替换（例如：“支撑件”修改为“支撑结构”，实质未变，应当忽略）。
4. 单纯的特征删除：如果修改仅仅是删除了原有的某个技术特征（扩大了保护范围），请直接忽略，不要输出。

### 严格的修改类型（仅限2种）
1. 从权特征并入
   - 触发条件：新权利要求中新增的特征，其实质内容在【任一旧权利要求】中已经明确记载。
   - 特殊场景：如果旧权利要求以并列备选项、Markush 式列举或“至少一个”的方式记载了多个候选限定，而新权利要求仅保留其中某一项或某一组具体项，这种“缩小范围/选取特定并列项”的修改，仍属于从权特征并入。
   - 来源判断必须以【旧权利要求明确记载的技术限定内容】为准，而不是以新权利要求为了成句加入的语法承接表达为准。
   - 如果旧权利要求仅公开了某个被限定的对象、参数、备选项或范围，而新权利要求在表述时加入了“输入”“接收”“控制”“用于”“包括”等外层动作或连接性措辞，则应将该并入特征概括为旧权利要求真正提供的核心限定内容，不要把这些外层措辞一并当作来源于旧权利要求的内容。
   - amendment_kind 必须为: "claim_feature_merge"
   - content_origin 必须为: "old_claim"
   - source_claim_ids: 必须准确填写来源的旧权利要求编号（例如 ["5"]，绝对不能为空）。

2. 说明书记载补入
   - 触发条件：新权利要求中新增的特征，在【所有旧权利要求】中均未记载，属于从说明书中新引入的实质性特征。
   - amendment_kind 必须为: "spec_feature_addition"
   - content_origin 必须为: "specification"
   - source_claim_ids: 必须为严格的空数组 []。

### 字段提取粒度要求（极其重要）
- 必须按【单条完整技术特征】粒度拆分，通常对应一个完整的动宾结构、控制关系或结构连接关系。如果并入了两个不同来源的特征，必须拆分为两个独立的 JSON 对象。
- amendment_id: 按顺序生成，如 "A1", "A2"。
- feature_text: 必须写成纯粹的、客观的技术特征陈述，不能写修改动作摘要。严禁使用“增加了”“变更为”“限定为”“并入了”等动作型元语言；应优先概括技术事实本身与核心限定变化，而不是“修改行为”。
- feature_text 示例：
  - 错误示范："增加了基于RRC值控制目标加速度的逻辑"
  - 正确示范："基于轮胎的RRC值控制车辆的目标加速度"
- feature_before_text: 旧特征原文片段。如果是说明书补入则严格填 ""；如果是从权并入，摘录来源旧权项的原文。
- feature_after_text: 新特征原文片段。必须是精简的词组或分句，严禁返回整条权利要求的全文！应优先体现新增技术限定的核心内容，避免带入新权利要求为适配 claim 句法而出现的外层动作或连接性措辞。
- 不要为了规避上述要求而把一个原本单一的技术限定拆得更细；保持适度概括，维持单条技术特征粒度稳定。

### 期望输出格式
必须直接输出合法的 JSON 对象。你的输出必须以 `{` 开头，以 `}` 结尾，绝对不要包含 ```json 等任何 Markdown 标记。
示例：
{
  "substantive_amendments": [
    {
      "amendment_id": "A1",
      "target_claim_ids": ["1"],
      "amendment_kind": "claim_feature_merge",
      "content_origin": "old_claim",
      "source_claim_ids": ["3"],
      "feature_text": "显示器材质为柔性OLED",
      "feature_before_text": "所述显示器为柔性OLED屏",
      "feature_after_text": "且所述显示器采用柔性OLED材质"
    },
    {
      "amendment_id": "A2",
      "target_claim_ids": ["1"],
      "amendment_kind": "spec_feature_addition",
      "content_origin": "specification",
      "source_claim_ids": [],
      "feature_text": "外壳表面设有厚度为0.1mm的防水涂层",
      "feature_before_text": "",
      "feature_after_text": "外壳表面设有厚度为0.1mm的防水涂层"
    }
  ]
}

若未发现任何实质性新增特征，请严格输出：{"substantive_amendments": []}"""

    def _build_user_prompt(self, payload: Dict[str, Any]) -> str:
        return f"""请作为资深专利代理师，分析以下权利要求修改数据。

【分析指引与特殊处理】
1. 关注 `changed_claims_pairs`：`target_claim_ids` 应填写发生变动的新权项的 `claim_id`。先对比 `new_text` 和 `old_text`，识别所有新增或发生实质变化的技术点。
2. 全局检索溯源：对每个新增技术点，务必在 `full_old_claims_context` (旧权全文) 中全局搜寻。找得到就是 `claim_feature_merge`，找不到就是 `spec_feature_addition`。如果旧权以并列备选项、Markush 式列举或“至少一个”公开多个候选限定，而新权只保留其中一项，也应判定为 `claim_feature_merge`。
3. 客观改写结果：完成溯源后，再将 `feature_text` 改写为客观技术事实，禁止出现“将A修改为B”“增加了”等元语言；`feature_after_text` 只能填写精简词组或分句，不能返回整条权利要求。
4. 全新增加的权利要求：如果某 pair 的 `old_text` 为空，说明这是一个全新添加的权利要求。仍然要先提炼该新权项中的核心技术特征，再去旧权文中找源头，判断是合并而来还是说明书引入。

【差异数据】
{json.dumps(payload, ensure_ascii=False, indent=2)}

请严格遵循 JSON 格式直接输出结果："""

    def _normalize_tracking_result(self, response: Dict[str, Any]) -> Dict[str, Any]:
        result = self._to_dict(response)
        amendments_raw = result.get("substantive_amendments", [])
        if not isinstance(amendments_raw, list):
            raise ValueError("amendment_tracking 输出格式错误：substantive_amendments 不是列表")

        amendments: List[Dict[str, Any]] = []
        for item in amendments_raw:
            amendment = self._to_dict(item)
            amendment_id = str(amendment.get("amendment_id", "")).strip()
            feature_text = str(amendment.get("feature_text", "")).strip()
            search_feature_text = str(amendment.get("search_feature_text", "")).strip() or feature_text
            feature_before_text = str(amendment.get("feature_before_text", "")).strip()
            feature_after_text = str(amendment.get("feature_after_text", "")).strip() or feature_text
            amendment_kind = str(amendment.get("amendment_kind", "")).strip()
            content_origin = str(amendment.get("content_origin", "")).strip()
            if not amendment_id or not feature_text:
                raise ValueError("amendment_tracking 输出非法 substantive_amendments 项，缺少 amendment_id 或 feature_text")
            if amendment_kind not in {"claim_feature_merge", "spec_feature_addition"}:
                raise ValueError(f"amendment_tracking 输出非法 amendment_kind: {amendment_kind}")
            if content_origin not in {"old_claim", "specification"}:
                raise ValueError(f"amendment_tracking 输出非法 content_origin: {content_origin}")

            target_claim_ids = [
                str(claim_id).strip()
                for claim_id in (amendment.get("target_claim_ids", []) or [])
                if str(claim_id).strip()
            ]
            if not target_claim_ids:
                raise ValueError("amendment_tracking 输出非法 substantive_amendments 项，缺少 target_claim_ids")
            source_claim_ids = [
                str(claim_id).strip()
                for claim_id in (amendment.get("source_claim_ids", []) or [])
                if str(claim_id).strip()
            ]
            if amendment_kind == "claim_feature_merge":
                if content_origin != "old_claim":
                    raise ValueError("claim_feature_merge 必须对应 content_origin=old_claim")
                if not source_claim_ids:
                    raise ValueError("claim_feature_merge 必须提供 source_claim_ids")
            else:
                if content_origin != "specification":
                    raise ValueError("spec_feature_addition 必须对应 content_origin=specification")
                source_claim_ids = []

            amendments.append(
                {
                    "amendment_id": amendment_id,
                    "target_claim_ids": target_claim_ids,
                    "amendment_kind": amendment_kind,
                    "content_origin": content_origin,
                    "source_claim_ids": source_claim_ids,
                    "feature_text": feature_text,
                    "search_feature_text": search_feature_text,
                    "feature_before_text": feature_before_text,
                    "feature_after_text": feature_after_text,
                }
            )

        return {
            "substantive_amendments": amendments,
        }

    def _build_claim_alignments(
        self,
        old_claims: List[Dict[str, Any]],
        new_claims: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        old_map = {str(item.get("claim_id", "")).strip(): item for item in old_claims if str(item.get("claim_id", "")).strip()}
        new_map = {str(item.get("claim_id", "")).strip(): item for item in new_claims if str(item.get("claim_id", "")).strip()}
        old_ids_sorted = self._sort_claim_ids(list(old_map.keys()))
        new_ids_sorted = self._sort_claim_ids(list(new_map.keys()))

        alignments: List[Dict[str, Any]] = []
        used_old_claim_ids = set()

        for claim_id in new_ids_sorted:
            new_claim = self._to_dict(new_map.get(claim_id, {}))
            new_text = str(new_claim.get("claim_text", "")).strip()
            old_claim = self._to_dict(old_map.get(claim_id, {}))
            old_text = str(old_claim.get("claim_text", "")).strip()

            if old_text:
                if self._normalize_text(old_text) == self._normalize_text(new_text):
                    used_old_claim_ids.add(claim_id)
                    alignments.append(
                        {
                            "claim_id": claim_id,
                            "old_claim_id": claim_id,
                            "alignment_kind": "same_number_match",
                            "reason": "unchanged",
                        }
                    )
                    continue
                if self._canonicalize_claim_text_for_alignment(old_text) == self._canonicalize_claim_text_for_alignment(new_text):
                    used_old_claim_ids.add(claim_id)
                    alignments.append(
                        {
                            "claim_id": claim_id,
                            "old_claim_id": claim_id,
                            "alignment_kind": "same_number_match",
                            "reason": "unchanged",
                        }
                    )
                    continue

            new_text_canonical = self._canonicalize_claim_text_for_alignment(new_text)
            new_claim_type = str(new_claim.get("claim_type", "")).strip()
            canonical_candidates = [
                old_id
                for old_id in old_ids_sorted
                if old_id not in used_old_claim_ids
                and self._canonicalize_claim_text_for_alignment(
                    str(self._to_dict(old_map.get(old_id, {})).get("claim_text", "")).strip()
                ) == new_text_canonical
                and self._claim_alignment_candidate_is_compatible(
                    new_claim_id=claim_id,
                    new_claim=new_claim,
                    old_claim_id=old_id,
                    old_claim=self._to_dict(old_map.get(old_id, {})),
                    require_parent_compatibility=(new_claim_type == "dependent"),
                )
            ]
            if len(canonical_candidates) == 1:
                matched_old_id = canonical_candidates[0]
                used_old_claim_ids.add(matched_old_id)
                alignments.append(
                    {
                        "claim_id": claim_id,
                        "old_claim_id": matched_old_id,
                        "alignment_kind": "renumbered_successor",
                        "reason": "upstream_deleted",
                    }
                )
                continue

            body_canonical_candidates = self._find_body_canonical_candidates(
                claim_id=claim_id,
                new_claim=new_claim,
                old_map=old_map,
                old_ids_sorted=old_ids_sorted,
                used_old_claim_ids=used_old_claim_ids,
            )
            if len(body_canonical_candidates) == 1:
                matched_old_id = body_canonical_candidates[0]
                used_old_claim_ids.add(matched_old_id)
                alignments.append(
                    {
                        "claim_id": claim_id,
                        "old_claim_id": matched_old_id,
                        "alignment_kind": "renumbered_successor",
                        "reason": "upstream_deleted",
                    }
                )
                continue

            if old_text:
                used_old_claim_ids.add(claim_id)
                alignments.append(
                    {
                        "claim_id": claim_id,
                        "old_claim_id": claim_id,
                        "alignment_kind": "same_number_match",
                        "reason": "unchanged",
                    }
                )
                continue

            alignments.append(
                {
                    "claim_id": claim_id,
                    "old_claim_id": "",
                    "alignment_kind": "new_claim",
                    "reason": "newly_added",
                }
            )

        return alignments

    def _find_body_canonical_candidates(
        self,
        claim_id: str,
        new_claim: Dict[str, Any],
        old_map: Dict[str, Dict[str, Any]],
        old_ids_sorted: List[str],
        used_old_claim_ids: set[str],
    ) -> List[str]:
        new_text_body = self._canonicalize_claim_body_for_alignment(new_claim.get("claim_text", ""))
        if not new_text_body:
            return []

        candidates: List[str] = []
        current_claim_key = self._claim_sort_key(claim_id)
        for old_id in old_ids_sorted:
            if old_id in used_old_claim_ids:
                continue
            if self._claim_sort_key(old_id) < current_claim_key:
                continue
            old_claim = self._to_dict(old_map.get(old_id, {}))
            if not self._claim_alignment_candidate_is_compatible(
                new_claim_id=claim_id,
                new_claim=new_claim,
                old_claim_id=old_id,
                old_claim=old_claim,
                require_parent_compatibility=True,
            ):
                continue
            old_text_body = self._canonicalize_claim_body_for_alignment(old_claim.get("claim_text", ""))
            if old_text_body and old_text_body == new_text_body:
                candidates.append(old_id)
        return candidates

    def _claim_alignment_candidate_is_compatible(
        self,
        new_claim_id: str,
        new_claim: Dict[str, Any],
        old_claim_id: str,
        old_claim: Dict[str, Any],
        require_parent_compatibility: bool,
    ) -> bool:
        new_claim_type = str(new_claim.get("claim_type", "")).strip()
        old_claim_type = str(old_claim.get("claim_type", "")).strip()
        if new_claim_type and old_claim_type and new_claim_type != old_claim_type:
            return False
        if require_parent_compatibility and new_claim_type == "dependent" and old_claim_type == "dependent":
            return self._dependent_parent_shift_compatible(
                new_claim_id=new_claim_id,
                new_parent_ids=self._normalize_claim_ids(new_claim.get("parent_claim_ids", [])),
                old_claim_id=old_claim_id,
                old_parent_ids=self._normalize_claim_ids(old_claim.get("parent_claim_ids", [])),
            )
        return True

    def _build_changed_claim_pairs(
        self,
        old_claims: List[Dict[str, Any]],
        new_claims: List[Dict[str, Any]],
        claim_alignments: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        old_map = {str(item.get("claim_id", "")).strip(): item for item in old_claims if str(item.get("claim_id", "")).strip()}
        new_map = {str(item.get("claim_id", "")).strip(): item for item in new_claims if str(item.get("claim_id", "")).strip()}
        changed_claims_pairs: List[Dict[str, str]] = []

        for alignment in claim_alignments:
            claim_id = str(alignment.get("claim_id", "")).strip()
            old_claim_id = str(alignment.get("old_claim_id", "")).strip()
            new_text = str(self._to_dict(new_map.get(claim_id, {})).get("claim_text", "")).strip()
            old_text = str(self._to_dict(old_map.get(old_claim_id, {})).get("claim_text", "")).strip()

            if str(alignment.get("alignment_kind", "")).strip() == "new_claim":
                changed_claims_pairs.append(
                    {
                        "claim_id": claim_id,
                        "old_text": "",
                        "new_text": new_text,
                    }
                )
                continue

            if self._normalize_text(old_text) == self._normalize_text(new_text):
                continue
            if self._canonicalize_claim_text_for_alignment(old_text) == self._canonicalize_claim_text_for_alignment(new_text):
                continue
            if self._canonicalize_claim_body_for_alignment(old_text) == self._canonicalize_claim_body_for_alignment(new_text):
                continue

            changed_claims_pairs.append(
                {
                    "claim_id": claim_id,
                    "old_text": old_text,
                    "new_text": new_text,
                }
            )

        return changed_claims_pairs

    def _refresh_alignment_reasons(
        self,
        claim_alignments: List[Dict[str, Any]],
        old_claims: List[Dict[str, Any]],
        substantive_amendments: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        old_ids_sorted = self._sort_claim_ids(
            [str(item.get("claim_id", "")).strip() for item in old_claims if str(item.get("claim_id", "")).strip()]
        )
        matched_old_ids = {
            str(item.get("old_claim_id", "")).strip()
            for item in claim_alignments
            if str(item.get("old_claim_id", "")).strip()
        }
        merged_source_ids = {
            claim_id
            for amendment in substantive_amendments
            if str(amendment.get("amendment_kind", "")).strip() == "claim_feature_merge"
            for claim_id in self._normalize_claim_ids(amendment.get("source_claim_ids", []))
        }

        refreshed: List[Dict[str, Any]] = []
        for alignment in claim_alignments:
            item = dict(alignment)
            old_claim_id = str(item.get("old_claim_id", "")).strip()
            if not old_claim_id or str(item.get("reason", "")).strip() == "newly_added":
                refreshed.append(item)
                continue
            inferred = self._infer_alignment_reason(old_claim_id, old_ids_sorted, matched_old_ids, merged_source_ids)
            item["reason"] = inferred
            if (
                str(item.get("alignment_kind", "")).strip() == "same_number_match"
                and old_claim_id == str(item.get("claim_id", "")).strip()
                and inferred == "unchanged"
            ):
                item["reason"] = "unchanged"
            refreshed.append(item)
        return refreshed

    def _infer_alignment_reason(
        self,
        old_claim_id: str,
        old_ids_sorted: List[str],
        matched_old_ids: set[str],
        merged_source_ids: set[str],
    ) -> str:
        shifted_out_old_ids: List[str] = []
        for candidate in old_ids_sorted:
            if self._claim_sort_key(candidate) >= self._claim_sort_key(old_claim_id):
                break
            if candidate not in matched_old_ids:
                shifted_out_old_ids.append(candidate)
        if any(candidate in merged_source_ids for candidate in shifted_out_old_ids):
            return "upstream_merged"
        if shifted_out_old_ids:
            return "upstream_deleted"
        return "unchanged"

    def _extract_structural_adjustments(
        self,
        old_claims: List[Dict[str, Any]],
        new_claims: List[Dict[str, Any]],
        claim_alignments: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        old_map = {str(item.get("claim_id", "")).strip(): item for item in old_claims if str(item.get("claim_id", "")).strip()}
        new_map = {str(item.get("claim_id", "")).strip(): item for item in new_claims if str(item.get("claim_id", "")).strip()}
        adjustments: List[Dict[str, Any]] = []
        next_index = 1

        for alignment in claim_alignments:
            claim_id = str(alignment.get("claim_id", "")).strip()
            old_claim_id = str(alignment.get("old_claim_id", "")).strip()
            alignment_kind = str(alignment.get("alignment_kind", "")).strip()
            reason = str(alignment.get("reason", "")).strip()
            if not old_claim_id or reason not in {"upstream_deleted", "upstream_merged"}:
                continue

            new_claim = self._to_dict(new_map.get(claim_id, {}))
            claim_type = str(new_claim.get("claim_type", "")).strip() or "unknown"
            old_text = str(self._to_dict(old_map.get(old_claim_id, {})).get("claim_text", "")).strip()
            new_text = str(new_claim.get("claim_text", "")).strip()
            old_canonical = self._canonicalize_claim_text_for_alignment(old_text)
            new_canonical = self._canonicalize_claim_text_for_alignment(new_text)
            old_normalized = self._normalize_text(old_text)
            new_normalized = self._normalize_text(new_text)

            if alignment_kind == "renumbered_successor" and claim_id != old_claim_id:
                adjustments.append(
                    {
                        "adjustment_id": f"S{next_index}",
                        "claim_id": claim_id,
                        "claim_type": claim_type,
                        "old_claim_id": old_claim_id,
                        "adjustment_kind": "renumbering",
                        "reason": reason,
                        "before_text": f"权利要求{old_claim_id}",
                        "after_text": f"权利要求{claim_id}",
                    }
                )
                next_index += 1

            if old_text and old_canonical == new_canonical and old_normalized != new_normalized:
                adjustments.append(
                    {
                        "adjustment_id": f"S{next_index}",
                        "claim_id": claim_id,
                        "claim_type": claim_type,
                        "old_claim_id": old_claim_id,
                        "adjustment_kind": "reference_adjustment",
                        "reason": reason,
                        "before_text": old_text,
                        "after_text": new_text,
                    }
                )
                next_index += 1

        return adjustments

    def _normalize_text(self, text: Any) -> str:
        value = str(text or "")
        value = re.sub(r"\s+", "", value)
        return re.sub(r"[，,；;。:：\-—_（）()\[\]{}]", "", value).strip()

    def _canonicalize_claim_text_for_alignment(self, text: Any) -> str:
        value = str(text or "")
        value = value.split("#", 1)[0]
        value = re.sub(
            r"权利要求\s*\d+(?:\s*[至到-]\s*\d+)?(?:\s*[、,，或和及]\s*\d+)*\s*中任一项所述",
            "权利要求#中任一项所述",
            value,
        )
        value = re.sub(
            r"权利要求\s*\d+(?:\s*[至到-]\s*\d+)?(?:\s*[、,，或和及]\s*\d+)*\s*所述",
            "权利要求#所述",
            value,
        )
        return self._normalize_text(value)

    def _canonicalize_claim_body_for_alignment(self, text: Any) -> str:
        value = str(text or "")
        value = value.split("#", 1)[0].strip()
        value = re.sub(
            r"^\s*根据权利要求\s*\d+(?:\s*[至到-]\s*\d+)?(?:\s*[、,，或和及]\s*\d+)*\s*(?:中任一项)?所述的?",
            "",
            value,
        )
        value = re.sub(r"^\s*[^，,。；;：:]*?(其中|其特征在于)[，,:：]?\s*", "", value)
        return self._normalize_text(value)

    def _sort_claim_ids(self, claim_ids: List[str]) -> List[str]:
        return sorted([str(item).strip() for item in claim_ids if str(item).strip()], key=self._claim_sort_key)

    def _claim_sort_key(self, value: str):
        return (0, int(value)) if str(value).isdigit() else (1, str(value))

    def _normalize_claim_ids(self, value: Any) -> List[str]:
        claim_ids: List[str] = []
        candidates = value if isinstance(value, list) else [value]
        for raw in candidates:
            text = str(raw or "").strip()
            if not text:
                continue
            for piece in re.split(r"[，,\s]+", text):
                part = piece.strip()
                if part and part.isdigit() and part not in claim_ids:
                    claim_ids.append(part)
        return claim_ids

    def _dependent_parent_shift_compatible(
        self,
        new_claim_id: str,
        new_parent_ids: List[str],
        old_claim_id: str,
        old_parent_ids: List[str],
    ) -> bool:
        if not new_parent_ids or not old_parent_ids:
            return True
        if not (str(new_claim_id).isdigit() and str(old_claim_id).isdigit()):
            return set(new_parent_ids).issubset(set(old_parent_ids))

        shift = int(old_claim_id) - int(new_claim_id)
        if shift <= 0:
            return False

        old_parent_set = set(old_parent_ids)
        for parent_id in new_parent_ids:
            if not str(parent_id).isdigit():
                if parent_id not in old_parent_set:
                    return False
                continue
            parent_num = int(parent_id)
            if str(parent_num) in old_parent_set:
                continue
            shifted_parent_id = str(parent_num + shift)
            if shifted_parent_id not in old_parent_set:
                return False
        return True

    def _extract_original_patent_claims(self, prepared_materials: Dict[str, Any]) -> List[Dict[str, Any]]:
        original_patent = self._to_dict(prepared_materials.get("original_patent", {}))
        patent_data = self._to_dict(original_patent.get("data", {}))
        claims_raw = patent_data.get("claims", [])
        if not isinstance(claims_raw, list):
            return []

        claims = []
        for idx, item in enumerate(claims_raw, start=1):
            claim = self._to_dict(item)
            claim_id = str(claim.get("claim_id", "")).strip() or str(idx)
            claims.append(
                {
                    "claim_id": claim_id,
                    "claim_text": str(claim.get("claim_text", "")).strip(),
                    "claim_type": str(claim.get("claim_type", "unknown")).strip() or "unknown",
                    "parent_claim_ids": self._sort_claim_ids(claim.get("parent_claim_ids", []) or []),
                }
            )
        return claims

    def _resolve_old_claims(
        self,
        prepared_materials: Dict[str, Any],
        previous_claims: List[Dict[str, Any]],
        current_notice_round: int,
    ) -> tuple[List[Dict[str, Any]], str, str]:
        if current_notice_round >= 2 and previous_claims:
            return previous_claims, "claims_previous", "multi_notice_previous_claims"
        if current_notice_round >= 2:
            logger.warning("多轮审查场景缺少上一版权利要求，回退使用原始专利权利要求作为旧权利要求基线")
            return (
                self._extract_original_patent_claims(prepared_materials),
                "original_patent",
                "multi_notice_missing_previous_claims",
            )
        return (
            self._extract_original_patent_claims(prepared_materials),
            "original_patent",
            "first_notice_or_missing_previous",
        )

    def _extract_current_notice_round(self, prepared_materials: Dict[str, Any]) -> int:
        office_action = self._to_dict(prepared_materials.get("office_action", {}))
        try:
            current_notice_round = int(office_action.get("current_notice_round", 0) or 0)
        except Exception:
            return 0
        return max(current_notice_round, 0)

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
