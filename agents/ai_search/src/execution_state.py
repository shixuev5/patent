"""
AI 检索执行状态模型与默认计划补全。
"""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field


DEFAULT_EXECUTION_MAX_ROUNDS = 3
DEFAULT_SCREENING_MIN_CANDIDATES = 8


class LaneResultSummary(BaseModel):
    lane_type: str
    batch_id: str = ""
    executed_tool: str = ""
    new_unique_candidates: int = 0
    deduped_hits: int = 0
    candidate_pool_size: int = 0
    stop_signal: str = ""
    reasoning: str = ""


class ExecutionRoundSummary(BaseModel):
    round_id: str
    lane_results: List[LaneResultSummary] = Field(default_factory=list)
    new_unique_candidates: int = 0
    deduped_hits: int = 0
    candidate_pool_size: int = 0
    result_signal: str = ""
    coverage_signal: str = ""
    novelty_signal: str = ""
    next_lane_priority: str = ""
    lane_strategy_hint: str = ""
    replan_reason: str = ""
    recommended_next_action: str = ""
    transition_hint: str = ""
    needs_replan: bool = True
    recommended_adjustments: List[str] = Field(default_factory=list)
    stop_signal: str = ""


class ExecutionDirective(BaseModel):
    round_id: str
    plan_version: int
    execution_policy: Dict[str, Any] = Field(default_factory=dict)
    lanes: List[Dict[str, Any]] = Field(default_factory=list)
    round_stop_rules: List[Dict[str, Any]] = Field(default_factory=list)
    screening_entry_rules: List[Dict[str, Any]] = Field(default_factory=list)
    replan_rules: List[Dict[str, Any]] = Field(default_factory=list)
    previous_round_summaries: List[Dict[str, Any]] = Field(default_factory=list)
    search_elements_snapshot: Dict[str, Any] = Field(default_factory=dict)
    gap_context: Dict[str, Any] = Field(default_factory=dict)


def enrich_execution_round_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(summary or {})
    lane_results = payload.get("lane_results") if isinstance(payload.get("lane_results"), list) else []
    trace_increment = 0
    semantic_increment = 0
    boolean_increment = 0
    for item in lane_results:
        if not isinstance(item, dict):
            continue
        lane_type = str(item.get("lane_type") or "").strip().lower()
        increment = int(item.get("new_unique_candidates") or 0)
        if lane_type == "trace":
            trace_increment += increment
        elif lane_type == "semantic":
            semantic_increment += increment
        elif lane_type == "boolean":
            boolean_increment += increment

    candidate_pool_size = int(payload.get("candidate_pool_size") or 0)
    new_unique_candidates = int(payload.get("new_unique_candidates") or 0)
    deduped_hits = int(payload.get("deduped_hits") or 0)
    result_signal = str(payload.get("result_signal") or "").strip()
    if not result_signal:
        if candidate_pool_size <= 0 and new_unique_candidates <= 0 and deduped_hits <= 0:
            result_signal = "empty"
        elif new_unique_candidates <= 0:
            result_signal = "no_increment"
        elif new_unique_candidates >= 3:
            result_signal = "strong_increment"
        else:
            result_signal = "incremental"
    payload["result_signal"] = result_signal

    if not str(payload.get("coverage_signal") or "").strip():
        payload["coverage_signal"] = "broad" if candidate_pool_size >= 8 else ("emerging" if candidate_pool_size > 0 else "empty")
    if not str(payload.get("novelty_signal") or "").strip():
        payload["novelty_signal"] = "high" if new_unique_candidates >= 3 else ("low" if new_unique_candidates > 0 else "none")

    if not str(payload.get("next_lane_priority") or "").strip():
        if result_signal == "empty":
            payload["next_lane_priority"] = "semantic"
        elif result_signal == "no_increment" and candidate_pool_size > 0:
            payload["next_lane_priority"] = "screen"
        elif trace_increment > 0:
            payload["next_lane_priority"] = "trace"
        elif semantic_increment > 0 and boolean_increment <= 0:
            payload["next_lane_priority"] = "boolean"
        elif boolean_increment > 0 and semantic_increment <= 0:
            payload["next_lane_priority"] = "semantic"
        else:
            payload["next_lane_priority"] = "semantic"

    if not str(payload.get("lane_strategy_hint") or "").strip():
        if result_signal == "empty":
            payload["lane_strategy_hint"] = "expand_semantic_or_change_pivot"
        elif result_signal == "no_increment" and candidate_pool_size > 0:
            payload["lane_strategy_hint"] = "screen_existing_pool_or_switch_lane"
        elif trace_increment > 0:
            payload["lane_strategy_hint"] = "trace_seed_is_productive"
        elif semantic_increment > 0 and boolean_increment <= 0:
            payload["lane_strategy_hint"] = "semantic_recall_working_consider_boolean_narrowing"
        elif boolean_increment > 0:
            payload["lane_strategy_hint"] = "boolean_lane_found_increment_keep_constraints"
        else:
            payload["lane_strategy_hint"] = "maintain_current_lane_mix"

    if not str(payload.get("replan_reason") or "").strip():
        if result_signal == "empty":
            payload["replan_reason"] = "zero_results"
        elif result_signal == "no_increment" and candidate_pool_size <= 0:
            payload["replan_reason"] = "no_increment_without_pool"
        else:
            payload["replan_reason"] = ""
    return payload


