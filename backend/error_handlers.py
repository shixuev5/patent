"""Application exception handlers."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from loguru import logger

from backend.storage.errors import StorageError, StorageUnavailableError


def _storage_error_payload(message: str) -> dict:
    return {"detail": {"code": "STORAGE_UNAVAILABLE", "message": message}}


async def _handle_storage_unavailable(_, exc: StorageUnavailableError) -> JSONResponse:
    headers = {}
    if exc.retry_after_seconds is not None:
        headers["Retry-After"] = str(max(1, int(exc.retry_after_seconds)))
    return JSONResponse(
        status_code=503,
        content=_storage_error_payload("存储服务暂不可用，请稍后重试。"),
        headers=headers,
    )


async def _handle_storage_error(_, exc: StorageError) -> JSONResponse:
    logger.exception("Unhandled storage error: {}", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": {"code": "STORAGE_ERROR", "message": "存储服务异常，请稍后重试。"}},
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StorageUnavailableError, _handle_storage_unavailable)
    app.add_exception_handler(StorageError, _handle_storage_error)
