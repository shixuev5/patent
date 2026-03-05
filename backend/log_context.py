"""
Task-scoped logging helpers.
"""

from contextlib import contextmanager
from typing import Iterator, Optional

from loguru import logger


TASK_TYPE_LABELS = {
    "patent_analysis": "AI 分析",
    "office_action_reply": "AI 研判",
}


def _norm(value: Optional[str], default: str = "-") -> str:
    raw = str(value or "").strip()
    return raw or default


def _optional_norm(value: Optional[str]) -> Optional[str]:
    raw = str(value or "").strip()
    return raw or None


def task_type_label(task_type: Optional[str]) -> str:
    key = _norm(task_type, default="-").lower()
    return TASK_TYPE_LABELS.get(key, key if key != "-" else "-")


def bind_task_logger(
    task_id: Optional[str],
    task_type: Optional[str],
    pn: Optional[str] = None,
    stage: Optional[str] = None,
):
    extras = {
        "task_id": _norm(task_id),
        "task_type_label": task_type_label(task_type),
        "pn": _norm(pn),
    }
    stage_value = _optional_norm(stage)
    if stage_value is not None:
        extras["stage"] = stage_value
    return logger.bind(**extras)


@contextmanager
def task_log_context(
    task_id: Optional[str],
    task_type: Optional[str],
    pn: Optional[str] = None,
    stage: Optional[str] = None,
) -> Iterator[None]:
    extras = {
        "task_id": _norm(task_id),
        "task_type_label": task_type_label(task_type),
        "pn": _norm(pn),
    }
    stage_value = _optional_norm(stage)
    if stage_value is not None:
        extras["stage"] = stage_value

    with logger.contextualize(**extras):
        yield
