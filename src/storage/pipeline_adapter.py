"""
Pipeline 适配器 - 将 TaskStorage 与 PatentPipeline 集成

提供便捷的方法来创建、更新和管理专利分析任务。
"""
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .models import Task, TaskStatus, TaskStep
from .task_storage import TaskStorage, get_task_storage


# 定义专利分析的标准步骤
DEFAULT_PIPELINE_STEPS = [
    ("download", "下载专利文档"),
    ("parse", "解析 PDF 文件"),
    ("transform", "专利结构化转换"),
    ("extract", "知识提取"),
    ("vision", "视觉处理"),
    ("check", "形式缺陷检查"),
    ("generate", "报告内容生成"),
    ("search", "检索策略生成"),
    ("render", "渲染报告"),
]


class PipelineTaskManager:
    """
    Pipeline 任务管理器

    封装 TaskStorage 的操作，提供针对专利分析流程的便捷方法。
    """

    def __init__(self, storage: TaskStorage = None):
        """
        初始化

        Args:
            storage: TaskStorage 实例，如果为 None 则使用全局单例
        """
        self.storage = storage or get_task_storage()

    def create_task(
        self,
        pn: Optional[str] = None,
        title: Optional[str] = None,
        raw_pdf_path: Optional[str] = None,
        auto_create_steps: bool = True,
    ) -> Task:
        """
        创建新的专利分析任务

        Args:
            pn: 专利号
            title: 任务标题
            raw_pdf_path: 原始PDF路径
            auto_create_steps: 是否自动创建标准步骤

        Returns:
            创建的任务对象
        """
        task_id = str(uuid.uuid4())[:8]

        # 自动设置标题
        if not title and pn:
            title = f"专利分析任务 - {pn}"
        elif not title:
            title = f"专利分析任务 - {task_id}"

        # 确定输出目录
        if pn:
            output_dir = str(Path("output") / pn)
        else:
            output_dir = str(Path("output") / task_id)

        task = Task(
            id=task_id,
            pn=pn,
            title=title,
            status=TaskStatus.PENDING,
            progress=0,
            output_dir=output_dir,
            raw_pdf_path=raw_pdf_path,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

        # 保存到数据库
        self.storage.create_task(task)

        # 自动创建标准步骤
        if auto_create_steps:
            self._create_default_steps(task_id)

        logger.info(f"Task created: {task_id} (PN: {pn})")
        return task

    def _create_default_steps(self, task_id: str):
        """创建默认的分析步骤"""
        for order, (step_key, step_name) in enumerate(DEFAULT_PIPELINE_STEPS):
            step = TaskStep(
                step_name=step_name,
                step_order=order,
                status="pending",
                progress=0,
            )
            self.storage.add_task_step(task_id, step)

    def start_task(self, task_id: str) -> bool:
        """
        开始执行任务

        Args:
            task_id: 任务ID

        Returns:
            是否成功
        """
        success = self.storage.update_task(
            task_id,
            status=TaskStatus.PROCESSING.value,
            updated_at=datetime.now().isoformat(),
        )
        if success:
            logger.info(f"Task started: {task_id}")
        return success

    def update_progress(
        self,
        task_id: str,
        progress: int,
        step: Optional[str] = None,
        step_status: Optional[str] = None,
    ) -> bool:
        """
        更新任务进度

        Args:
            task_id: 任务ID
            progress: 进度 (0-100)
            step: 当前步骤名称
            step_status: 步骤状态

        Returns:
            是否成功
        """
        updates = {
            "progress": max(0, min(100, progress)),
            "updated_at": datetime.now().isoformat(),
        }
        if step:
            updates["current_step"] = step

        success = self.storage.update_task(task_id, **updates)

        # 同时更新步骤状态
        if success and step and step_status:
            self.storage.update_task_step(task_id, step, status=step_status)

        return success

    def complete_task(
        self,
        task_id: str,
        output_files: Optional[Dict[str, str]] = None,
    ) -> bool:
        """
        标记任务完成

        Args:
            task_id: 任务ID
            output_files: 输出文件路径字典

        Returns:
            是否成功
        """
        now = datetime.now()

        metadata_updates = {}
        if output_files:
            task = self.storage.get_task(task_id)
            if task and task.metadata:
                metadata_updates = task.metadata.copy()
            metadata_updates["output_files"] = output_files

        updates = {
            "status": TaskStatus.COMPLETED.value,
            "progress": 100,
            "completed_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        if metadata_updates:
            updates["metadata"] = json.dumps(metadata_updates, ensure_ascii=False)

        success = self.storage.update_task(task_id, **updates)
        if success:
            logger.info(f"Task completed: {task_id}")
        return success

    def fail_task(self, task_id: str, error_message: str) -> bool:
        """
        标记任务失败

        Args:
            task_id: 任务ID
            error_message: 错误信息

        Returns:
            是否成功
        """
        success = self.storage.update_task(
            task_id,
            status=TaskStatus.FAILED.value,
            error_message=error_message,
            updated_at=datetime.now().isoformat(),
        )
        if success:
            logger.error(f"Task failed: {task_id} - {error_message}")
        return success

    def get_task(self, task_id: str, include_steps: bool = False) -> Optional[Task]:
        """
        获取任务

        Args:
            task_id: 任务ID
            include_steps: 是否包含步骤详情

        Returns:
            任务对象
        """
        if include_steps:
            return self.storage.get_task_with_steps(task_id)
        return self.storage.get_task(task_id)

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        pn: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Task]:
        """
        列出任务

        Args:
            status: 按状态筛选
            pn: 按专利号筛选
            limit: 数量限制
            offset: 偏移量

        Returns:
            任务列表
        """
        return self.storage.list_tasks(
            status=status,
            pn=pn,
            limit=limit,
            offset=offset,
        )

    def delete_task(self, task_id: str, delete_output: bool = False) -> bool:
        """
        删除任务

        Args:
            task_id: 任务ID
            delete_output: 是否同时删除输出目录

        Returns:
            是否成功
        """
        if delete_output:
            task = self.storage.get_task(task_id)
            if task and task.output_dir:
                import shutil
                try:
                    shutil.rmtree(task.output_dir, ignore_errors=True)
                except Exception as e:
                    logger.warning(f"Failed to delete output dir: {e}")

        success = self.storage.delete_task(task_id)
        if success:
            logger.info(f"Task deleted: {task_id}")
        return success


# 便捷函数：快速创建任务管理器
def get_pipeline_manager(storage: TaskStorage = None) -> PipelineTaskManager:
    """
    获取 PipelineTaskManager 实例

    Args:
        storage: TaskStorage 实例，如果为 None 则使用全局单例

    Returns:
        PipelineTaskManager 实例
    """
    return PipelineTaskManager(storage)
