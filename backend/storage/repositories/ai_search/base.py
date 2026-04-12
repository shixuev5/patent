"""Shared AI search repository helpers."""

from __future__ import annotations

from typing import Any, Optional


class AiSearchRunLookupMixin:
    def _resolve_ai_search_run_id(self, task_id: str, plan_version_or_run_id: Any) -> Optional[str]:
        if isinstance(plan_version_or_run_id, str) and not str(plan_version_or_run_id).isdigit():
            return str(plan_version_or_run_id).strip() or None
        if isinstance(plan_version_or_run_id, str) and str(plan_version_or_run_id).strip():
            try:
                plan_version_or_run_id = int(str(plan_version_or_run_id).strip())
            except Exception:
                return str(plan_version_or_run_id).strip() or None
        if isinstance(plan_version_or_run_id, int):
            run = self.get_ai_search_run(task_id, plan_version=int(plan_version_or_run_id))
            return str(run.get("run_id") or "").strip() if run else None
        return None
