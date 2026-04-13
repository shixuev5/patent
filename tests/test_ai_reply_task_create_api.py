from __future__ import annotations

import asyncio
import io

import pytest
from fastapi import HTTPException, UploadFile

from backend.models import CurrentUser
from backend.routes import tasks as tasks_route
from backend.storage.pipeline_adapter import PipelineTaskManager
from backend.storage import SQLiteTaskStorage
from config import settings


class _FakeRequest:
    def __init__(self, keys: list[str] | None = None):
        self._keys = keys or []

    async def form(self):
        return {key: "1" for key in self._keys}


class _ImmediateDoneTask:
    def add_done_callback(self, callback):
        callback(self)


def _fake_create_task(coro):
    coro.close()
    return _ImmediateDoneTask()


def _upload(filename: str, content: bytes = b"test") -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=filename)


def _mount_task_manager(monkeypatch, tmp_path):
    storage = SQLiteTaskStorage(tmp_path / "ai_reply_create_task.db")
    manager = PipelineTaskManager(storage=storage)
    monkeypatch.setattr(tasks_route, "task_manager", manager)
    monkeypatch.setattr(tasks_route, "emit_system_log", lambda **kwargs: None)
    monkeypatch.setattr(tasks_route, "_enforce_daily_quota", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks_route.asyncio, "create_task", _fake_create_task)
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
    return manager


def test_create_ai_reply_task_accepts_new_claim_fields(monkeypatch, tmp_path) -> None:
    manager = _mount_task_manager(monkeypatch, tmp_path)

    response = asyncio.run(
        tasks_route.create_task(
            request=_FakeRequest(keys=["taskType", "officeActionFile", "responseFile", "previousClaimsFile", "currentClaimsFile"]),
            taskType="ai_reply",
            officeActionFile=_upload("oa.doc"),
            responseFile=_upload("response.docx"),
            previousClaimsFile=_upload("previous_claims.pdf"),
            currentClaimsFile=_upload("current_claims.doc"),
            comparisonDocs=[_upload("comparison_1.doc")],
            current_user=CurrentUser(user_id="authing:user-1"),
        )
    )

    assert response.taskId
    task = manager.get_task(response.taskId)
    assert task is not None
    input_files = task.metadata.get("input_files", [])
    assert [item["file_type"] for item in input_files] == [
        "office_action",
        "response",
        "claims_previous",
        "claims_current",
        "comparison_doc",
    ]


def test_create_ai_reply_task_allows_empty_claim_uploads(monkeypatch, tmp_path) -> None:
    manager = _mount_task_manager(monkeypatch, tmp_path)

    response = asyncio.run(
        tasks_route.create_task(
            request=_FakeRequest(keys=["taskType", "officeActionFile", "responseFile"]),
            taskType="ai_reply",
            officeActionFile=_upload("oa.pdf"),
            responseFile=_upload("response.docx"),
            previousClaimsFile=None,
            currentClaimsFile=None,
            comparisonDocs=None,
            current_user=CurrentUser(user_id="authing:user-2"),
        )
    )

    task = manager.get_task(response.taskId)
    assert task is not None
    input_files = task.metadata.get("input_files", [])
    assert [item["file_type"] for item in input_files] == ["office_action", "response"]
    assert task.title == "oa"


def test_ai_reply_title_prefers_publication_number_when_resolved() -> None:
    title = tasks_route._build_task_title("ai_reply", pn="cn123456a", filename="oa.docx")
    assert title == "CN123456A"


def test_patent_like_title_uses_file_stem_without_suffix() -> None:
    title = tasks_route._build_task_title("patent_analysis", filename="CN123456A.pdf")
    assert title == "CN123456A"


def test_validate_patent_analysis_patent_number_returns_title(monkeypatch) -> None:
    monkeypatch.setattr(
        tasks_route,
        "_query_patent_info_for_publication_number",
        lambda pn: {
            "PN": "CN116745575A",
            "PATENT_ID": "pid-1",
            "TITLE": {"CN": "一种测试装置"},
        },
    )

    result = tasks_route._validate_patent_analysis_patent_number("cn116745575a")

    assert result["patentNumber"] == "CN116745575A"
    assert result["patentTitle"] == "一种测试装置"


