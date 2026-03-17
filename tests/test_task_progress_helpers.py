import hashlib
from types import SimpleNamespace

from backend.routes import tasks as tasks_route
from backend.storage.models import TaskType
from backend.storage.r2_storage import R2Config, R2Storage


def test_should_persist_progress_update_with_throttle_window() -> None:
    assert tasks_route._should_persist_progress_update(
        previous_step="步骤A",
        next_step="步骤A",
        last_persist_at=0.0,
        now=1.0,
    )
    assert not tasks_route._should_persist_progress_update(
        previous_step="步骤A",
        next_step="步骤A",
        last_persist_at=10.0,
        now=12.0,
        throttle_seconds=3.0,
    )
    assert tasks_route._should_persist_progress_update(
        previous_step="步骤A",
        next_step="步骤A",
        last_persist_at=10.0,
        now=13.2,
        throttle_seconds=3.0,
    )
    assert tasks_route._should_persist_progress_update(
        previous_step="步骤A",
        next_step="步骤B",
        last_persist_at=11.0,
        now=11.1,
        throttle_seconds=3.0,
    )


def test_should_persist_progress_update_respects_minimum_throttle_guard() -> None:
    assert not tasks_route._should_persist_progress_update(
        previous_step="步骤A",
        next_step="步骤A",
        last_persist_at=1.0,
        now=1.05,
        throttle_seconds=0.0,
    )
    assert tasks_route._should_persist_progress_update(
        previous_step="步骤A",
        next_step="步骤A",
        last_persist_at=1.0,
        now=1.11,
        throttle_seconds=0.0,
    )


def test_build_task_pdf_r2_key_uses_task_type_specific_layout() -> None:
    storage = R2Storage(
        R2Config(
            endpoint_url="https://example.invalid",
            access_key_id="ak",
            secret_access_key="sk",
            bucket="bucket",
            enabled=False,
            key_prefix="workspace",
        )
    )

    assert (
        tasks_route._build_task_pdf_r2_key(TaskType.PATENT_ANALYSIS.value, "cn123", storage)
        == "workspace/CN123/ai_analysis.pdf"
    )
    assert (
        tasks_route._build_task_pdf_r2_key(TaskType.AI_REVIEW.value, "cn123", storage)
        == "workspace/CN123/ai_review.pdf"
    )
    assert (
        tasks_route._build_task_pdf_r2_key(TaskType.AI_REPLY.value, "cn123", storage)
        == "workspace/CN123/ai_reply.pdf"
    )
    assert tasks_route._build_task_pdf_r2_key(TaskType.AI_REPLY.value, None, storage) is None


def test_build_task_download_filename_prefers_pn_then_title_then_task_id() -> None:
    task = SimpleNamespace(id="task-1", pn="CN202600001A", title="标题A")
    assert (
        tasks_route._build_task_download_filename(TaskType.AI_REPLY.value, task)
        == "AI 答复报告_CN202600001A.pdf"
    )

    task = SimpleNamespace(id="task-2", pn=None, title="标题B")
    assert (
        tasks_route._build_task_download_filename(TaskType.AI_REVIEW.value, task)
        == "AI 审查报告_标题B.pdf"
    )

    task = SimpleNamespace(id="task-3", pn=None, title=None)
    assert (
        tasks_route._build_task_download_filename(TaskType.PATENT_ANALYSIS.value, task)
        == "AI 分析报告_task-3.pdf"
    )


def test_resolve_input_sha256_prefers_explicit_value() -> None:
    assert tasks_route._resolve_input_sha256("ABCDEF", "/not/exist.pdf") == "abcdef"


def test_resolve_input_sha256_falls_back_to_file_digest(tmp_path) -> None:
    source = tmp_path / "raw.pdf"
    source.write_bytes(b"mock patent pdf content")
    expected = hashlib.sha256(b"mock patent pdf content").hexdigest()
    assert tasks_route._resolve_input_sha256(None, str(source)) == expected
