"""
SQLite 任务存储模块

提供任务的持久化存储能力，包括：
- 任务 CRUD 操作
- 任务步骤管理
- 任务查询和筛选
- 批量操作

使用 SQLite 作为后端存储，轻量且无需额外服务。
"""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from loguru import logger

from config import settings
from .models import Task, TaskStep, TaskStatus


class TaskStorage:
    """
    任务存储管理器

    使用 SQLite 持久化存储任务数据，支持多线程安全访问。
    """

    # SQL 语句定义
    CREATE_TABLES_SQL = """
    -- 任务主表
    CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        pn TEXT,
        title TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        progress INTEGER DEFAULT 0,
        current_step TEXT,
        output_dir TEXT,
        raw_pdf_path TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT,
        metadata TEXT
    );

    -- 任务步骤表
    CREATE TABLE IF NOT EXISTS task_steps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL,
        step_name TEXT NOT NULL,
        step_order INTEGER NOT NULL,
        status TEXT DEFAULT 'pending',
        progress INTEGER DEFAULT 0,
        start_time TEXT,
        end_time TEXT,
        error_message TEXT,
        metadata TEXT,
        FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
    );

    -- 创建索引
    CREATE INDEX IF NOT EXISTS idx_tasks_pn ON tasks(pn);
    CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
    CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at);
    CREATE INDEX IF NOT EXISTS idx_steps_task_id ON task_steps(task_id);
    """

    def __init__(self, db_path: Union[str, Path] = None):
        """
        初始化任务存储

        Args:
            db_path: SQLite 数据库文件路径，默认为项目目录下的 data/tasks.db
        """
        if db_path is None:
            # 默认路径: 项目根目录/data/tasks.db
            db_path = settings.DATA_DIR / "tasks.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # 线程本地存储，用于连接复用
        self._local = threading.local()

        # 初始化数据库
        self._init_database()

        logger.info(f"TaskStorage initialized: {self.db_path}")

    def _init_database(self):
        """初始化数据库表结构"""
        with self._get_connection() as conn:
            conn.executescript(self.CREATE_TABLES_SQL)
            conn.commit()

    @contextmanager
    def _get_connection(self):
        """
        获取数据库连接（上下文管理器）

        每个线程使用独立的连接，确保线程安全。
        """
        thread_id = threading.get_ident()

        # 检查当前线程是否已有连接
        if not hasattr(self._local, 'connection') or self._local.connection is None:
            self._local.connection = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                isolation_level=None,  # 自动提交模式
            )
            # 启用外键支持
            self._local.connection.execute("PRAGMA foreign_keys = ON")
            # 设置行工厂，使查询结果可以通过列名访问
            self._local.connection.row_factory = sqlite3.Row

        try:
            yield self._local.connection
        except Exception as e:
            self._local.connection.rollback()
            raise e

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        """将数据库行转换为 Task 对象"""
        return Task(
            id=row["id"],
            pn=row["pn"],
            title=row["title"],
            status=TaskStatus(row["status"]),
            progress=row["progress"],
            current_step=row["current_step"],
            output_dir=row["output_dir"],
            raw_pdf_path=row["raw_pdf_path"],
            error_message=row["error_message"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )

    def _row_to_step(self, row: sqlite3.Row) -> TaskStep:
        """将数据库行转换为 TaskStep 对象"""
        return TaskStep(
            step_name=row["step_name"],
            step_order=row["step_order"],
            status=row["status"],
            progress=row["progress"],
            start_time=datetime.fromisoformat(row["start_time"]) if row["start_time"] else None,
            end_time=datetime.fromisoformat(row["end_time"]) if row["end_time"] else None,
            error_message=row["error_message"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )

    # ==================== 核心 CRUD 操作 ====================

    def create_task(self, task: Task) -> Task:
        """
        创建新任务

        Args:
            task: 任务对象（id 必须已设置）

        Returns:
            创建的任务对象
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    id, pn, title, status, progress, current_step,
                    output_dir, raw_pdf_path, error_message,
                    created_at, updated_at, completed_at, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.pn,
                    task.title,
                    task.status.value,
                    task.progress,
                    task.current_step,
                    task.output_dir,
                    task.raw_pdf_path,
                    task.error_message,
                    task.created_at.isoformat(),
                    task.updated_at.isoformat(),
                    task.completed_at.isoformat() if task.completed_at else None,
                    json.dumps(task.metadata, ensure_ascii=False) if task.metadata else None,
                ),
            )
            conn.commit()

        logger.debug(f"Task created: {task.id}")
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        """
        获取任务

        Args:
            task_id: 任务ID

        Returns:
            任务对象，如果不存在返回 None
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM tasks WHERE id = ?",
                (task_id,),
            )
            row = cursor.fetchone()

        if row is None:
            return None

        return self._row_to_task(row)

    def get_task_with_steps(self, task_id: str) -> Optional[Task]:
        """
        获取任务及其所有步骤

        Args:
            task_id: 任务ID

        Returns:
            包含 steps 列表的任务对象
        """
        task = self.get_task(task_id)
        if task is None:
            return None

        task.steps = self.get_task_steps(task_id)
        return task

    def update_task(self, task_id: str, **kwargs) -> bool:
        """
        更新任务字段

        Args:
            task_id: 任务ID
            **kwargs: 要更新的字段（如 status, progress, current_step 等）

        Returns:
            是否更新成功
        """
        allowed_fields = {
            "pn", "title", "status", "progress", "current_step",
            "output_dir", "raw_pdf_path", "error_message",
            "completed_at", "metadata", "updated_at",
        }

        # 过滤非法字段
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

        if not updates:
            logger.warning(f"No valid fields to update for task {task_id}")
            return False

        # 自动更新 updated_at
        updates["updated_at"] = datetime.now().isoformat()

        # 构建 SQL
        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        values = list(updates.values()) + [task_id]

        with self._get_connection() as conn:
            cursor = conn.execute(
                f"UPDATE tasks SET {set_clause} WHERE id = ?",
                values,
            )
            conn.commit()
            success = cursor.rowcount > 0

        if success:
            logger.debug(f"Task {task_id} updated: {list(updates.keys())}")
        return success

    def delete_task(self, task_id: str) -> bool:
        """
        删除任务

        Args:
            task_id: 任务ID

        Returns:
            是否删除成功
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM tasks WHERE id = ?",
                (task_id,),
            )
            conn.commit()
            success = cursor.rowcount > 0

        if success:
            logger.info(f"Task deleted: {task_id}")
        return success

    # ==================== 任务步骤管理 ====================

    def add_task_step(self, task_id: str, step: TaskStep) -> bool:
        """
        添加任务步骤

        Args:
            task_id: 任务ID
            step: 步骤对象

        Returns:
            是否添加成功
        """
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO task_steps (
                    task_id, step_name, step_order, status, progress,
                    start_time, end_time, error_message, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    step.step_name,
                    step.step_order,
                    step.status,
                    step.progress,
                    step.start_time.isoformat() if step.start_time else None,
                    step.end_time.isoformat() if step.end_time else None,
                    step.error_message,
                    json.dumps(step.metadata, ensure_ascii=False) if step.metadata else None,
                ),
            )
            conn.commit()

        logger.debug(f"Step added to task {task_id}: {step.step_name}")
        return True

    def get_task_steps(self, task_id: str) -> List[TaskStep]:
        """
        获取任务的所有步骤

        Args:
            task_id: 任务ID

        Returns:
            步骤列表（按 step_order 排序）
        """
        with self._get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM task_steps
                WHERE task_id = ?
                ORDER BY step_order ASC
                """,
                (task_id,),
            )
            rows = cursor.fetchall()

        return [self._row_to_step(row) for row in rows]

    def update_task_step(self, task_id: str, step_name: str, **kwargs) -> bool:
        """
        更新任务步骤

        Args:
            task_id: 任务ID
            step_name: 步骤名称
            **kwargs: 要更新的字段

        Returns:
            是否更新成功
        """
        allowed_fields = {"status", "progress", "start_time", "end_time", "error_message", "metadata"}
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

        if not updates:
            return False

        # 处理特殊字段
        params = []
        set_clauses = []
        for k, v in updates.items():
            if k in ("start_time", "end_time") and v is not None:
                v = v.isoformat() if isinstance(v, datetime) else v
            elif k == "metadata" and v is not None:
                v = json.dumps(v, ensure_ascii=False)
            set_clauses.append(f"{k} = ?")
            params.append(v)

        params.extend([task_id, step_name])

        with self._get_connection() as conn:
            cursor = conn.execute(
                f"""
                UPDATE task_steps
                SET {', '.join(set_clauses)}
                WHERE task_id = ? AND step_name = ?
                """,
                params,
            )
            conn.commit()
            success = cursor.rowcount > 0

        return success

    def delete_task_steps(self, task_id: str) -> bool:
        """
        删除任务的所有步骤

        Args:
            task_id: 任务ID

        Returns:
            是否删除成功
        """
        with self._get_connection() as conn:
            conn.execute(
                "DELETE FROM task_steps WHERE task_id = ?",
                (task_id,),
            )
            conn.commit()
        return True

    # ==================== 批量查询操作 ====================

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        pn: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        order_by: str = "created_at",
        order_desc: bool = True,
    ) -> List[Task]:
        """
        查询任务列表

        Args:
            status: 按状态筛选
            pn: 按专利号筛选（支持模糊匹配）
            limit: 返回数量限制
            offset: 分页偏移
            order_by: 排序字段
            order_desc: 是否降序

        Returns:
            任务列表
        """
        where_clauses = ["1=1"]
        params = []

        if status:
            where_clauses.append("status = ?")
            params.append(status.value)

        if pn:
            where_clauses.append("pn LIKE ?")
            params.append(f"%{pn}%")

        order_direction = "DESC" if order_desc else "ASC"

        sql = f"""
            SELECT * FROM tasks
            WHERE {' AND '.join(where_clauses)}
            ORDER BY {order_by} {order_direction}
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        with self._get_connection() as conn:
            cursor = conn.execute(sql, params)
            rows = cursor.fetchall()

        return [self._row_to_task(row) for row in rows]

    def count_tasks(
        self,
        status: Optional[TaskStatus] = None,
        pn: Optional[str] = None,
    ) -> int:
        """
        统计任务数量

        Args:
            status: 按状态筛选
            pn: 按专利号筛选

        Returns:
            任务数量
        """
        where_clauses = ["1=1"]
        params = []

        if status:
            where_clauses.append("status = ?")
            params.append(status.value)

        if pn:
            where_clauses.append("pn LIKE ?")
            params.append(f"%{pn}%")

        sql = f"SELECT COUNT(*) FROM tasks WHERE {' AND '.join(where_clauses)}"

        with self._get_connection() as conn:
            cursor = conn.execute(sql, params)
            return cursor.fetchone()[0]

    def get_statistics(self) -> Dict[str, Any]:
        """
        获取任务统计信息

        Returns:
            统计数据字典
        """
        with self._get_connection() as conn:
            # 各状态任务数量
            cursor = conn.execute("""
                SELECT status, COUNT(*) as count
                FROM tasks
                GROUP BY status
            """)
            status_counts = {row["status"]: row["count"] for row in cursor.fetchall()}

            # 今日任务数
            today = datetime.now().strftime("%Y-%m-%d")
            cursor = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE DATE(created_at) = ?",
                (today,),
            )
            today_count = cursor.fetchone()[0]

            # 平均处理时间（已完成的任务）
            cursor = conn.execute(
                """
                SELECT AVG(
                    julianday(completed_at) - julianday(created_at)
                ) * 24 * 60 as avg_minutes
                FROM tasks
                WHERE status = 'completed' AND completed_at IS NOT NULL
                """
            )
            row = cursor.fetchone()
            avg_duration = row[0] if row and row[0] else None

        total = sum(status_counts.values())

        return {
            "total": total,
            "by_status": status_counts,
            "today_created": today_count,
            "avg_duration_minutes": round(avg_duration, 2) if avg_duration else None,
        }

    # ==================== 清理和维护 ====================

    def cleanup_old_tasks(self, days: int = 30, dry_run: bool = False) -> int:
        """
        清理指定天数之前的已完成/失败任务

        Args:
            days: 保留最近多少天的任务
            dry_run: 如果为 True，只返回将要删除的数量，不实际删除

        Returns:
            删除（或将要删除）的任务数量
        """
        cutoff_date = (datetime.now() - timedelta(days=days)).isoformat()

        with self._get_connection() as conn:
            # 查询要删除的任务
            cursor = conn.execute(
                """
                SELECT id FROM tasks
                WHERE updated_at < ?
                AND status IN ('completed', 'failed', 'cancelled')
                """,
                (cutoff_date,),
            )
            task_ids = [row["id"] for row in cursor.fetchall()]

            if dry_run:
                return len(task_ids)

            # 删除任务（级联删除步骤）
            if task_ids:
                placeholders = ",".join(["?"] * len(task_ids))
                conn.execute(
                    f"DELETE FROM tasks WHERE id IN ({placeholders})",
                    task_ids,
                )
                conn.commit()
                logger.info(f"Cleaned up {len(task_ids)} old tasks")

        return len(task_ids)

    def vacuum(self):
        """执行数据库 VACUUM，释放空间"""
        with self._get_connection() as conn:
            conn.execute("VACUUM")
            conn.commit()
        logger.info("Database vacuum completed")


# 全局存储实例（单例模式）
_storage_instance: Optional[TaskStorage] = None
_storage_lock = threading.Lock()


def get_task_storage(db_path: Optional[Union[str, Path]] = None) -> TaskStorage:
    """
    获取 TaskStorage 单例实例

    Args:
        db_path: 数据库路径，首次创建时有效

    Returns:
        TaskStorage 实例
    """
    global _storage_instance

    if _storage_instance is None:
        with _storage_lock:
            if _storage_instance is None:
                _storage_instance = TaskStorage(db_path)

    return _storage_instance


def reset_storage_instance():
    """重置存储实例（主要用于测试）"""
    global _storage_instance
    with _storage_lock:
        _storage_instance = None