def test_validate_patent_number_api_returns_title(monkeypatch, tmp_path) -> None:
    _mount_task_manager(monkeypatch, tmp_path)
    monkeypatch.setattr(
        tasks_route,
        "_validate_patent_analysis_patent_number",
        lambda pn: {
            "patentNumber": "CN116745575A",
            "patentTitle": "一种测试装置",
            "patentInfo": {"PATENT_ID": "pid-1"},
        },
    )

    response = asyncio.run(
        tasks_route.validate_patent_number(
            patentNumber="cn116745575a",
            current_user=CurrentUser(user_id="authing:user-validate"),
        )
    )

    assert response.patentNumber == "CN116745575A"
    assert response.patentTitle == "一种测试装置"
    assert response.exists is True


def test_create_patent_analysis_task_stores_validated_title(monkeypatch, tmp_path) -> None:
    manager = _mount_task_manager(monkeypatch, tmp_path)
    monkeypatch.setattr(
        tasks_route,
        "_validate_patent_analysis_patent_number",
        lambda pn: {
            "patentNumber": "CN116745575A",
            "patentTitle": "一种测试装置",
            "patentInfo": {"PATENT_ID": "pid-1"},
        },
    )

    response = asyncio.run(
        tasks_route.create_task(
            request=_FakeRequest(keys=["taskType", "patentNumber"]),
            taskType="patent_analysis",
            patentNumber="cn116745575a",
            file=None,
            current_user=CurrentUser(user_id="authing:user-analysis"),
        )
    )

    task = manager.get_task(response.taskId)
    assert task is not None
    assert task.pn == "CN116745575A"
    assert task.metadata["patent_title"] == "一种测试装置"


def test_create_patent_analysis_task_rejects_unknown_patent_number(monkeypatch, tmp_path) -> None:
    _mount_task_manager(monkeypatch, tmp_path)

    def _raise_not_found(_pn: str):
        raise HTTPException(status_code=404, detail="未在智慧芽检索到该专利公开号，无法创建 AI 分析任务。")

    monkeypatch.setattr(tasks_route, "_validate_patent_analysis_patent_number", _raise_not_found)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            tasks_route.create_task(
                request=_FakeRequest(keys=["taskType", "patentNumber"]),
                taskType="patent_analysis",
                patentNumber="CN116745575A",
                file=None,
                current_user=CurrentUser(user_id="authing:user-analysis-404"),
            )
        )

    assert exc_info.value.status_code == 404
    assert "未在智慧芽检索到该专利公开号" in str(exc_info.value.detail)


def test_create_ai_reply_task_rejects_legacy_claims_file_field(monkeypatch, tmp_path) -> None:
    _mount_task_manager(monkeypatch, tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            tasks_route.create_task(
                request=_FakeRequest(keys=["taskType", "officeActionFile", "responseFile", "claimsFile"]),
                taskType="ai_reply",
                officeActionFile=_upload("oa.pdf"),
                responseFile=_upload("response.docx"),
                previousClaimsFile=None,
                currentClaimsFile=None,
                comparisonDocs=None,
                current_user=CurrentUser(user_id="authing:user-3"),
            )
        )

    assert exc_info.value.status_code == 400
    assert "claimsFile 已废弃" in str(exc_info.value.detail)


def test_create_ai_reply_task_rejects_unsupported_suffix(monkeypatch, tmp_path) -> None:
    _mount_task_manager(monkeypatch, tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            tasks_route.create_task(
                request=_FakeRequest(keys=["taskType", "officeActionFile", "responseFile"]),
                taskType="ai_reply",
                officeActionFile=_upload("oa.txt"),
                responseFile=_upload("response.doc"),
                previousClaimsFile=None,
                currentClaimsFile=None,
                comparisonDocs=None,
                current_user=CurrentUser(user_id="authing:user-4"),
            )
        )

    assert exc_info.value.status_code == 400
    assert "仅支持 .doc/.docx/.pdf 格式" in str(exc_info.value.detail)
