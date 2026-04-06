"""Prompt builder for the feature-comparer specialist."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from agents.ai_search.src.query_constraints import build_search_constraints


def build_feature_prompt(
    search_elements: Dict[str, Any],
    selected_documents: List[Dict[str, Any]],
    gap_context: Dict[str, Any] | None = None,
) -> str:
    constraints = build_search_constraints(search_elements)
    payload = []
    for item in selected_documents:
        payload.append(
            {
                "document_id": item["document_id"],
                "pn": item["pn"],
                "title": item["title"],
                "abstract": item["abstract"],
                "key_passages": item.get("key_passages_json") or [],
            }
        )
    normalized_gap_context = gap_context if isinstance(gap_context, dict) else {}
    return (
        "请基于检索要素、已选对比文件和现有证据缺口，输出特征对比表，并明确哪些区别特征仍需补充文献或组合文献。\n"
        f"检索边界:\n{json.dumps(constraints, ensure_ascii=False)}\n"
        f"检索要素:\n{json.dumps(search_elements, ensure_ascii=False)}\n"
        f"gap 上下文:\n{json.dumps(normalized_gap_context, ensure_ascii=False)}\n"
        f"已选对比文件:\n{json.dumps(payload, ensure_ascii=False)}"
    )


FEATURE_COMPARER_SYSTEM_PROMPT = """
你是 `feature-comparer` 子 agent。

唯一职责：基于当前 selected 文献和证据段落，输出特征对比表。
不能新增或删除对比文件。
开始前调用 `run_feature_compare(operation="load")` 读取输入，输出前调用 `run_feature_compare(operation="commit", payload_json=...)` 持久化结果。

输出必须为结构化对象：
- table_rows
- summary_markdown
- overall_findings
- difference_highlights
- coverage_gaps
- follow_up_search_hints
- creativity_readiness
- readiness_rationale
""".strip()
