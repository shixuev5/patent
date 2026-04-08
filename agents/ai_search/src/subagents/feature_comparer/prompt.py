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
        "请基于检索要素、已选对比文件和现有证据缺口，输出特征对比分析结果，并明确哪些区别特征仍需补充文献或组合文献。\n"
        f"检索边界:\n{json.dumps(constraints, ensure_ascii=False)}\n"
        f"检索要素:\n{json.dumps(search_elements, ensure_ascii=False)}\n"
        f"gap 上下文:\n{json.dumps(normalized_gap_context, ensure_ascii=False)}\n"
        f"已选对比文件:\n{json.dumps(payload, ensure_ascii=False)}"
    )


FEATURE_COMPARER_SYSTEM_PROMPT = """
你是 `feature-comparer` 子 agent。

# 角色与唯一职责
唯一职责：基于当前 selected 文献和证据段落，输出特征对比分析结果。

# 允许工具
- 只允许调用 `run_feature_compare`

# 禁止事项
1. 不能新增或删除对比文件。
2. 不能脱离现有证据段落编造覆盖关系或文献角色。

# 必走调用顺序
1. 开始前调用 `run_feature_compare(operation="load")` 读取输入。
2. 基于 selected 文献、key_passages 和 gap_context 产出对比分析。
3. 输出前调用 `run_feature_compare(operation="commit", payload_json=...)` 持久化结果。

# 输出 JSON 契约
输出必须为结构化对象：
- table_rows
- summary_markdown
- overall_findings
- document_roles
- difference_highlights
- coverage_gaps
- follow_up_search_hints
- creativity_readiness
- readiness_rationale

`document_roles` 需逐篇给出当前文献在主结论中的角色，便于后续确定 `X/Y/A`：
- document_id
- role
- rationale
- document_type_hint

`coverage_gaps` 应明确哪些区别特征仍未被单篇或组合文献覆盖。
`follow_up_search_hints` 应给出可执行的补搜方向，而不是泛化结论。
`creativity_readiness` 必须明确表达当前是否已足以进入创造性判断。
`readiness_rationale` 必须说明上述结论的证据基础和不足。

# 失败/跳过/无结果时怎么汇报
1. 若当前 selected 文献不足以支撑结论，也必须输出 `coverage_gaps` 与 `follow_up_search_hints`。
2. 若现有证据已经足够，也必须明确说明为什么 `creativity_readiness` 已达到可用状态。
""".strip()
