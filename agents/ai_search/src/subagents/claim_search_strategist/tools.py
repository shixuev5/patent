"""Claim-search-strategist specialist tools."""

from __future__ import annotations

import json
import uuid
from typing import Any, List

from langchain.tools import ToolRuntime

from agents.ai_search.src.runtime import extract_json_object
from agents.ai_search.src.state import PHASE_SEARCH_STRATEGY


def build_claim_search_strategist_tools(context: Any) -> List[Any]:
    def get_claim_context() -> str:
        """读取最新的 claim decomposition 和 search strategy。"""
        return json.dumps(
            {
                "claim_decomposition": context.latest_message_metadata("claim_decomposition"),
                "claim_search_strategy": context.latest_message_metadata("claim_search_strategy"),
            },
            ensure_ascii=False,
        )

    def get_gap_context() -> str:
        """读取最新的 limitation coverage、gap 和 creativity readiness 上下文。"""
        return json.dumps(context.latest_gap_context(), ensure_ascii=False)

    def build_gap_strategy_seed(plan_version: int = 0) -> str:
        """把最新 gap 上下文转换成下一轮 strategist 可直接消费的 replan seed。"""
        return json.dumps(context.build_gap_strategy_seed_payload(plan_version), ensure_ascii=False)

    def save_claim_search_strategy(payload_json: str, runtime: ToolRuntime | None = None) -> str:
        """保存 claim-aware 检索策略。"""
        payload = extract_json_object(payload_json)
        seed = context.build_gap_strategy_seed_payload()
        if not isinstance(payload.get("targeted_gaps"), list) or not payload.get("targeted_gaps"):
            payload["targeted_gaps"] = seed.get("targeted_gaps") or []
        if not isinstance(payload.get("planning_mode"), str) or not str(payload.get("planning_mode") or "").strip():
            payload["planning_mode"] = str(seed.get("planning_mode") or "initial_plan")
        if not isinstance(payload.get("replan_focus"), list) or not payload.get("replan_focus"):
            payload["replan_focus"] = [
                str(item.get("limitation_id") or item.get("claim_id") or "").strip()
                for item in (payload.get("targeted_gaps") or [])
                if isinstance(item, dict) and str(item.get("limitation_id") or item.get("claim_id") or "").strip()
            ]
        if not isinstance(payload.get("batch_specs"), list) or not payload.get("batch_specs"):
            payload["batch_specs"] = seed.get("seed_batch_specs") or []
        if payload.get("planning_mode") == "gap_replan" and "continue_search" not in payload:
            payload["continue_search"] = True
        context.storage.create_ai_search_message(
            {
                "message_id": uuid.uuid4().hex,
                "task_id": context.task_id,
                "role": "assistant",
                "kind": "claim_search_strategy",
                "content": str(payload.get("strategy_summary") or "").strip() or None,
                "stream_status": "completed",
                "metadata": payload,
            }
        )
        context.update_task_phase(PHASE_SEARCH_STRATEGY, runtime=runtime, current_task="search_strategy")
        return "claim search strategy saved"

    return [get_claim_context, get_gap_context, build_gap_strategy_seed, save_claim_search_strategy]
