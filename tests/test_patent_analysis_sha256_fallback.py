from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Dict

from backend.routes import tasks as tasks_route
from backend.storage.models import TaskType
from backend.storage.pipeline_adapter import PipelineTaskManager
from backend.storage import SQLiteTaskStorage


class _FakeWorkflow:
    def __init__(self, result_builder):
        self._result_builder = result_builder

    def stream(self, initial_state, config=None, stream_mode: str = "values"):
        yield self._result_builder(initial_state)


class _DisabledR2Storage:
    enabled = False


def _mount_task_manager(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "patent_analysis_sha256.db")
    manager = PipelineTaskManager(storage=storage)
    monkeypatch.setattr(tasks_route, "task_manager", manager)
    return manager


def _mount_fake_workflow(monkeypatch, state_builder):
    import agents.patent_analysis.main as patent_analysis_main

    fake_workflow = _FakeWorkflow(state_builder)
    monkeypatch.setattr(patent_analysis_main, "create_workflow", lambda config=None: fake_workflow)
    monkeypatch.setattr(
        patent_analysis_main,
        "build_runtime_config",
        lambda task_id, checkpoint_ns="patent_analysis": {},
    )


def _build_completed_state(initial_state) -> Dict[str, Any]:
    output_dir = Path(str(initial_state.output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_pdf_path = output_dir / "raw.pdf"
    final_pdf_path = output_dir / "final_report.pdf"
    final_md_path = output_dir / "final_report.md"

    raw_pdf_path.write_bytes(b"%PDF-1.4\nmock source patent\n")
    final_pdf_path.write_bytes(b"%PDF-1.4\nmock analysis report\n")
    final_md_path.write_text("# mock report", encoding="utf-8")

    return {
        "status": "completed",
        "current_node": "render",
        "progress": 100.0,
        "resolved_pn": "CN123456A",
        "final_output_pdf": str(final_pdf_path),
        "final_output_md": str(final_md_path),
        "analysis_json": {"ai_title": "mock"},
    }


def test_patent_analysis_fallback_sha256_from_downloaded_pdf(monkeypatch, tmp_path):
    manager = _mount_task_manager(monkeypatch, tmp_path)
    _mount_fake_workflow(monkeypatch, _build_completed_state)
    monkeypatch.setattr(tasks_route, "emit_system_log", lambda **kwargs: None)
    monkeypatch.setattr(tasks_route, "_build_r2_storage", lambda: _DisabledR2Storage())
    monkeypatch.setattr(tasks_route.settings, "OUTPUT_DIR", tmp_path / "output")
    notify_calls = []

    async def _fake_notify(task_id: str, terminal_status: str, **kwargs):
        notify_calls.append({"task_id": task_id, "terminal_status": terminal_status, **kwargs})

    monkeypatch.setattr(tasks_route, "_notify_task_terminal_email", _fake_notify)

    task = manager.create_task(
        owner_id="authing:user-1",
        task_type=TaskType.PATENT_ANALYSIS.value,
        pn="CN123456A",
        title="AI 分析任务 - 测试",
    )

    asyncio.run(tasks_route.run_patent_analysis_task(task.id, pn="CN123456A"))

    latest = manager.get_task(task.id)
    assert latest is not None
    assert latest.status.value == "completed"

    expected_sha256 = hashlib.sha256(b"%PDF-1.4\nmock source patent\n").hexdigest()
    row = manager.storage.get_patent_analysis_by_pn("CN123456A")
    assert row is not None
    assert row["sha256"] == expected_sha256

    output_files = latest.metadata.get("output_files", {})
    analysis_json_path = Path(output_files.get("json", ""))
    payload = json.loads(analysis_json_path.read_text(encoding="utf-8"))
    assert payload["metadata"]["input_sha256"] == expected_sha256
    assert notify_calls == [
        {
            "task_id": task.id,
            "terminal_status": "completed",
            "task_type": TaskType.PATENT_ANALYSIS.value,
        }
    ]


def test_patent_analysis_cached_reuse_triggers_completed_email(monkeypatch, tmp_path):
    manager = _mount_task_manager(monkeypatch, tmp_path)
    monkeypatch.setattr(tasks_route, "emit_system_log", lambda **kwargs: None)
    monkeypatch.setattr(tasks_route, "_get_cached_analysis_payload", lambda **kwargs: {"metadata": {"resolved_pn": "CN123456A"}})

    class _FakeR2Storage:
        enabled = True

        def build_patent_pdf_key(self, patent_number: str) -> str:
            return f"patent/{patent_number}/ai_analysis.pdf"

        def build_analysis_json_key(self, patent_number: str) -> str:
            return f"patent/{patent_number}/ai_analysis.json"

        def key_exists(self, key: str) -> bool:
            return key.endswith("/ai_analysis.pdf")

    monkeypatch.setattr(tasks_route, "_build_r2_storage", lambda: _FakeR2Storage())
    notify_calls = []

    async def _fake_notify(task_id: str, terminal_status: str, **kwargs):
        notify_calls.append({"task_id": task_id, "terminal_status": terminal_status, **kwargs})

    monkeypatch.setattr(tasks_route, "_notify_task_terminal_email", _fake_notify)

    task = manager.create_task(
        owner_id="authing:user-1",
        task_type=TaskType.PATENT_ANALYSIS.value,
        pn="CN123456A",
        title="AI 分析任务 - 测试",
    )

    asyncio.run(tasks_route.run_patent_analysis_task(task.id, pn="CN123456A"))

    latest = manager.get_task(task.id)
    assert latest is not None
    assert latest.status.value == "completed"
    assert latest.metadata["output_files"]["r2_key"] == "patent/CN123456A/ai_analysis.pdf"
    assert notify_calls == [
        {
            "task_id": task.id,
            "terminal_status": "completed",
            "task_type": TaskType.PATENT_ANALYSIS.value,
        }
    ]


def test_patent_analysis_failed_workflow_triggers_failed_email(monkeypatch, tmp_path):
    manager = _mount_task_manager(monkeypatch, tmp_path)
    _mount_fake_workflow(monkeypatch, lambda _initial_state: {"status": "failed", "errors": [{"error_message": "boom"}]})
    monkeypatch.setattr(tasks_route, "emit_system_log", lambda **kwargs: None)
    monkeypatch.setattr(tasks_route, "_build_r2_storage", lambda: _DisabledR2Storage())
    notify_calls = []

    async def _fake_notify(task_id: str, terminal_status: str, **kwargs):
        notify_calls.append({"task_id": task_id, "terminal_status": terminal_status, **kwargs})

    monkeypatch.setattr(tasks_route, "_notify_task_terminal_email", _fake_notify)

    task = manager.create_task(
        owner_id="authing:user-1",
        task_type=TaskType.PATENT_ANALYSIS.value,
        pn="CN123456A",
        title="AI 分析任务 - 测试",
    )

    asyncio.run(tasks_route.run_patent_analysis_task(task.id, pn="CN123456A"))

    latest = manager.get_task(task.id)
    assert latest is not None
    assert latest.status.value == "failed"
    assert notify_calls == [
        {
            "task_id": task.id,
            "terminal_status": "failed",
            "task_type": TaskType.PATENT_ANALYSIS.value,
            "error_message": "boom",
        }
    ]
