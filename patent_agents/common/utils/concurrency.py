"""
Helpers for context-safe concurrent execution.
"""

from __future__ import annotations

import contextvars
from concurrent.futures import Executor, Future
from typing import Any, Callable, TypeVar


T = TypeVar("T")


def submit_with_current_context(
    executor: Executor,
    func: Callable[..., T],
    *args: Any,
    **kwargs: Any,
) -> Future[T]:
    """
    Submit a callable to an executor while preserving current contextvars.
    """
    ctx = contextvars.copy_context()
    return executor.submit(_run_in_context, ctx, func, args, kwargs)


def _run_in_context(
    ctx: contextvars.Context,
    func: Callable[..., T],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> T:
    return ctx.run(func, *args, **kwargs)