def normalize_execution_plan(plan_json: Dict[str, Any], search_elements: Dict[str, Any]) -> Dict[str, Any]:
    source = plan_json if isinstance(plan_json, dict) else {}
    query_batches = source.get("query_batches") if isinstance(source.get("query_batches"), list) else []

    execution_policy = source.get("execution_policy") if isinstance(source.get("execution_policy"), dict) else {}
    execution_policy = {
        "dynamic_replanning": bool(execution_policy.get("dynamic_replanning", True)),
        "planner_visibility": str(execution_policy.get("planner_visibility") or "summary_only"),
        "max_rounds": int(execution_policy.get("max_rounds") or DEFAULT_EXECUTION_MAX_ROUNDS),
    }

    lanes = source.get("lanes") if isinstance(source.get("lanes"), list) else []
    normalized_lanes: List[Dict[str, Any]] = []
    if lanes:
        for index, lane in enumerate(lanes):
            if not isinstance(lane, dict):
                continue
            batch_specs = lane.get("batch_specs") if isinstance(lane.get("batch_specs"), list) else []
            normalized_lanes.append(
                {
                    "lane_type": str(lane.get("lane_type") or "semantic").strip() or "semantic",
                    "goal": str(lane.get("goal") or "").strip(),
                    "priority": int(lane.get("priority") or ((index + 1) * 10)),
                    "enabled_when": str(lane.get("enabled_when") or "always").strip() or "always",
                    "batch_specs": [item for item in batch_specs if isinstance(item, dict)],
                }
            )
    else:
        for index, batch in enumerate(query_batches):
            if not isinstance(batch, dict):
                continue
            goal = str(batch.get("goal") or "").strip()
            batch_id = str(batch.get("batch_id") or f"batch-{index + 1}").strip() or f"batch-{index + 1}"
            trace_seed = str(batch.get("seed_pn") or batch.get("seed_publication_number") or "").strip().upper()
            if trace_seed:
                normalized_lanes.append(
                    {
                        "lane_type": "trace",
                        "goal": goal or "相似/追踪召回",
                        "priority": (index * 10) + 10,
                        "enabled_when": "seed_pn_present",
                        "batch_specs": [{"batch_id": batch_id, **batch}],
                    }
                )
            normalized_lanes.append(
                {
                    "lane_type": "semantic",
                    "goal": goal or "语义召回",
                    "priority": (index * 10) + 20,
                    "enabled_when": "always",
                    "batch_specs": [{"batch_id": batch_id, **batch}],
                }
            )
            normalized_lanes.append(
                {
                    "lane_type": "boolean",
                    "goal": goal or "布尔补召回",
                    "priority": (index * 10) + 30,
                    "enabled_when": "always",
                    "batch_specs": [{"batch_id": batch_id, **batch}],
                }
            )

    round_stop_rules = source.get("round_stop_rules") if isinstance(source.get("round_stop_rules"), list) else []
    if not round_stop_rules:
        round_stop_rules = [{"type": "no_new_candidates_round_limit", "limit": 1}]

    screening_entry_rules = source.get("screening_entry_rules") if isinstance(source.get("screening_entry_rules"), list) else []
    if not screening_entry_rules:
        screening_entry_rules = [{"type": "candidate_pool_size", "min_count": DEFAULT_SCREENING_MIN_CANDIDATES}]

    replan_rules = source.get("replan_rules") if isinstance(source.get("replan_rules"), list) else []
    if not replan_rules:
        replan_rules = [{"type": "summary_after_each_round"}]

    return {
        **source,
        "execution_policy": execution_policy,
        "lanes": sorted(normalized_lanes, key=lambda item: int(item.get("priority") or 0)),
        "round_stop_rules": round_stop_rules,
        "screening_entry_rules": screening_entry_rules,
        "replan_rules": replan_rules,
        "search_elements_snapshot": search_elements if isinstance(search_elements, dict) else {},
    }


