"""
Pipeline 适配器：将 TaskStorage 与 PatentPipeline 集成。
"""

import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from loguru import logger

from config import settings
from .models import Task, TaskStatus, TaskStep
from .task_storage import TaskStorage, get_task_storage

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
    def __init__(self, storage: TaskStorage = None):
        self.storage = storage or get_task_storage()

    def create_task(
        self,
        pn: Optional[str] = None,
        title: Optional[str] = None,
        raw_pdf_path: Optional[str] = None,
        auto_create_steps: bool = True,
    ) -> Task:
        task_id = str(uuid.uuid4())[:8]

        if not title and pn:
            title = f"专利分析任务 - {pn}"
        elif not title:
            title = f"专利分析任务 - {task_id}"

        if pn:
            output_dir = str(settings.OUTPUT_DIR / pn)
        else:
            output_dir = str(settings.OUTPUT_DIR / task_id)

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

        self.storage.create_task(task)
        if auto_create_steps:
            self._create_default_steps(task_id)

        logger.info(f"Task created: {task_id} (PN: {pn})")
        return task

    def _create_default_steps(self, task_id: str):
        for order, (_, step_name) in enumerate(DEFAULT_PIPELINE_STEPS):
            step = TaskStep(
                step_name=step_name,
                step_order=order,
                status="pending",
                progress=0,
            )
            self.storage.add_task_step(task_id, step)

    def start_task(self, task_id: str) -> bool:
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
        updates = {
            "progress": max(0, min(100, progress)),
            "updated_at": datetime.now().isoformat(),
        }
        if step:
            updates["current_step"] = step

        success = self.storage.update_task(task_id, **updates)
        if success and step and step_status:
            self.storage.update_task_step(task_id, step, status=step_status)
        return success

    def complete_task(self, task_id: str, output_files: Optional[Dict[str, str]] = None) -> bool:
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
        return self.storage.list_tasks(
            status=status,
            pn=pn,
            limit=limit,
            offset=offset,
        )

    def delete_task(self, task_id: str, delete_output: bool = False) -> bool:
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


def get_pipeline_manager(storage: TaskStorage = None) -> PipelineTaskManager:
    return PipelineTaskManager(storage)
