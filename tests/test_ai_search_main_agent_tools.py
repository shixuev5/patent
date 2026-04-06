from __future__ import annotations

from agents.ai_search.src import main_agent as main_agent_module


def test_build_main_agent_exposes_documented_tools(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_create_deep_agent(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(main_agent_module, "create_deep_agent", _fake_create_deep_agent)
    monkeypatch.setattr(main_agent_module, "large_model", lambda: object())

    main_agent_module.build_main_agent(object(), "task-ai-search")

    tools = captured.get("tools")
    assert isinstance(tools, list)
    assert tools
    assert all(callable(tool) for tool in tools)
    assert all(str(getattr(tool, "__doc__", "") or "").strip() for tool in tools)
