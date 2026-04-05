from agents.ai_search.src.execution_state import (
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
