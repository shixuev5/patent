from __future__ import annotations

from types import SimpleNamespace

import pytest

import agents.common.utils.llm as llm_module
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
    def __init__(self, content: str):
        self._content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(
            content=self._content,
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
    def __init__(self, completions: _FakeCompletions):
        self.completions = completions


class _FakeClient:
    def __init__(self, content: str):
        self.completions = _FakeCompletions(content)
        self.chat = _FakeChat(self.completions)


class _RetryableError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class _FlakyCompletions:
    def __init__(self, failures: list[Exception], content: str):
        self._failures = list(failures)
        self._content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._failures:
            raise self._failures.pop(0)
        message = SimpleNamespace(
            content=self._content,
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


class _FlakyClient:
    def __init__(self, failures: list[Exception], content: str):
        self.completions = _FlakyCompletions(failures=failures, content=content)
        self.chat = _FakeChat(self.completions)


def _assert_thinking_payload(call_kwargs):
    assert call_kwargs["extra_body"]["enable_thinking"] is True
    assert call_kwargs["extra_body"]["thinking_budget"] == LLMService._THINKING_BUDGET


def test_llm_logs_full_prompt_and_response(tmp_path, monkeypatch):
    storage = _MemoryStorage()
    monkeypatch.setattr(system_logs, "_STORAGE_REF", storage)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_DIR", tmp_path)
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_FILE", tmp_path / "system_events.log")
    monkeypatch.setattr(system_logs, "SYSTEM_LOG_PAYLOAD_DIR", tmp_path / "payloads")

    service = LLMService(api_key="test", base_url="https://example.com")
    text_client = _FakeClient(content='{"answer":"ok"}')
    service.text_client = text_client

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
            model_override="qwen3.5-flash",
        )

    assert result["answer"] == "ok"
    assert storage.rows
    row = storage.rows[-1]
    assert row["category"] == "llm_call"
    assert row["event_name"] == "chat_completion_json"
    assert row["task_id"] == "task-llm-1"
    assert row["owner_id"] == "authing:user-1"
    assert row["method"] == "POST"
    assert row["path"] == "/v1/chat/completions"
    # key-like string should be redacted in serialized payload
    payload_text = row.get("payload_inline_json") or ""
    assert "sk-abcdef1234567890" not in payload_text
    _assert_thinking_payload(text_client.completions.calls[-1])


def test_all_task_policies_enable_thinking():
    for task_kind in LLMService._TASK_POLICY_MAP:
        policy = LLMService._resolve_policy(task_kind)
        assert policy["thinking"] is True
        assert policy["tier"] in {"default", "large"}


def test_vision_single_image_uses_thinking_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_module, "emit_system_log", lambda **kwargs: None)

    service = LLMService(api_key="test", base_url="https://example.com")
    vision_client = _FakeClient(content="single-image-ok")
    service.vlm_client = vision_client

    image_path = tmp_path / "single.png"
    image_path.write_bytes(b"fake-image")

    result = service.invoke_vision_image(
        image_path=str(image_path),
        system_prompt="sys",
        user_prompt="user",
        task_kind="vision_single_figure_explain",
        model_override="qwen3.5-flash",
    )

    assert result == "single-image-ok"
    _assert_thinking_payload(vision_client.completions.calls[-1])


def test_vision_multi_image_text_uses_thinking_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_module, "emit_system_log", lambda **kwargs: None)

    service = LLMService(api_key="test", base_url="https://example.com")
    vision_client = _FakeClient(content="multi-image-ok")
    service.vlm_client = vision_client

    image_path1 = tmp_path / "img1.png"
    image_path2 = tmp_path / "img2.png"
    image_path1.write_bytes(b"fake-image-1")
    image_path2.write_bytes(b"fake-image-2")

    result = service.invoke_vision_images(
        image_paths=[str(image_path1), str(image_path2)],
        system_prompt="sys",
        user_prompt="user",
        task_kind="vision_multi_figure_synthesis",
        model_override="qwen3.5-plus",
    )

    assert result == "multi-image-ok"
    _assert_thinking_payload(vision_client.completions.calls[-1])


def test_invoke_text_json_retries_on_retryable_error(monkeypatch):
    monkeypatch.setattr(llm_module, "emit_system_log", lambda **kwargs: None)
    sleep_calls = []
    monkeypatch.setattr(llm_module.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    service = LLMService(api_key="test", base_url="https://example.com")
    text_client = _FlakyClient(
        failures=[
            _RetryableError("Too many requests. throttled due to capacity limits", 503),
            _RetryableError("ServiceUnavailable"),
        ],
        content='{"answer":"ok"}',
    )
    service.text_client = text_client

    result = service.invoke_text_json(
        messages=[{"role": "user", "content": "hello"}],
        task_kind="core_summary_generation",
        model_override="qwen3.5-flash",
    )

    assert result == {"answer": "ok"}
    assert len(text_client.completions.calls) == 3
    assert sleep_calls == [1.0, 2.0]


def test_invoke_text_json_no_retry_for_non_retryable_error(monkeypatch):
    monkeypatch.setattr(llm_module, "emit_system_log", lambda **kwargs: None)
    sleep_calls = []
    monkeypatch.setattr(llm_module.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    service = LLMService(api_key="test", base_url="https://example.com")
    text_client = _FlakyClient(
        failures=[RuntimeError("invalid api key")],
        content='{"answer":"ok"}',
    )
    service.text_client = text_client

    with pytest.raises(RuntimeError, match="invalid api key"):
        service.invoke_text_json(
            messages=[{"role": "user", "content": "hello"}],
            task_kind="core_summary_generation",
            model_override="qwen3.5-flash",
        )

    assert len(text_client.completions.calls) == 1
    assert sleep_calls == []
