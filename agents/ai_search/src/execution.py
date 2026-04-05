"""
AI 检索查询阶段执行编排。
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from agents.ai_search.src.execution_state import normalize_execution_plan, should_enter_screening, should_stop_execution
from agents.ai_search.src.runtime import extract_structured_response
from agents.ai_search.src.subagents.query_executor import build_query_executor_agent


def _build_execution_directive(
    plan_version: int,
    normalized_plan: Dict[str, Any],
    previous_round_summaries: List[Dict[str, Any]],
) -> Dict[str, Any]:
    round_index = len(previous_round_summaries) + 1
    return {
        "round_id": f"round-{round_index}",
        "plan_version": int(plan_version),
        "execution_policy": normalized_plan.get("execution_policy") or {},
        "lanes": normalized_plan.get("lanes") or [],
        "round_stop_rules": normalized_plan.get("round_stop_rules") or [],
        "screening_entry_rules": normalized_plan.get("screening_entry_rules") or [],
        "replan_rules": normalized_plan.get("replan_rules") or [],
        "previous_round_summaries": previous_round_summaries,
        "search_elements_snapshot": normalized_plan.get("search_elements_snapshot") or {},
    }


def _normalize_round_summary(
    directive: Dict[str, Any],
    structured: Dict[str, Any],
    candidate_pool_size: int,
) -> Dict[str, Any]:
    lane_results = structured.get("lane_results") if isinstance(structured.get("lane_results"), list) else []
    normalized_lane_results: List[Dict[str, Any]] = []
    new_unique_candidates = 0
    deduped_hits = 0
    for item in lane_results:
        if not isinstance(item, dict):
            continue
        normalized = {
            "lane_type": str(item.get("lane_type") or "").strip(),
            "batch_id": str(item.get("batch_id") or "").strip(),
            "executed_tool": str(item.get("executed_tool") or "").strip(),
            "new_unique_candidates": int(item.get("new_unique_candidates") or 0),
            "deduped_hits": int(item.get("deduped_hits") or 0),
            "candidate_pool_size": int(item.get("candidate_pool_size") or candidate_pool_size),
            "stop_signal": str(item.get("stop_signal") or "").strip(),
            "reasoning": str(item.get("reasoning") or "").strip(),
        }
        new_unique_candidates += normalized["new_unique_candidates"]
        deduped_hits += normalized["deduped_hits"]
        normalized_lane_results.append(normalized)
    return {
        "round_id": str(structured.get("round_id") or directive.get("round_id") or uuid.uuid4().hex[:8]),
        "lane_results": normalized_lane_results,
        "new_unique_candidates": int(structured.get("new_unique_candidates") or new_unique_candidates),
        "deduped_hits": int(structured.get("deduped_hits") or deduped_hits),
        "candidate_pool_size": int(structured.get("candidate_pool_size") or candidate_pool_size),
        "needs_replan": bool(structured.get("needs_replan", True)),
        "recommended_adjustments": [
            str(item).strip()
            for item in (structured.get("recommended_adjustments") or [])
            if str(item).strip()
        ],
        "stop_signal": str(structured.get("stop_signal") or "").strip(),
    }


def run_query_execution_rounds(storage: Any, task_id: str, plan_version: int) -> Dict[str, Any]:
    plan = storage.get_ai_search_plan(task_id, int(plan_version)) or {}
    plan_json = plan.get("plan_json") if isinstance(plan.get("plan_json"), dict) else {}
    search_elements = plan.get("search_elements_json") if isinstance(plan.get("search_elements_json"), dict) else {}
    normalized_plan = normalize_execution_plan(plan_json, search_elements)
    executor = build_query_executor_agent(storage, task_id)

    summaries: List[Dict[str, Any]] = []
    max_rounds = int((normalized_plan.get("execution_policy") or {}).get("max_rounds") or 3)
    for _ in range(max_rounds):
        directive = _build_execution_directive(int(plan_version), normalized_plan, summaries)
        result = executor.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": json.dumps(directive, ensure_ascii=False),
                    }
                ]
            }
        )
        structured = extract_structured_response(result)
        candidate_pool_size = len(storage.list_ai_search_documents(task_id, int(plan_version)))
        summary = _normalize_round_summary(directive, structured, candidate_pool_size)
        storage.create_ai_search_message(
            {
                "message_id": uuid.uuid4().hex,
                "task_id": task_id,
                "plan_version": int(plan_version),
                "role": "assistant",
                "kind": "execution_summary",
                "content": json.dumps(summary, ensure_ascii=False),
                "stream_status": "completed",
                "metadata": summary,
            }
        )
        summaries.append(summary)
        if should_enter_screening(normalized_plan, summary) or should_stop_execution(normalized_plan, summaries):
            break

    return {
        "plan": normalized_plan,
        "summaries": summaries,
        "candidate_pool_size": len(storage.list_ai_search_documents(task_id, int(plan_version))),
    }
