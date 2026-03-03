"""
修改差异分析节点
使用大模型识别新增/变化特征，并判定来源（原权利要求上提 or 说明书特征）
"""

import difflib
import json
import re
from typing import Any, Dict, List

from loguru import logger

from agents.common.utils.llm import get_llm_service
from agents.office_action_reply.src.state import AddedFeature
from agents.office_action_reply.src.utils import get_node_cache


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
                "track_amendment_v4",
                self._track_amendment,
                self._state_get(state, "prepared_materials", {}),
                self._state_get(state, "claims_new_structured", []),
            )

            updates["claims_old_structured"] = result.get("claims_old_structured", [])
            updates["has_claim_amendment"] = result["has_claim_amendment"]
            updates["added_features"] = [
                item if isinstance(item, AddedFeature) else AddedFeature(**item)
                for item in result.get("added_features", [])
            ]
            updates["status"] = "completed"
            updates["progress"] = 60.0
            logger.info(f"修改差异分析完成，新增特征数: {len(updates['added_features'])}")
        except Exception as e:
            logger.error(f"修改差异分析失败: {e}")
            updates["errors"] = [{
                "node_name": "amendment_tracking",
                "error_message": str(e),
                "error_type": "amendment_tracking_error",
            }]
            updates["status"] = "failed"

        return updates

    def _track_amendment(self, prepared_materials, new_claims) -> Dict[str, Any]:
        prepared = self._to_dict(prepared_materials)
        old_claims = self._extract_old_claims(prepared)
        new_claims_list = [self._to_dict(item) for item in (new_claims or [])]

        # 未提供新权利要求文件，直接视为无修改
        if not new_claims_list:
            return {
                "claims_old_structured": old_claims,
                "has_claim_amendment": False,
                "added_features": [],
            }

        structured_diff = self._build_structured_diff(old_claims, new_claims_list)
        if not structured_diff.get("has_changes", False):
            return {
                "claims_old_structured": old_claims,
                "has_claim_amendment": False,
                "added_features": [],
            }

        messages = [
            {"role": "system", "content": self._build_system_prompt()},
            {"role": "user", "content": self._build_user_prompt(structured_diff)},
        ]
        response = self.llm_service.chat_completion_json(
            messages,
            temperature=0.05,
            thinking=True,
        )
        normalized = self._normalize_tracking_result(response)
        return {
            "claims_old_structured": old_claims,
            "has_claim_amendment": normalized["has_claim_amendment"],
            "added_features": normalized["added_features"],
        }

    def _build_system_prompt(self) -> str:
        return """你是专利审查修改分析专家。输入仅包含“结构化 diff”，你必须基于 diff 识别新增技术特征。

任务：
1. 判断是否存在实质性修改（has_claim_amendment）。
2. 若存在修改，提取新增特征列表 added_features。
3. 对每个新增特征判定来源：
   - source_type="claim"：可在旧从属权利要求找到对应记载（上提）
   - source_type="spec"：旧权利要求中找不到，视为说明书提取特征

输出要求：
- 只输出 JSON 对象，不得输出额外文本。
- 输出结构：
{
  "has_claim_amendment": true,
  "added_features": [
    {
      "feature_id": "New_F1",
      "feature_text": "新增特征文本",
      "target_claim_ids": ["1"],
      "source_type": "claim",
      "source_claim_ids": ["3"]
    }
  ]
}

字段约束：
- source_type 只能是 "claim" 或 "spec"。
- target_claim_ids/source_claim_ids 均为数字字符串数组。
- source_claim_ids 仅填写可由 diff 直接支持的来源权利要求编号。
- 若 has_claim_amendment=false，则 added_features 必须为空数组。"""

    def _build_user_prompt(self, structured_diff: Dict[str, Any]) -> str:
        return f"""请基于以下结构化 diff 输出结果：

【结构化Diff】
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
        features_raw = result.get("added_features", [])
        if not isinstance(features_raw, list):
            raise ValueError("amendment_tracking 输出格式错误：added_features 不是列表")

        features: List[Dict[str, Any]] = []
        for item in features_raw:
            feature = self._to_dict(item)
            feature_id = str(feature.get("feature_id", "")).strip()
            feature_text = str(feature.get("feature_text", "")).strip()
            if not feature_id or not feature_text:
                raise ValueError("amendment_tracking 输出非法 added_features 项，缺少 feature_id 或 feature_text")

            source_type = str(feature.get("source_type", "")).strip()
            if source_type not in {"claim", "spec"}:
                raise ValueError(f"amendment_tracking 输出非法 source_type: {source_type}")

            target_claim_ids = [
                str(claim_id).strip()
                for claim_id in (feature.get("target_claim_ids", []) or [])
                if str(claim_id).strip()
            ]
            source_claim_ids = [
                str(claim_id).strip()
                for claim_id in (feature.get("source_claim_ids", []) or [])
                if str(claim_id).strip()
            ]

            features.append({
                "feature_id": feature_id,
                "feature_text": feature_text,
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

        old_ids = set(old_map.keys())
        new_ids = set(new_map.keys())
        common_ids = self._sort_claim_ids(list(old_ids & new_ids))
        removed_ids = self._sort_claim_ids(list(old_ids - new_ids))
        added_ids = self._sort_claim_ids(list(new_ids - old_ids))

        changed_claim_ids: List[str] = []
        added_segments: List[Dict[str, Any]] = []
        segment_index = 1

        for claim_id in common_ids:
            old_claim = self._to_dict(old_map.get(claim_id, {}))
            new_claim = self._to_dict(new_map.get(claim_id, {}))
            old_text = str(old_claim.get("claim_text", "")).strip()
            new_text = str(new_claim.get("claim_text", "")).strip()
            if self._normalize_text(old_text) == self._normalize_text(new_text):
                continue

            changed_claim_ids.append(claim_id)

            segments = self._extract_added_segments(old_text, new_text)
            for segment in segments:
                candidate_source_claim_ids = self._find_candidate_source_claim_ids(segment, old_claims)
                added_segments.append({
                    "segment_id": f"S{segment_index}",
                    "target_claim_id": claim_id,
                    "text": segment,
                    "target_claim_excerpt": self._build_target_claim_excerpt(new_text, segment),
                    "candidate_source_claim_ids": candidate_source_claim_ids,
                })
                segment_index += 1

        all_changed_claim_ids = self._sort_claim_ids(changed_claim_ids + removed_ids + added_ids)

        return {
            "summary": {
                "old_claim_count": len(old_claims),
                "new_claim_count": len(new_claims),
                "changed_claim_count": len(all_changed_claim_ids),
                "added_segment_count": len(added_segments),
            },
            "has_changes": bool(all_changed_claim_ids),
            "changed_claim_ids": all_changed_claim_ids,
            "added_segments": added_segments,
        }

    def _extract_added_segments(self, old_text: str, new_text: str) -> List[str]:
        matcher = difflib.SequenceMatcher(None, old_text, new_text)
        segments: List[str] = []
        seen = set()

        for op, _, _, j1, j2 in matcher.get_opcodes():
            if op not in {"insert", "replace"}:
                continue
            piece = self._clean_segment(new_text[j1:j2])
            if len(self._normalize_text(piece)) < 6:
                continue
            key = self._normalize_text(piece)
            if key in seen:
                continue
            seen.add(key)
            segments.append(piece)

        if segments:
            return segments[:12]

        old_units = {self._normalize_text(unit) for unit in self._split_units(old_text)}
        for unit in self._split_units(new_text):
            key = self._normalize_text(unit)
            if len(key) < 6 or key in old_units or key in seen:
                continue
            seen.add(key)
            segments.append(unit)
            if len(segments) >= 12:
                break
        return segments

    def _build_target_claim_excerpt(self, claim_text: str, segment: str, window: int = 60) -> str:
        content = str(claim_text or "").strip()
        seg = str(segment or "").strip()
        if not content or not seg:
            return ""
        idx = content.find(seg)
        if idx < 0:
            return content[: max(2 * window, 120)]
        start = max(0, idx - window)
        end = min(len(content), idx + len(seg) + window)
        return content[start:end].strip()

    def _find_candidate_source_claim_ids(
        self,
        segment: str,
        old_claims: List[Dict[str, Any]],
    ) -> List[str]:
        segment_norm = self._normalize_text(segment)
        if not segment_norm:
            return []

        candidates: List[str] = []
        for claim in old_claims:
            claim_id = str(claim.get("claim_id", "")).strip()
            if not claim_id:
                continue
            claim_text = str(claim.get("claim_text", "")).strip()
            claim_norm = self._normalize_text(claim_text)
            if not claim_norm:
                continue

            if segment_norm in claim_norm:
                if claim_id not in candidates:
                    candidates.append(claim_id)
            if len(candidates) >= 4:
                break
        return self._sort_claim_ids(candidates)

    def _split_units(self, text: str) -> List[str]:
        units = re.split(r"[；;。！？\n]+", str(text or ""))
        cleaned_units: List[str] = []
        for unit in units:
            cleaned = self._clean_segment(unit)
            if cleaned:
                cleaned_units.append(cleaned)
        return cleaned_units

    def _clean_segment(self, text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "")).strip(" ，,；;。:\n\r\t")

    def _normalize_text(self, text: Any) -> str:
        value = str(text or "")
        value = re.sub(r"\s+", "", value)
        return re.sub(r"[，,；;。:\-—_（）()\[\]{}]", "", value).strip()

    def _sort_claim_ids(self, claim_ids: List[str]) -> List[str]:
        def _key(value: str):
            return (0, int(value)) if value.isdigit() else (1, value)

        return sorted([str(item).strip() for item in claim_ids if str(item).strip()], key=_key)

    def _extract_old_claims(self, prepared_materials: Dict[str, Any]) -> List[Dict[str, Any]]:
        original_patent = self._to_dict(prepared_materials.get("original_patent", {}))
        patent_data = self._to_dict(original_patent.get("data", {}))
        claims_raw = patent_data.get("claims", [])
        if not isinstance(claims_raw, list):
            return []

        claims = []
        for idx, item in enumerate(claims_raw, start=1):
            claim = self._to_dict(item)
            claim_id = str(claim.get("claim_id", "")).strip() or str(idx)
            claims.append({
                "claim_id": claim_id,
                "claim_text": str(claim.get("claim_text", "")).strip(),
                "claim_type": str(claim.get("claim_type", "unknown")).strip() or "unknown",
            })
        return claims

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
