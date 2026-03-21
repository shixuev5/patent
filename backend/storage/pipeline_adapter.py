"""
Pipeline 适配器：将 TaskStorage 与 PatentPipeline 集成。
"""

import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Any

from loguru import logger

from config import settings
from backend.time_utils import utc_now
from .models import Task, TaskStatus, TaskType
from .task_storage import get_task_storage

DEFAULT_PIPELINE_STEPS = [
    ("download", "下载专利文档"),
    ("parse", "解析 PDF 文件"),
    ("transform", "专利结构化转换"),
    ("extract", "知识提取"),
    ("vision", "视觉处理"),
    ("check", "AI 审查"),
    ("generate", "报告内容生成"),
    ("search_matrix", "生成检索要素"),
    ("search_semantic", "生成语义检索"),
    ("search_join", "汇总检索策略"),
    ("render", "渲染报告"),
]


class PipelineTaskManager:
    def __init__(self, storage: Any = None):
        self.storage = storage or get_task_storage()

    def create_task(
        self,
        owner_id: Optional[str] = None,
        task_type: str = TaskType.PATENT_ANALYSIS.value,
        pn: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Task:
        task_id = str(uuid.uuid4())[:8]

        if not title and pn:
            title = f"AI 分析任务 - {pn}"
        elif not title:
            title = f"AI 分析任务 - {task_id}"

        output_dir = str(settings.OUTPUT_DIR / task_id)

        task = Task(
            id=task_id,
            owner_id=owner_id,
            task_type=task_type,
            pn=pn,
            title=title,
            status=TaskStatus.PENDING,
            progress=0,
            output_dir=output_dir,
            created_at=utc_now(),
            updated_at=utc_now(),
        )

        self.storage.create_task(task)

        logger.info(f"任务已创建：{task_id}（PN: {pn}）")
        return task

    def start_task(self, task_id: str) -> bool:
        success = self.storage.update_task(
            task_id,
            status=TaskStatus.PROCESSING.value,
            updated_at=utc_now(),
        )
        if success:
            logger.info(f"任务已开始：{task_id}")
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
            "updated_at": utc_now(),
        }
        if step:
            updates["current_step"] = step

        success = self.storage.update_task(task_id, **updates)
        return success

    def complete_task(self, task_id: str, output_files: Optional[Dict[str, str]] = None) -> bool:
        now = utc_now()
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
            try:
                task = self.storage.get_task(task_id)
                analysis_pn = None
                if output_files and isinstance(output_files, dict):
                    analysis_pn = output_files.get("pn")
                if not analysis_pn and task:
                    analysis_pn = task.pn
                if analysis_pn and task and str(task.task_type) == TaskType.PATENT_ANALYSIS.value:
                    self.storage.record_patent_analysis(analysis_pn)
            except Exception as exc:
                logger.warning(f"记录 AI 分析统计失败（{task_id}）：{exc}")
            logger.info(f"任务已完成：{task_id}")
        return success

    def _resolve_terminal_completed_at(self, task_id: str, now: datetime) -> str:
        """终态时间以首次写入为准，避免重复失败/取消覆盖。"""
        task = self.storage.get_task(task_id)
        completed_at = getattr(task, "completed_at", None) if task else None
        if not completed_at:
            return now.isoformat()
        if hasattr(completed_at, "isoformat"):
            return completed_at.isoformat()
        return str(completed_at)

    def fail_task(self, task_id: str, error_message: str) -> bool:
        now = utc_now()
        success = self.storage.update_task(
            task_id,
            status=TaskStatus.FAILED.value,
            error_message=error_message,
            completed_at=self._resolve_terminal_completed_at(task_id, now),
            updated_at=now.isoformat(),
        )
        if success:
            logger.error(f"任务失败：{task_id} - {error_message}")
        return success

    def cancel_task(self, task_id: str, error_message: str = "任务已取消") -> bool:
        now = utc_now()
        success = self.storage.update_task(
            task_id,
            status=TaskStatus.CANCELLED.value,
            error_message=error_message,
            completed_at=self._resolve_terminal_completed_at(task_id, now),
            updated_at=now.isoformat(),
        )
        if success:
            logger.info(f"任务已取消：{task_id} - {error_message}")
        return success

    def get_task(self, task_id: str) -> Optional[Task]:
        return self.storage.get_task(task_id)

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        pn: Optional[str] = None,
        owner_id: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Task]:
        return self.storage.list_tasks(
            status=status,
            pn=pn,
            owner_id=owner_id,
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
                    logger.warning(f"删除输出目录失败：{e}")

        success = self.storage.delete_task(task_id)
        if success:
            logger.info(f"任务已删除：{task_id}")
        return success


def get_pipeline_manager(storage: Any = None) -> PipelineTaskManager:
    return PipelineTaskManager(storage)
