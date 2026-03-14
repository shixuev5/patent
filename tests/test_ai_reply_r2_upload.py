from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, List

from backend.routes import tasks as tasks_route
from backend.storage.models import TaskType
from backend.storage.pipeline_adapter import PipelineTaskManager
from backend.storage.sqlite_storage import SQLiteTaskStorage


class _FakeWorkflow:
    def __init__(self, result_builder):
        self._result_builder = result_builder

    def stream(self, initial_state, config=None, stream_mode: str = "values"):
        yield self._result_builder(initial_state)


class _FakeR2Storage:
    def __init__(self, fail_pdf: bool = False, fail_json: bool = False):
        self.enabled = True
        self.fail_pdf = fail_pdf
        self.fail_json = fail_json
        self.put_calls: List[Dict[str, Any]] = []

    def build_ai_reply_pdf_key(self, pn: str) -> str:
        return f"workspace/ai_reply/{pn}.pdf"

    def build_ai_reply_json_key(self, pn: str) -> str:
        return f"workspace/ai_reply/{pn}.json"

    def put_bytes(self, key: str, content: bytes, content_type: str = "application/octet-stream") -> bool:
        self.put_calls.append({"key": key, "content_type": content_type, "size": len(content)})
        if content_type == "application/pdf":
            return not self.fail_pdf
        if content_type == "application/json":
            return not self.fail_json
        return True


def _build_completed_state(initial_state, publication_number: str = "CN115655695A") -> Dict[str, Any]:
    output_dir = Path(str(initial_state.output_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = output_dir / "final_report.pdf"
    md_path = output_dir / "final_report.md"
    json_path = output_dir / "final_report.json"
    pdf_path.write_bytes(b"%PDF-1.4\n%mock ai reply report\n")
    md_path.write_text("# mock", encoding="utf-8")
    json_path.write_text('{"status":"ok"}', encoding="utf-8")
    return {
        "status": "completed",
        "current_node": "final_report_render",
        "progress": 100.0,
        "final_report_artifacts": {
            "pdf_path": str(pdf_path),
            "markdown_path": str(md_path),
        },
        "prepared_materials": {
            "original_patent": {
                "application_number": "202310001234.5",
                "data": {
                    "bibliographic_data": {
                        "publication_number": publication_number,
                    }
                },
            },
            "office_action": {
                "application_number": "202310001234.5",
            },
        },
    }


def _mount_task_manager(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_reply_r2_upload.db")
    manager = PipelineTaskManager(storage=storage)
    monkeypatch.setattr(tasks_route, "task_manager", manager)
    return manager


def _mount_fake_workflow(monkeypatch, state_builder):
    import agents.ai_reply.main as ai_reply_main

    fake_workflow = _FakeWorkflow(state_builder)
    monkeypatch.setattr(ai_reply_main, "create_workflow", lambda config=None: fake_workflow)
    monkeypatch.setattr(ai_reply_main, "build_runtime_config", lambda task_id, checkpoint_ns="ai_reply": {})


def test_ai_reply_uploads_pdf_and_json_to_r2_and_updates_pn(monkeypatch, tmp_path):
    manager = _mount_task_manager(monkeypatch, tmp_path)
    _mount_fake_workflow(monkeypatch, _build_completed_state)
    monkeypatch.setattr(tasks_route, "emit_system_log", lambda **kwargs: None)

    fake_r2 = _FakeR2Storage(fail_pdf=False, fail_json=False)
    monkeypatch.setattr(tasks_route, "_build_r2_storage", lambda: fake_r2)

    task = manager.create_task(
        owner_id="authing:user-1",
        task_type=TaskType.AI_REPLY.value,
        title="AI 答复任务 - 测试",
    )
    input_files = [
        {"stored_path": "/tmp/oa.pdf", "file_type": "office_action", "original_name": "oa.pdf"},
        {"stored_path": "/tmp/resp.pdf", "file_type": "response", "original_name": "resp.pdf"},
    ]
    asyncio.run(tasks_route.run_ai_reply_task(task.id, input_files=input_files))

    latest = manager.get_task(task.id)
    assert latest is not None
    assert latest.status.value == "completed"
    assert latest.pn == "CN115655695A"
    output_files = latest.metadata.get("output_files", {})
    assert output_files.get("pn") == "CN115655695A"
    assert output_files.get("r2_key") == "workspace/ai_reply/CN115655695A.pdf"
    assert output_files.get("ai_reply_r2_key") == "workspace/ai_reply/CN115655695A.json"
    content_types = {item["content_type"] for item in fake_r2.put_calls}
    assert content_types == {"application/pdf", "application/json"}


def test_ai_reply_r2_upload_failure_keeps_task_completed(monkeypatch, tmp_path):
    manager = _mount_task_manager(monkeypatch, tmp_path)
    _mount_fake_workflow(monkeypatch, _build_completed_state)
    monkeypatch.setattr(tasks_route, "emit_system_log", lambda **kwargs: None)

    fake_r2 = _FakeR2Storage(fail_pdf=True, fail_json=True)
    monkeypatch.setattr(tasks_route, "_build_r2_storage", lambda: fake_r2)

    task = manager.create_task(
        owner_id="authing:user-1",
        task_type=TaskType.AI_REPLY.value,
        title="AI 答复任务 - 测试",
    )
    input_files = [
        {"stored_path": "/tmp/oa.pdf", "file_type": "office_action", "original_name": "oa.pdf"},
        {"stored_path": "/tmp/resp.pdf", "file_type": "response", "original_name": "resp.pdf"},
    ]
    asyncio.run(tasks_route.run_ai_reply_task(task.id, input_files=input_files))

    latest = manager.get_task(task.id)
    assert latest is not None
    assert latest.status.value == "completed"
    output_files = latest.metadata.get("output_files", {})
    assert output_files.get("pn") == "CN115655695A"
    assert "r2_key" not in output_files
    assert "ai_reply_r2_key" not in output_files
