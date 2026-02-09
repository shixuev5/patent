"""
快速入门 - 最简单的 TaskStorage 集成方式

在 main.py 中使用的最小示例：

    from src.storage.quickstart import init_task_storage, update_task_progress, complete_task

    # 初始化（Pipeline __init__ 中调用）
    task = init_task_storage(pn)

    # 更新进度（Pipeline 各步骤中调用）
    update_task_progress(task.id, progress=50, step="解析PDF")

    # 完成任务（Pipeline run 方法最后调用）
    complete_task(task.id, pdf_path, md_path)
"""
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from . import get_pipeline_manager, TaskStatus
from .models import Task


def init_task_storage(
    pn: str,
    title: Optional[str] = None,
    task_id: Optional[str] = None,
) -> Task:
    """
    初始化任务存储（创建新任务或获取现有任务）

    在 PatentPipeline.__init__ 中调用：

        self.task = init_task_storage(pn)
        self.task_id = self.task.id

    Args:
        pn: 专利号
        title: 任务标题（可选，默认自动生成）
        task_id: 现有任务ID（可选，用于恢复已有任务）

    Returns:
        任务对象
    """
    manager = get_pipeline_manager()

    # 如果提供了 task_id，尝试获取现有任务
    if task_id:
        existing_task = manager.get_task(task_id)
        if existing_task:
            return existing_task

    # 检查是否已存在相同专利号的进行中任务
    existing = manager.list_tasks(pn=pn, status=TaskStatus.PROCESSING, limit=1)
    if existing:
        return existing[0]

    # 创建新任务
    task = manager.create_task(
        pn=pn,
        title=title or f"专利分析 - {pn}",
        auto_create_steps=True,
    )

    # 立即标记为处理中
    manager.start_task(task.id)

    return task


def update_task_progress(
    task_id: str,
    progress: int,
    step: Optional[str] = None,
    step_status: str = "running",
    error: Optional[str] = None,
) -> bool:
    """
    更新任务进度

    在 Pipeline 各步骤中调用：

        # 步骤开始时
        update_task_progress(self.task_id, progress=10, step="下载专利")

        # 步骤完成时
        update_task_progress(self.task_id, progress=25, step="解析PDF", step_status="completed")

        # 出错时
        update_task_progress(self.task_id, progress=50, step="解析PDF", error="PDF格式错误")

    Args:
        task_id: 任务ID
        progress: 进度 (0-100)
        step: 当前步骤名称
        step_status: 步骤状态 (running/completed/failed)
        error: 错误信息（如果有）

    Returns:
        是否更新成功
    """
    manager = get_pipeline_manager()

    # 如果有错误，标记任务失败
    if error:
        return manager.fail_task(task_id, error)

    # 更新进度
    return manager.update_progress(
        task_id,
        progress=progress,
        step=step,
        step_status=step_status,
    )


def complete_task(
    task_id: str,
    pdf_path: Optional[str] = None,
    md_path: Optional[str] = None,
    **extra_files
) -> bool:
    """
    标记任务完成

    在 Pipeline run 方法最后调用：

        complete_task(
            self.task_id,
            pdf_path=str(self.paths["final_pdf"]),
            md_path=str(self.paths["final_md"]),
        )

    Args:
        task_id: 任务ID
        pdf_path: 生成的PDF路径
        md_path: 生成的Markdown路径
        **extra_files: 其他输出文件路径

    Returns:
        是否成功
    """
    manager = get_pipeline_manager()

    output_files = {}
    if pdf_path:
        output_files["pdf"] = pdf_path
    if md_path:
        output_files["md"] = md_path
    output_files.update(extra_files)

    return manager.complete_task(task_id, output_files)


def get_task_status(task_id: str) -> dict:
    """
    获取任务状态（便捷函数）

    Args:
        task_id: 任务ID

    Returns:
        状态字典
    """
    manager = get_pipeline_manager()
    task = manager.get_task(task_id, include_steps=True)

    if not task:
        return {"exists": False}

    return {
        "exists": True,
        "id": task.id,
        "status": task.status.value,
        "progress": task.progress,
        "current_step": task.current_step,
        "pn": task.pn,
        "title": task.title,
        "error": task.error_message,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "steps": [
            {
                "name": s.step_name,
                "status": s.status,
                "progress": s.progress,
            }
            for s in task.steps
        ],
    }


def list_recent_tasks(limit: int = 10, status: Optional[str] = None) -> list:
    """
    列出最近的任务（便捷函数）

    Args:
        limit: 数量限制
        status: 状态筛选

    Returns:
        任务列表
    """
    manager = get_pipeline_manager()

    status_enum = None
    if status:
        try:
            status_enum = TaskStatus(status)
        except ValueError:
            pass

    tasks = manager.list_tasks(status=status_enum, limit=limit)

    return [
        {
            "id": t.id,
            "pn": t.pn,
            "title": t.title[:30],
            "status": t.status.value,
            "progress": t.progress,
            "created_at": t.created_at.strftime("%Y-%m-%d %H:%M"),
        }
        for t in tasks
    ]
