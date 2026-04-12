from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

from config import settings
from backend.storage import D1TaskStorage
from backend.storage.r2_storage import R2Config, R2Storage
from backend.storage.models import TaskType


def _normalize_pn(value: Any) -> Optional[str]:
    normalized = str(value or "").strip().upper()
    return normalized or None


def _build_key_layout_storage() -> R2Storage:
    key_prefix = str(os.getenv("R2_KEY_PREFIX", "patent")).strip() or "patent"
    return R2Storage(
        R2Config(
            endpoint_url="",
            access_key_id="",
            secret_access_key="",
            bucket="",
            enabled=False,
            key_prefix=key_prefix,
        )
    )


def _expected_output_key_map(task_type: str, pn: str, r2_storage: R2Storage) -> Dict[str, str]:
    if task_type == TaskType.AI_REPLY.value:
        return {
            "r2_key": r2_storage.build_ai_reply_pdf_key(pn),
            "ai_reply_r2_key": r2_storage.build_ai_reply_json_key(pn),
        }
    if task_type == TaskType.AI_REVIEW.value:
        return {
            "r2_key": r2_storage.build_ai_review_pdf_key(pn),
            "ai_review_r2_key": r2_storage.build_ai_review_json_key(pn),
        }
    return {
        "r2_key": r2_storage.build_patent_pdf_key(pn),
        "analysis_r2_key": r2_storage.build_analysis_json_key(pn),
        "patent_r2_key": r2_storage.build_patent_json_key(pn),
    }


def migrate_sqlite_task_metadata_r2_keys(
    db_path: Path,
    *,
    dry_run: bool,
    limit: Optional[int] = None,
) -> Dict[str, int]:
    stats = {
        "scanned": 0,
        "updated_tasks": 0,
        "updated_fields": 0,
        "skipped_invalid_metadata": 0,
        "skipped_no_output_files": 0,
        "skipped_no_pn": 0,
        "unchanged": 0,
    }

    r2_storage = _build_key_layout_storage()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        query = """
            SELECT id, task_type, pn, metadata
            FROM tasks
            WHERE deleted_at IS NULL
            ORDER BY created_at ASC
        """
        params: tuple[Any, ...] = ()
        if limit is not None:
            query = f"{query} LIMIT ?"
            params = (limit,)

        rows = conn.execute(query, params).fetchall()
        now = datetime.now().isoformat()
        for row in rows:
            stats["scanned"] += 1
            task_id = str(row["id"])
            task_type = str(row["task_type"] or TaskType.PATENT_ANALYSIS.value).strip().lower()
            metadata_raw = row["metadata"]
            if not metadata_raw:
                stats["skipped_invalid_metadata"] += 1
                continue
            try:
                metadata = json.loads(metadata_raw)
            except Exception:
                stats["skipped_invalid_metadata"] += 1
                continue
            if not isinstance(metadata, dict):
                stats["skipped_invalid_metadata"] += 1
                continue

            output_files = metadata.get("output_files")
            if not isinstance(output_files, dict):
                stats["skipped_no_output_files"] += 1
                continue

            pn = _normalize_pn(row["pn"] or output_files.get("pn"))
            if not pn:
                stats["skipped_no_pn"] += 1
                continue

            expected = _expected_output_key_map(task_type, pn, r2_storage)
            changed = False

            if output_files.get("pn") != pn:
                output_files["pn"] = pn
                changed = True

            for key, expected_key in expected.items():
                current = output_files.get(key)
                if isinstance(current, str) and current != expected_key:
                    output_files[key] = expected_key
                    changed = True
                    stats["updated_fields"] += 1

            if not changed:
                stats["unchanged"] += 1
                continue

            stats["updated_tasks"] += 1
            if dry_run:
                logger.info(f"[dry-run] task={task_id} metadata.output_files 已匹配新路径")
                continue

            metadata["output_files"] = output_files
            conn.execute(
                "UPDATE tasks SET metadata = ?, updated_at = ? WHERE id = ?",
                (json.dumps(metadata, ensure_ascii=False), now, task_id),
            )

        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    return stats


