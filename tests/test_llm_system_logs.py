from __future__ import annotations

from types import SimpleNamespace

from agents.common.utils.llm import LLMService
from backend import system_logs
from backend import task_usage_tracking


class _MemoryStorage:
    def __init__(self):
        self.rows = []

    def insert_system_log(self, record):
        self.rows.append(record)
        return True


class _FakeCompletions:
    @staticmethod
    def create(**kwargs):
        message = SimpleNamespace(
            content='{"answer":"ok"}',
            reasoning_content="reasoning text",
        )
        choice = SimpleNamespace(message=message)
        usage = SimpleNamespace(
            prompt_tokens=120,
            completion_tokens=30,
            total_tokens=150,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=10),
        )
        return SimpleNamespace(
            id="resp-1",
            choices=[choice],
            usage=usage,
        )


class _FakeChat:
    completions = _FakeCompletions()


class _FakeClient:
    chat = _FakeChat()


def test_llm_logs_full_prompt_and_response(tmp_path, monkeypatch):
    storage = _MemoryStorage()
    monkeypatch.setattr(system_logs, "_STORAGE_REF", storage)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_DIR", tmp_path)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_FILE", tmp_path / "system_events.log")
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_PAYLOAD_DIR", tmp_path / "payloads")

    service = LLMService(api_key="test", base_url="https://example.com")
    service.text_client = _FakeClient()

    collector = task_usage_tracking.create_task_usage_collector(
        task_id="task-llm-1",
        owner_id="authing:user-1",
        task_type="patent_analysis",
    )

    with task_usage_tracking.task_usage_collection(collector):
        result = service.invoke_text_json(
            messages=[
                {"role": "system", "content": "sys prompt"},
                {"role": "user", "content": "user prompt with token sk-abcdef1234567890"},
            ],
            task_kind="core_summary_generation",
            model_override="deepseek-chat",
        )

    assert result["answer"] == "ok"
    assert storage.rows
    row = storage.rows[-1]
    assert row["category"] == "llm_call"
    assert row["event_name"] == "chat_completion_json"
    assert row["task_id"] == "task-llm-1"
    assert row["owner_id"] == "authing:user-1"
    # key-like string should be redacted in serialized payload
    payload_text = row.get("payload_inline_json") or ""
    assert "sk-abcdef1234567890" not in payload_text
