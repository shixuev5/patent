from __future__ import annotations

import operator
from typing import Any, Annotated, Dict, List, Optional

from pydantic import BaseModel, Field


_STATUS_PRIORITY = {
    "pending": 0,
    "running": 1,
    "completed": 2,
    "cancelled": 3,
    "failed": 4,
}


def merge_progress(left: float, right: float) -> float:
    return max(float(left or 0.0), float(right or 0.0))


def merge_status(left: str, right: str) -> str:
    left_status = str(left or "pending").lower()
    right_status = str(right or "pending").lower()
    if _STATUS_PRIORITY.get(right_status, 0) >= _STATUS_PRIORITY.get(left_status, 0):
        return right_status
    return left_status


def merge_current_node(left: str, right: str) -> str:
    right_value = str(right or "").strip()
    if right_value:
        return right_value
    return str(left or "")


def merge_paths(left: Dict[str, str], right: Dict[str, str]) -> Dict[str, str]:
    merged: Dict[str, str] = {}
    if isinstance(left, dict):
        merged.update(left)
    if isinstance(right, dict):
        merged.update(right)
    return merged


class ErrorInfo(BaseModel):
    node_name: str = Field(..., description="错误发生节点")
    error_message: str = Field(..., description="错误详情")
    error_type: str = Field("general", description="错误类型")


class WorkflowState(BaseModel):
    pn: str = Field("", description="专利号")
    upload_file_path: Optional[str] = Field(None, description="上传文件路径")

    task_id: str = Field("", description="任务ID")
    output_dir: str = Field("", description="输出目录")

    paths: Annotated[Dict[str, str], merge_paths] = Field(
        default_factory=dict, description="流水线路径字典"
    )
    resolved_pn: str = Field("", description="最终用于产物命名的专利号")

    patent_data: Optional[Dict[str, Any]] = Field(None, description="结构化专利数据")
    parts_db: Optional[Dict[str, Any]] = Field(None, description="知识部件库")
    image_parts: Optional[Dict[str, Any]] = Field(None, description="图像部件映射")
    image_labels: Optional[Dict[str, Any]] = Field(None, description="图像标注中间结果")
    check_result: Optional[Dict[str, Any]] = Field(None, description="形式检查结果")
    report_core_json: Optional[Dict[str, Any]] = Field(None, description="报告核心JSON")
    report_json: Optional[Dict[str, Any]] = Field(None, description="报告JSON")
    search_json: Optional[Dict[str, Any]] = Field(None, description="检索策略JSON")

    final_output_pdf: str = Field("", description="最终PDF路径")
    final_output_md: str = Field("", description="最终Markdown路径")

    errors: Annotated[List[ErrorInfo], operator.add] = Field(default_factory=list, description="错误列表")

    current_node: Annotated[str, merge_current_node] = Field("start", description="当前节点")
    progress: Annotated[float, merge_progress] = Field(0.0, description="进度")
    status: Annotated[str, merge_status] = Field("pending", description="状态")


class WorkflowConfig(BaseModel):
    cache_dir: str = Field(".cache", description="缓存目录")
    timeout: int = Field(300, description="超时时间(秒)")
    max_retries: int = Field(3, description="节点最大重试次数")
    pdf_parser: str = Field("local", description="PDF解析器")
    cancel_event: Any = Field(default=None, description="取消事件")
    enable_checkpoint: bool = Field(False, description="是否启用 LangGraph checkpoint")
    checkpoint_ns: str = Field("patent_analysis", description="checkpoint 命名空间")
    checkpointer: Any = Field(default=None, description="自定义 checkpoint saver")
