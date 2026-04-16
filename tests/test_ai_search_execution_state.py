from agents.ai_search.src.execution_state import (
    build_execution_todos,
    normalize_execution_plan,
    resolve_plan_step,
)


def _plan() -> dict:
    return {
        "sub_plans": [
            {
                "sub_plan_id": "sub_plan_1",
                "goal": "异常检测",
                "query_blueprints": [{"batch_id": "b1", "goal": "异常检测"}, {"batch_id": "b2", "goal": "Block C 条件检索"}],
                "retrieval_steps": [
                    {
                        "step_id": "step_1",
                        "title": "首轮宽召回",
                        "purpose": "验证召回方向",
                        "feature_combination": "A+B1",
                        "language_strategy": "中文优先",
                        "ipc_cpc_mode": "先不加 IPC/CPC",
                        "ipc_cpc_codes": [],
                        "expected_recall": "50-100 条",
                        "fallback_action": "结果过宽时补限定词",
                        "query_blueprint_refs": ["b1"],
                        "phase_key": "execute_search",
                        "activation_mode": "immediate",
                    },
                    {
                        "step_id": "step_2",
                        "title": "Block C 条件分支",
                        "purpose": "主检索命中后叠加 Block C",
                        "feature_combination": "A+C",
                        "language_strategy": "中文优先",
                        "ipc_cpc_mode": "沿用并补充 IPC/CPC",
                        "ipc_cpc_codes": ["G06N 3/08"],
                        "expected_recall": "更聚焦的结果池",
                        "fallback_action": "微调 Block C 同义词",
                        "query_blueprint_refs": ["b2"],
                        "phase_key": "execute_search",
                        "activation_mode": "conditional",
                        "depends_on_step_ids": ["step_1"],
                        "activation_conditions": {
                            "any_of": [
                                {"signal": "primary_goal_reached", "equals": True},
                                {"signal": "recall_quality", "equals": "too_broad"},
                            ]
                        },
                        "activation_summary": "命中主目标或结果过宽时激活。",
                    }
                ],
            }
        ]
    }


def test_normalize_execution_plan_requires_retrieval_steps():
    try:
        normalize_execution_plan(
            {
                "sub_plans": [
                    {
                        "sub_plan_id": "sub_plan_1",
                        "query_blueprints": [{"batch_id": "b1"}],
                        "retrieval_steps": [],
                    }
                ]
            },
            {},
        )
    except ValueError as exc:
        assert "retrieval_steps" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_normalize_execution_plan_rejects_cross_sub_plan_query_refs():
    try:
        normalize_execution_plan(
            {
                "sub_plans": [
                    {
                        "sub_plan_id": "sub_plan_1",
                        "query_blueprints": [{"batch_id": "b1"}],
                        "retrieval_steps": [
                            {
                                "step_id": "step_1",
                                "title": "step",
                                "purpose": "purpose",
                                "feature_combination": "A+B",
                                "language_strategy": "zh",
                                "ipc_cpc_mode": "none",
                                "ipc_cpc_codes": [],
                                "expected_recall": "10",
                                "fallback_action": "retry",
                                "query_blueprint_refs": ["missing"],
                                "phase_key": "execute_search",
                            }
                        ],
                    }
                ]
            },
            {},
        )
    except ValueError as exc:
        assert "query_blueprints" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_build_execution_todos_comes_from_retrieval_steps():
    normalized = normalize_execution_plan(_plan(), {"objective": "检索异常检测方案"})
    todos = build_execution_todos(1, normalized)

    assert [item["todo_id"] for item in todos] == ["plan_1:sub_plan_1:step_1"]
    assert todos[0]["description"].startswith("目的：验证召回方向")
    assert todos[0]["phase_key"] == "execute_search"


def test_normalize_execution_plan_keeps_conditional_activation_fields():
    normalized = normalize_execution_plan(_plan(), {"objective": "检索异常检测方案"})
    conditional_step = normalized["sub_plans"][0]["retrieval_steps"][1]

    assert conditional_step["activation_mode"] == "conditional"
    assert conditional_step["depends_on_step_ids"] == ["step_1"]
    assert conditional_step["activation_conditions"]["any_of"][0]["signal"] == "primary_goal_reached"
    assert conditional_step["activation_summary"] == "命中主目标或结果过宽时激活。"


def test_normalize_execution_plan_coerces_supported_phase_key_aliases():
    plan = _plan()
    plan["sub_plans"][0]["retrieval_steps"][0]["phase_key"] = "primary_search"
    plan["sub_plans"][0]["retrieval_steps"][1]["phase_key"] = "supplementary_search"

    normalized = normalize_execution_plan(plan, {"objective": "检索异常检测方案"})

    assert normalized["sub_plans"][0]["retrieval_steps"][0]["phase_key"] == "execute_search"
    assert normalized["sub_plans"][0]["retrieval_steps"][1]["phase_key"] == "execute_search"


def test_resolve_plan_step_returns_matching_sub_plan_and_step():
    normalized = normalize_execution_plan(_plan(), {})
    sub_plan, step = resolve_plan_step(normalized, "sub_plan_1", "step_1")

    assert sub_plan["sub_plan_id"] == "sub_plan_1"
    assert step["step_id"] == "step_1"
