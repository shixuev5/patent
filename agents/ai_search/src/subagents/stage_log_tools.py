"""Shared explicit stage-log tool for AI Search subagents."""

from __future__ import annotations

import json
from typing import Any, List

from langchain.tools import ToolRuntime


def build_stage_log_tools(context: Any, stage_kind: str) -> List[Any]:
    def write_stage_log(
        content: str,
        status: str = "completed",
        append: bool = False,
        runtime: ToolRuntime = None,
    ) -> str:
        """写入当前阶段的用户可见工作日志。"""
        result = context.write_stage_log(
            stage_kind=str(stage_kind or "").strip(),
            content=str(content or ""),
            status=str(status or "completed"),
            append=bool(append),
            runtime=runtime,
        )
        return json.dumps(
            {
                "message_id": str(result.get("message_id") or "").strip(),
                "stage_instance_id": str(result.get("stage_instance_id") or "").strip(),
                "status": str(result.get("status") or "").strip(),
            },
            ensure_ascii=False,
        )

    return [write_stage_log]
