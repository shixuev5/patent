from agents.ai_search.src.execution_state import (
    decide_search_transition,
    enrich_execution_round_summary,
    normalize_execution_plan,
    should_enter_screening,
    should_stop_execution,
)


def test_normalize_execution_plan_builds_default_lanes_from_query_batches():
    normalized = normalize_execution_plan(
        {
            "query_batches": [
                {
                    "batch_id": "b1",
                    "goal": "异常检测",
                    "must_terms_zh": ["异常检测"],
                }
            ]
        },
        {"objective": "检索异常检测方案"},
    )

    lane_types = [item["lane_type"] for item in normalized["lanes"]]
    assert lane_types == ["semantic", "boolean"]
    assert normalized["execution_policy"]["dynamic_replanning"] is True
    assert normalized["execution_policy"]["planner_visibility"] == "summary_only"


def test_normalize_execution_plan_keeps_trace_lane_when_seed_present():
    normalized = normalize_execution_plan(
        {
            "query_batches": [
                {
                    "batch_id": "b1",
                    "goal": "相似追踪",
                    "seed_pn": "CN123456A",
                }
            ]
        },
        {},
    )

    lane_types = [item["lane_type"] for item in normalized["lanes"]]
    assert lane_types == ["trace", "semantic", "boolean"]


def test_execution_rules_stop_and_enter_screening():
    normalized = normalize_execution_plan({}, {})
    summary = {
        "candidate_pool_size": 12,
        "new_unique_candidates": 0,
        "stop_signal": "",
    }

    assert should_enter_screening(normalized, summary) is True
    assert should_stop_execution(
        normalized,
        [
            {"new_unique_candidates": 0, "stop_signal": ""},
        ],
    ) is True


def test_enrich_execution_round_summary_backfills_decision_fields():
    enriched = enrich_execution_round_summary(
        {
            "round_id": "round-1",
            "lane_results": [{"lane_type": "semantic", "new_unique_candidates": 2}],
            "new_unique_candidates": 2,
            "deduped_hits": 1,
            "candidate_pool_size": 3,
        }
    )

    assert enriched["result_signal"] == "incremental"
    assert enriched["coverage_signal"] == "emerging"
    assert enriched["novelty_signal"] == "low"
    assert enriched["next_lane_priority"] == "boolean"
    assert enriched["lane_strategy_hint"] == "semantic_recall_working_consider_boolean_narrowing"


def test_decide_search_transition_replans_after_repeated_zero_results():
    normalized = normalize_execution_plan({}, {})

    decision = decide_search_transition(
        normalized,
        [
            {"round_id": "r1", "new_unique_candidates": 0, "deduped_hits": 0, "candidate_pool_size": 0},
            {"round_id": "r2", "new_unique_candidates": 0, "deduped_hits": 0, "candidate_pool_size": 0},
        ],
    )

    assert decision["recommended_action"] == "replan_search"
    assert decision["transition_hint"] == "repeated_zero_results"


def test_decide_search_transition_enters_screen_after_plateau_with_pool():
    normalized = normalize_execution_plan({}, {})

    decision = decide_search_transition(
        normalized,
        [
            {"round_id": "r1", "new_unique_candidates": 2, "deduped_hits": 0, "candidate_pool_size": 6},
            {"round_id": "r2", "new_unique_candidates": 0, "deduped_hits": 4, "candidate_pool_size": 6},
            {"round_id": "r3", "new_unique_candidates": 0, "deduped_hits": 2, "candidate_pool_size": 6},
        ],
    )

    assert decision["recommended_action"] == "enter_coarse_screen"
    assert decision["transition_hint"] == "stable_pool_without_increment"