def _build_d1_storage_from_env() -> D1TaskStorage:
    account_id = str(os.getenv("D1_ACCOUNT_ID", "")).strip()
    database_id = str(os.getenv("D1_DATABASE_ID", "")).strip()
    api_token = str(os.getenv("D1_API_TOKEN", "")).strip()
    api_base_url = str(os.getenv("D1_API_BASE_URL", "https://api.cloudflare.com/client/v4")).strip()
    timeout_seconds = int(str(os.getenv("D1_TIMEOUT_SECONDS", "20")).strip() or "20")
    return D1TaskStorage(
        account_id=account_id,
        database_id=database_id,
        api_token=api_token,
        api_base_url=api_base_url,
        timeout_seconds=timeout_seconds,
    )


def migrate_d1_task_metadata_r2_keys(
    *,
    dry_run: bool,
    limit: Optional[int] = None,
) -> Dict[str, int]:
    stats = {
        "scanned": 0,
        "updated_tasks": 0,
        "updated_fields": 0,
        "update_failed": 0,
        "skipped_invalid_metadata": 0,
        "skipped_no_output_files": 0,
        "skipped_no_pn": 0,
        "unchanged": 0,
    }

    r2_storage = _build_key_layout_storage()
    d1_storage = _build_d1_storage_from_env()
    offset = 0
    page_size = 100

    while True:
        if limit is not None and stats["scanned"] >= limit:
            break
        batch_limit = page_size
        if limit is not None:
            batch_limit = min(page_size, limit - stats["scanned"])
            if batch_limit <= 0:
                break

        tasks = d1_storage.list_tasks(limit=batch_limit, offset=offset, order_by="created_at", order_desc=False)
        if not tasks:
            break

        for task in tasks:
            stats["scanned"] += 1
            metadata = task.metadata if isinstance(task.metadata, dict) else {}
            if not metadata:
                stats["skipped_invalid_metadata"] += 1
                continue
            output_files = metadata.get("output_files")
            if not isinstance(output_files, dict):
                stats["skipped_no_output_files"] += 1
                continue

            task_type = str(task.task_type or TaskType.PATENT_ANALYSIS.value).strip().lower()
            pn = _normalize_pn(task.pn or output_files.get("pn"))
            if not pn:
                stats["skipped_no_pn"] += 1
                continue

            expected = _expected_output_key_map(task_type, pn, r2_storage)
            changed = False

            if output_files.get("pn") != pn:
                output_files["pn"] = pn
                changed = True

            for key, expected_key in expected.items():
                current = output_files.get(key)
                if isinstance(current, str) and current != expected_key:
                    output_files[key] = expected_key
                    changed = True
                    stats["updated_fields"] += 1

            if not changed:
                stats["unchanged"] += 1
                continue

            stats["updated_tasks"] += 1
            if dry_run:
                logger.info(f"[dry-run] task={task.id} metadata.output_files 已匹配新路径")
                continue

            metadata["output_files"] = output_files
            updated = d1_storage.update_task(task.id, metadata=metadata)
            if not updated:
                stats["update_failed"] += 1
                logger.warning(f"task={task.id} metadata 更新失败")

        offset += len(tasks)
        if len(tasks) < batch_limit:
            break

    return stats


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="迁移 task metadata 中的旧 R2 key 到 <PN>/ 固定文件名布局。")
    parser.add_argument(
        "--backend",
        choices=["auto", "sqlite", "d1"],
        default="auto",
        help="存储后端：auto(默认，读取 TASK_STORAGE_BACKEND)/sqlite/d1",
    )
    parser.add_argument(
        "--db-path",
        default=str(settings.DATA_DIR / "tasks.db"),
        help="SQLite tasks.db 路径（仅 sqlite 后端生效）。",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅统计与预览，不写入数据库。")
    parser.add_argument("--limit", type=int, default=None, help="最多扫描任务数。")
    args = parser.parse_args(argv)

    if args.limit is not None and args.limit <= 0:
        parser.error("--limit 必须是正整数。")

    backend = str(args.backend or "auto").strip().lower()
    if backend == "auto":
        backend = str(os.getenv("TASK_STORAGE_BACKEND", "sqlite")).strip().lower() or "sqlite"

    if backend == "d1":
        stats = migrate_d1_task_metadata_r2_keys(
            dry_run=args.dry_run,
            limit=args.limit,
        )
    else:
        db_path = Path(str(args.db_path)).expanduser().resolve()
        if not db_path.exists():
            logger.error(f"数据库文件不存在：{db_path}")
            return 2

        stats = migrate_sqlite_task_metadata_r2_keys(
            db_path=db_path,
            dry_run=args.dry_run,
            limit=args.limit,
        )
    logger.info(f"metadata 迁移完成: {json.dumps(stats, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
