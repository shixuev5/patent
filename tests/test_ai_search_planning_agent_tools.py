from __future__ import annotations

from agents.ai_search.src import planning_agent as planning_agent_module


def test_build_planning_agent_exposes_documented_tools(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(planning_agent_module, "create_deep_agent", _fake_create_deep_agent)
    monkeypatch.setattr(planning_agent_module, "large_model", lambda: object())

    planning_agent_module.build_planning_agent(object(), "task-ai-search")

    tools = captured.get("tools")
    assert isinstance(tools, list)
    assert tools
    assert all(callable(tool) for tool in tools)
    assert all(str(getattr(tool, "__doc__", "") or "").strip() for tool in tools)
