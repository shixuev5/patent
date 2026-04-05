"""
检索要素子 agent 定义。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from agents.ai_search.src.runtime import AiSearchGuardMiddleware, large_model


DATE_TEXT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
COMPACT_DATE_TEXT_RE = re.compile(r"^\d{8}$")


SEARCH_ELEMENTS_SYSTEM_PROMPT = """
你是 `search-elements` 子 agent。

唯一职责：从用户输入和当前上下文中构建检索要素表。

要求：
1. 只做信息抽取与澄清判断，不做专利检索。
2. 优先提取：检索目标、申请人、申请日、优先权日、技术要素。
3. `applicants` 可以为空数组；若为空，在 `clarification_summary` 中注明当前无法执行申请人追溯检索。
4. `filing_date` 与 `priority_date` 优先使用 `YYYY-MM-DD`；若都缺失，必须把“申请日或优先权日”写入 `missing_items`。
5. 只有当“检索目标”缺失，或“至少一个技术要素”缺失时，才返回 `status=needs_answer`。
6. 即使缺少日期，只要检索目标和至少一个技术要素明确，也可以返回 `status=complete`。
7. 最终输出必须是一个 JSON 对象，不要加任何解释文字或 markdown。
8. 若无法从上下文推断某字段，不要编造。

顶层输出字段固定：
- status
- objective
- applicants
- filing_date
- priority_date
- search_elements
- missing_items
- clarification_summary

`search_elements` 每项至少包含：
- element_name
- keywords_zh
- keywords_en
可选字段：
- notes
""".strip()


def _normalize_string_list(values: Any) -> List[str]:
    outputs: List[str] = []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return outputs
    for item in values:
        text = str(item or "").strip()
        if text and text not in outputs:
            outputs.append(text)
    return outputs


def normalize_date_text(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if DATE_TEXT_RE.fullmatch(text):
        return text
    if COMPACT_DATE_TEXT_RE.fullmatch(text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return None


def normalize_search_elements_payload(payload: Any) -> Dict[str, Any]:
    source = payload if isinstance(payload, dict) else {}
    objective = str(source.get("objective") or "").strip()
    applicants = _normalize_string_list(source.get("applicants") or [])
    filing_date = normalize_date_text(source.get("filing_date"))
    priority_date = normalize_date_text(source.get("priority_date"))
    missing_items = _normalize_string_list(source.get("missing_items") or [])

    raw_elements = source.get("search_elements")
    search_elements = raw_elements if isinstance(raw_elements, list) else []
    normalized_elements: List[Dict[str, Any]] = []
    for item in search_elements:
        if not isinstance(item, dict):
            continue
        element_name = str(
            item.get("element_name")
            or item.get("feature")
            or item.get("name")
            or ""
        ).strip()
        base_item = {
            key: value
            for key, value in item.items()
            if key != "synonyms"
        }
        normalized_elements.append(
            {
                **base_item,
                "element_name": element_name,
                "keywords_zh": _normalize_string_list(item.get("keywords_zh") or []),
                "keywords_en": _normalize_string_list(item.get("keywords_en") or []),
                "notes": str(item.get("notes") or "").strip(),
            }
        )

    if not filing_date and not priority_date and "申请日或优先权日" not in missing_items:
        missing_items.append("申请日或优先权日")

    clarification_parts: List[str] = []
    raw_summary = str(source.get("clarification_summary") or "").strip()
    if raw_summary:
        clarification_parts.append(raw_summary)
    if not applicants:
        clarification_parts.append("未提供申请人，无法执行申请人追溯检索。")
    clarification_summary = " ".join(part for part in clarification_parts if part).strip()

    status = str(source.get("status") or "").strip().lower()
    if not objective or not any(str(item.get("element_name") or "").strip() for item in normalized_elements):
        status = "needs_answer"
    elif status not in {"complete", "needs_answer"}:
        status = "complete"

    return {
        "status": status,
        "objective": objective,
        "applicants": applicants,
        "filing_date": filing_date,
        "priority_date": priority_date,
        "search_elements": normalized_elements,
        "missing_items": missing_items,
        "clarification_summary": clarification_summary,
    }


def build_search_elements_subagent() -> dict:
    return {
        "name": "search-elements",
        "description": "根据用户需求整理结构化检索要素，并提取申请人和日期边界。",
        "system_prompt": SEARCH_ELEMENTS_SYSTEM_PROMPT,
        "model": large_model(),
        "tools": [],
        "middleware": [AiSearchGuardMiddleware()],
    }