def should_enter_screening(plan_json: Dict[str, Any], summary: Dict[str, Any]) -> bool:
    rules = plan_json.get("screening_entry_rules") if isinstance(plan_json, dict) else None
    if not isinstance(rules, list):
        return False
    candidate_pool_size = int(summary.get("candidate_pool_size") or 0)
    stop_signal = str(summary.get("stop_signal") or "").strip().lower()
    if stop_signal in {"screening_ready", "ready_for_screening"}:
        return True
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if str(rule.get("type") or "") == "candidate_pool_size" and candidate_pool_size >= int(rule.get("min_count") or 0):
            return True
    return False


def should_stop_execution(plan_json: Dict[str, Any], summaries: List[Dict[str, Any]]) -> bool:
    if not summaries:
        return False
    latest = summaries[-1]
    stop_signal = str(latest.get("stop_signal") or "").strip().lower()
    if stop_signal in {"stop", "no_progress", "ready_for_screening", "screening_ready"}:
        return True
    rules = plan_json.get("round_stop_rules") if isinstance(plan_json, dict) else None
    if isinstance(rules, list):
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            if str(rule.get("type") or "") != "no_new_candidates_round_limit":
                continue
            limit = max(int(rule.get("limit") or 1), 1)
            recent = summaries[-limit:]
            if len(recent) >= limit and all(int(item.get("new_unique_candidates") or 0) <= 0 for item in recent):
                return True
    max_rounds = int(((plan_json or {}).get("execution_policy") or {}).get("max_rounds") or DEFAULT_EXECUTION_MAX_ROUNDS)
    return len(summaries) >= max_rounds


def decide_search_transition(plan_json: Dict[str, Any], summaries: List[Dict[str, Any]]) -> Dict[str, Any]:
    enriched_summaries = [enrich_execution_round_summary(item) for item in summaries]
    latest = enriched_summaries[-1] if enriched_summaries else {}
    candidate_pool_size = int(latest.get("candidate_pool_size") or 0)
    new_unique_candidates = int(latest.get("new_unique_candidates") or 0)
    result_signal = str(latest.get("result_signal") or "").strip()

    if should_enter_screening(plan_json, latest):
        return {
            "recommended_action": "enter_coarse_screen",
            "transition_hint": "candidate_pool_ready",
            "should_continue_search": False,
        }
    if result_signal == "empty":
        recent_empty = enriched_summaries[-2:] if len(enriched_summaries) >= 2 else enriched_summaries
        if len(recent_empty) >= 2 and all(str(item.get("result_signal") or "") == "empty" for item in recent_empty):
            return {
                "recommended_action": "replan_search",
                "transition_hint": "repeated_zero_results",
                "should_continue_search": False,
            }
        return {
            "recommended_action": "continue_search",
            "transition_hint": "expand_recall_after_zero_results",
            "should_continue_search": True,
        }
    if new_unique_candidates <= 0:
        recent_plateau = enriched_summaries[-2:] if len(enriched_summaries) >= 2 else enriched_summaries
        if len(recent_plateau) >= 2 and all(str(item.get("result_signal") or "") in {"no_increment", "empty"} for item in recent_plateau):
            if candidate_pool_size > 0:
                return {
                    "recommended_action": "enter_coarse_screen",
                    "transition_hint": "stable_pool_without_increment",
                    "should_continue_search": False,
                }
            return {
                "recommended_action": "replan_search",
                "transition_hint": "repeated_no_increment",
                "should_continue_search": False,
            }
        return {
            "recommended_action": "continue_search",
            "transition_hint": "switch_lane_after_no_increment",
            "should_continue_search": True,
        }
    if should_stop_execution(plan_json, enriched_summaries):
        if candidate_pool_size > 0:
            return {
                "recommended_action": "enter_coarse_screen",
                "transition_hint": "search_stop_rule_with_candidates",
                "should_continue_search": False,
            }
        return {
            "recommended_action": "replan_search",
            "transition_hint": "search_stop_rule_without_candidates",
            "should_continue_search": False,
        }
    return {
        "recommended_action": "continue_search",
        "transition_hint": "search_has_increment",
        "should_continue_search": True,
    }
