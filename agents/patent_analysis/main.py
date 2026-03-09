"""
专利分析 Agent 入口（LangGraph）
"""

from __future__ import annotations

import argparse
import os
import uuid
from pathlib import Path
from typing import Any, Dict

from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import RetryPolicy
from loguru import logger

from backend.log_context import bind_task_logger, task_log_context
from backend.logging_setup import setup_logging_utc8
from config import settings

from agents.patent_analysis.src.edges import handle_error
from agents.patent_analysis.src.nodes import (
    CheckGenerateJoinNode,
    CheckNode,
    DownloadNode,
    ExtractNode,
    GenerateNode,
    ParseNode,
    RenderNode,
    SearchNode,
    TransformNode,
    VisionNode,
)
from agents.patent_analysis.src.state import WorkflowConfig, WorkflowState
from agents.patent_analysis.src.workflow_utils import item_get


def create_workflow(config: WorkflowConfig | None = None):
    if config is None:
        config = WorkflowConfig()

    workflow = StateGraph(WorkflowState)
    retry_policy = RetryPolicy(max_attempts=config.max_retries)

    workflow.add_node("download", DownloadNode(config), retry_policy=retry_policy)
    workflow.add_node("parse", ParseNode(config), retry_policy=retry_policy)
    workflow.add_node("transform", TransformNode(config), retry_policy=retry_policy)
    workflow.add_node("extract", ExtractNode(config), retry_policy=retry_policy)
    workflow.add_node("vision", VisionNode(config), retry_policy=retry_policy)
    workflow.add_node("check", CheckNode(config), retry_policy=retry_policy)
    workflow.add_node("generate", GenerateNode(config), retry_policy=retry_policy)
    workflow.add_node("check_generate_join", CheckGenerateJoinNode(config), retry_policy=retry_policy)
    workflow.add_node("search", SearchNode(config), retry_policy=retry_policy)
    workflow.add_node("render", RenderNode(config), retry_policy=retry_policy)
    workflow.add_node("handle_error", handle_error)

    workflow.set_entry_point("download")

    def create_router(next_node: str):
        def router(state: Any) -> str:
            status = str(item_get(state, "status", "pending") or "pending").lower()
            if status in {"failed", "cancelled"}:
                return "failed"
            return next_node

        return router

    def route_from_vision(state: Any):
        status = str(item_get(state, "status", "pending") or "pending").lower()
        if status in {"failed", "cancelled"}:
            return "failed"
        return ["check", "generate"]

    workflow.add_conditional_edges(
        "download",
        create_router("parse"),
        {"failed": "handle_error", "parse": "parse"},
    )
    workflow.add_conditional_edges(
        "parse",
        create_router("transform"),
        {"failed": "handle_error", "transform": "transform"},
    )
    workflow.add_conditional_edges(
        "transform",
        create_router("extract"),
        {"failed": "handle_error", "extract": "extract"},
    )
    workflow.add_conditional_edges(
        "extract",
        create_router("vision"),
        {"failed": "handle_error", "vision": "vision"},
    )
    workflow.add_conditional_edges(
        "vision",
        route_from_vision,
        {
            "failed": "handle_error",
            "check": "check",
            "generate": "generate",
        },
    )

    workflow.add_edge("check", "check_generate_join")
    workflow.add_edge("generate", "check_generate_join")

    workflow.add_conditional_edges(
        "check_generate_join",
        create_router("search"),
        {"failed": "handle_error", "search": "search"},
    )
    workflow.add_conditional_edges(
        "search",
        create_router("render"),
        {"failed": "handle_error", "render": "render"},
    )
    workflow.add_conditional_edges(
        "render",
        create_router("end"),
        {"failed": "handle_error", "end": END},
    )
    workflow.add_edge("handle_error", END)

    checkpointer = None
    if config.enable_checkpoint:
        checkpointer = config.checkpointer or InMemorySaver()

    if checkpointer is not None:
        return workflow.compile(checkpointer=checkpointer)
    return workflow.compile()


def build_runtime_config(task_id: str, checkpoint_ns: str = "patent_analysis") -> Dict[str, Dict[str, str]]:
    return {
        "configurable": {
            "thread_id": str(task_id).strip() or "patent-task",
            "checkpoint_ns": str(checkpoint_ns).strip() or "patent_analysis",
        }
    }


def setup_logging(log_dir: str | None = None) -> None:
    log_file = None
    if log_dir:
        log_dir_path = Path(log_dir)
        log_dir_path.mkdir(parents=True, exist_ok=True)
        log_file = str(log_dir_path / "workflow.log")
    setup_logging_utc8(level="INFO", log_file=log_file, file_level="DEBUG", rotation="10 MB")


def _to_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="专利分析 LangGraph 流程")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pn", help="单个专利号")
    group.add_argument("--upload-file", help="上传的专利 PDF 路径")
    parser.add_argument("--task-id", help="任务ID（可选）")

    args = parser.parse_args()

    task_id = str(args.task_id or str(uuid.uuid4())[:8]).strip()
    pn = str(args.pn or "").strip()
    upload_file_path = str(args.upload_file or "").strip()

    if upload_file_path and not Path(upload_file_path).exists():
        logger.error(f"上传文件不存在: {upload_file_path}")
        return 1

    output_dir = settings.OUTPUT_DIR / task_id
    output_dir.mkdir(parents=True, exist_ok=True)

    setup_logging(str(output_dir / "logs"))
    task_logger = bind_task_logger(task_id, "patent_analysis", pn=pn or "-", stage="main")
    task_logger.info(f"任务ID: {task_id}")
    task_logger.info(f"输出目录: {output_dir}")

    config = WorkflowConfig(
        cache_dir=str(output_dir / ".cache"),
        pdf_parser=os.getenv("PDF_PARSER", "local"),
        enable_checkpoint=True,
        checkpointer=InMemorySaver(),
    )

    initial_state = WorkflowState(
        pn=pn,
        upload_file_path=upload_file_path or None,
        output_dir=str(output_dir),
        task_id=task_id,
        current_node="start",
        status="pending",
        progress=0.0,
    )

    task_logger.info("创建 LangGraph 工作流")
    workflow = create_workflow(config)

    task_logger.info("开始执行工作流")
    try:
        with task_log_context(task_id, "patent_analysis", pn=pn or "-"):
            result = workflow.invoke(
                initial_state,
                config=build_runtime_config(task_id, checkpoint_ns=config.checkpoint_ns),
            )
    except Exception as exc:  # pragma: no cover - runtime safeguard
        task_logger.error(f"工作流执行异常: {exc}")
        task_logger.exception("异常堆栈")
        return 1

    result_dict = _to_dict(result)
    status = str(result_dict.get("status", "failed") or "failed").lower()

    if status in {"failed", "cancelled"}:
        errors = result_dict.get("errors") or []
        task_logger.error(f"工作流执行{status}，错误数量: {len(errors)}")
        for error in errors:
            node_name = item_get(error, "node_name", "unknown")
            error_message = item_get(error, "error_message", str(error))
            task_logger.error(f"节点 {node_name}: {error_message}")
        return 1

    output_pdf = str(result_dict.get("final_output_pdf", "")).strip()
    resolved_pn = str(result_dict.get("resolved_pn", "")).strip()

    task_logger.success("工作流执行成功")
    task_logger.info(f"最终专利号: {resolved_pn or pn or '-'}")
    task_logger.info(f"输出文件: {output_pdf}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
