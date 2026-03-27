"""
修改差异分析节点
使用大模型识别新增/变化特征，并判定来源（原权利要求上提 or 说明书特征）
"""

import json
import re
from typing import Any, Dict, List

from loguru import logger

from agents.common.utils.llm import get_llm_service
from agents.ai_reply.src.state import AddedFeature, StructuredClaim
from agents.ai_reply.src.utils import get_node_cache


class AmendmentTrackingNode:
    """修改差异分析节点（LLM主判）"""

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
            cache = get_node_cache(self.config, "amendment_tracking")
            result = cache.run_step(
                "track_amendment_v7",
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
            updates["has_claim_amendment"] = result["has_claim_amendment"]
            updates["added_features"] =[
                item if isinstance(item, AddedFeature) else AddedFeature(**item)
                for item in result.get("added_features", [])
            ]
            updates["status"] = "completed"
            updates["progress"] = 60.0
            logger.info(f"修改差异分析完成，新增特征数: {len(updates['added_features'])}")
        except Exception as e:
            logger.error(f"修改差异分析失败: {e}")
            updates["errors"] =[{
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
        old_claims = self._resolve_old_claims(prepared, previous_claims_list, current_notice_round)
        effective_claims = current_claims_list or old_claims

        if not current_claims_list:
            return {
                "claims_old_structured": old_claims,
                "claims_effective_structured": effective_claims,
                "has_claim_amendment": False,
                "added_features":[],
            }

        structured_diff = self._build_structured_diff(old_claims, effective_claims)
        if not structured_diff.get("has_changes", False):
            return {
                "claims_old_structured": old_claims,
                "claims_effective_structured": effective_claims,
                "has_claim_amendment": False,
                "added_features":[],
            }

        messages =[
            {"role": "system", "content": self._build_system_prompt()},
            {"role": "user", "content": self._build_user_prompt(structured_diff)},
        ]
        
        # 依赖外部 LangGraph 节点的自动重试机制
        response = self.llm_service.invoke_text_json(
            messages=messages,
            task_kind="oar_amendment_tracking",
            temperature=0.05,
        )
        normalized = self._normalize_tracking_result(response)
        
        return {
            "claims_old_structured": old_claims,
            "claims_effective_structured": effective_claims,
            "has_claim_amendment": normalized["has_claim_amendment"],
            "added_features": normalized["added_features"],
        }

    def _build_system_prompt(self) -> str:
        return """你是资深的中国专利代理师和专利局审查员。你的任务是对比【修改前和修改后的权利要求对】，精准提取引入的【新增/修改的实质性技术特征】，并判定这些特征的来源。

### 分析步骤与核心原则：

1. **对比权项对（changed_claims_pairs）**
   - 阅读每一对修改前后的权利要求，找出申请人实际增加或实质性修改的“完整技术特征”。
   - **特殊情况处理**：如果 `old_text` 为空字符串，说明该权利要求是**全新增加**的，请直接提取 `new_text` 中的实质性技术特征（请忽略“如权利要求X所述的”等前序引用套话）。
   - 过滤掉非实质性修改：如果仅包含标点符号更改、错别字修正、从属编号调整、语序调整或纯语言文字润色（不改变技术方案实质），请忽略。
   - 只有当引入了新的技术限制条件、新的结构、步骤、参数或关系时，才将 `has_claim_amendment` 判定为 `true`。

2. **提炼新增技术特征（added_features）**
   - `feature_text` 必须是技术上语义完整的句子或短句，不能是零碎词语。
   - 如果同一权项中的多个零碎修改共同组成了一个完整的技术动作或结构关系，必须将它们合并成一个完整通顺的 `feature_text`。
   - 同时必须输出该条变更对应的旧片段 `feature_before_text` 与新片段 `feature_after_text`，用于报告中的单条特征 diff 展示。
   - 若属于纯新增，`feature_before_text` 置空字符串，`feature_after_text` 必须等于或覆盖 `feature_text`。
   - 若属于替换/改写，`feature_before_text` 与 `feature_after_text` 都必须填写，且二者只覆盖这条特征自身，不要输出整条权利要求全文。

3. **判定特征来源（source_type）**
   - 必须全局检索提供的 `full_old_claims_context`（旧权利要求全文本）。
   - **`claim`（权项上提）**：如果该新特征的实质技术内容，在**任意一条**旧权利要求中已经记载过（即使本次措辞有微调），则判定为 `claim`，并在 `source_claim_ids` 填写该特征原本所在的旧权项编号。
   - **`spec`（说明书提取）**：当且仅当该特征的内容在**所有旧权利要求**中均未曾记载过（属于申请人从说明书中找出的新特征），才判定为 `spec`。

### 严格的输出格式与约束：

- **只输出合法的 JSON 对象**，绝对不要输出任何 Markdown 标记（如 ```json）、分析过程或其他解释性文本。
- JSON 结构必须严格符合以下要求：
{
  "has_claim_amendment": true,
  "added_features":[
    {
      "feature_id": "F1", // 从 F1, F2 开始依次编号
      "feature_text": "完整的技术特征描述（如：第二弹性件的一端与滑块连接，另一端与壳体内壁连接）",
      "feature_before_text": "该变更项在旧权利要求中的对应旧片段；若为纯新增则为空字符串",
      "feature_after_text": "该变更项在新权利要求中的对应新片段；应等于或覆盖 feature_text",
      "target_claim_ids":["1", "3"], // 该特征被添加到了哪些新权利要求中（字符串数组）
      "source_type": "claim", // 必须且只能是 "claim" 或 "spec"
      "source_claim_ids": ["4", "5"] // 如果是 claim，填写原权项编号；如果是 spec，必须为空数组[]
    }
  ]
}

### 边界条件处理：
- 当且仅当 `has_claim_amendment` 为 `false` 时，`added_features` 必须为 `[]`。
- `source_claim_ids` 中的编号必须仅包含数字（作为字符串），不要包含“权利要求”等字眼。
- 当 `source_type` 为 `spec` 时，`source_claim_ids` **必须**为空数组 `[]`。"""

    def _build_user_prompt(self, structured_diff: Dict[str, Any]) -> str:
        return f"""请分析以下新旧权利要求差异数据。

【分析提示】
1. `changed_claims_pairs` 是代码层面已粗筛出的“确有文本差异，且不是纯重编号/平移”的权项对。
2. 请直接对比每组 `old_text` 与 `new_text`，提炼真正新增或实质修改的完整技术特征。
3. 每条 `added_features` 都要按“单条特征粒度”补充 `feature_before_text` 与 `feature_after_text`，不要返回整条权利要求全文。
4. 请务必核对 `full_old_claims_context`。如果某个新特征的实质内容在任一旧权利要求中已经出现过，即使表达有微调，也应判定为 `claim`；只有在旧权利要求中完全找不到时，才判定为 `spec`。

【差异数据】
{json.dumps(structured_diff, ensure_ascii=False, indent=2)}"""

    def _normalize_tracking_result(self, response: Dict[str, Any]) -> Dict[str, Any]:
        result = self._to_dict(response)
        if "has_claim_amendment" not in result:
            raise ValueError("amendment_tracking 输出缺少 has_claim_amendment")
        has_claim_amendment_raw = result.get("has_claim_amendment")
        if not isinstance(has_claim_amendment_raw, bool):
            raise ValueError("amendment_tracking 输出非法 has_claim_amendment，必须为布尔值")
        has_claim_amendment = has_claim_amendment_raw

        if "added_features" not in result:
            raise ValueError("amendment_tracking 输出缺少 added_features")
        features_raw = result.get("added_features",[])
        if not isinstance(features_raw, list):
            raise ValueError("amendment_tracking 输出格式错误：added_features 不是列表")

        features: List[Dict[str, Any]] =[]
        for item in features_raw:
            feature = self._to_dict(item)
            feature_id = str(feature.get("feature_id", "")).strip()
            feature_text = str(feature.get("feature_text", "")).strip()
            feature_before_text = str(feature.get("feature_before_text", "")).strip()
            feature_after_text = str(feature.get("feature_after_text", "")).strip()
            if not feature_after_text:
                feature_after_text = feature_text
            if not feature_text:
                feature_text = feature_after_text
            if not feature_id or not feature_text:
                raise ValueError("amendment_tracking 输出非法 added_features 项，缺少 feature_id 或 feature_text")

            source_type = str(feature.get("source_type", "")).strip()
            if source_type not in {"claim", "spec"}:
                raise ValueError(f"amendment_tracking 输出非法 source_type: {source_type}")

            target_claim_ids =[
                str(claim_id).strip()
                for claim_id in (feature.get("target_claim_ids", []) or[])
                if str(claim_id).strip()
            ]
            source_claim_ids =[
                str(claim_id).strip()
                for claim_id in (feature.get("source_claim_ids", []) or[])
                if str(claim_id).strip()
            ]

            # 自动容错修正：如果 LLM 判定为说明书增加，但错误地带上了来源权项编号，强制清空
            if source_type == "spec" and source_claim_ids:
                logger.warning(f"特征 {feature_id} 被标记为 'spec' 但 source_claim_ids 非空，已自动修正清除。")
                source_claim_ids =[]

            features.append({
                "feature_id": feature_id,
                "feature_text": feature_text,
                "feature_before_text": feature_before_text,
                "feature_after_text": feature_after_text,
                "target_claim_ids": target_claim_ids,
                "source_type": source_type,
                "source_claim_ids": source_claim_ids,
            })

        if not has_claim_amendment and features:
            raise ValueError("amendment_tracking 输出冲突：has_claim_amendment=false 但 added_features 非空")

        return {
            "has_claim_amendment": has_claim_amendment,
            "added_features": features,
        }

    def _build_structured_diff(
        self,
        old_claims: List[Dict[str, Any]],
        new_claims: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        old_map = {str(item.get("claim_id", "")).strip(): item for item in old_claims if str(item.get("claim_id", "")).strip()}
        new_map = {str(item.get("claim_id", "")).strip(): item for item in new_claims if str(item.get("claim_id", "")).strip()}

        # 改为遍历所有的新权利要求ID，以涵盖纯新增的编号
        new_ids_sorted = self._sort_claim_ids(list(new_map.keys()))
        changed_claims_pairs: List[Dict[str, str]] =[]

        # 构建旧权利要求的标准化文本池，用于处理权项“重编号/平移”的情况
        old_texts_pool = {self._normalize_text(claim.get("claim_text", "")) for claim in old_claims}

        for claim_id in new_ids_sorted:
            new_claim = self._to_dict(new_map.get(claim_id, {}))
            new_text = str(new_claim.get("claim_text", "")).strip()
            
            # 提取对应的旧权项（如果是全新增加的编号，old_text 为空）
            old_claim = self._to_dict(old_map.get(claim_id, {}))
            old_text = str(old_claim.get("claim_text", "")).strip()
            
            old_text_norm = self._normalize_text(old_text)
            new_text_norm = self._normalize_text(new_text)
            
            # 若文本完全一致，跳过（未修改）
            if old_text_norm == new_text_norm:
                continue

            # 核心防御：如果新权利要求的文本在任意旧权利要求中原封不动出现过
            # 说明只是申请人删除了某些权项导致编号前移（重编号），不应视为内容被修改
            if new_text_norm in old_texts_pool:
                continue

            changed_claims_pairs.append({
                "claim_id": claim_id,
                "old_text": old_text,  # 可能是空字符串
                "new_text": new_text,
            })

        full_old_claims_context = {
            str(claim.get("claim_id", "")): str(claim.get("claim_text", ""))
            for claim in old_claims
            if str(claim.get("claim_id", "")).strip()
        }
        
        changed_claim_ids = [item["claim_id"] for item in changed_claims_pairs]

        return {
            "summary": {
                "old_claim_count": len(old_claims),
                "new_claim_count": len(new_claims),
                "changed_claim_count": len(changed_claims_pairs),
            },
            "has_changes": bool(changed_claims_pairs),
            "changed_claim_ids": changed_claim_ids,
            "changed_claims_pairs": changed_claims_pairs,
            "full_old_claims_context": full_old_claims_context,
        }

    def _normalize_text(self, text: Any) -> str:
        value = str(text or "")
        value = re.sub(r"\s+", "", value)
        return re.sub(r"[，,；;。:\-—_（）()\[\]{}]", "", value).strip()

    def _sort_claim_ids(self, claim_ids: List[str]) -> List[str]:
        def _key(value: str):
            return (0, int(value)) if value.isdigit() else (1, value)

        return sorted([str(item).strip() for item in claim_ids if str(item).strip()], key=_key)

    def _extract_original_patent_claims(self, prepared_materials: Dict[str, Any]) -> List[Dict[str, Any]]:
        original_patent = self._to_dict(prepared_materials.get("original_patent", {}))
        patent_data = self._to_dict(original_patent.get("data", {}))
        claims_raw = patent_data.get("claims",[])
        if not isinstance(claims_raw, list):
            return []

        claims =[]
        for idx, item in enumerate(claims_raw, start=1):
            claim = self._to_dict(item)
            claim_id = str(claim.get("claim_id", "")).strip() or str(idx)
            claims.append({
                "claim_id": claim_id,
                "claim_text": str(claim.get("claim_text", "")).strip(),
                "claim_type": str(claim.get("claim_type", "unknown")).strip() or "unknown",
                "parent_claim_ids": self._sort_claim_ids(claim.get("parent_claim_ids", []) or []),
            })
        return claims

    def _resolve_old_claims(
        self,
        prepared_materials: Dict[str, Any],
        previous_claims: List[Dict[str, Any]],
        current_notice_round: int,
    ) -> List[Dict[str, Any]]:
        if current_notice_round >= 2 and previous_claims:
            return previous_claims
        return self._extract_original_patent_claims(prepared_materials)

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
