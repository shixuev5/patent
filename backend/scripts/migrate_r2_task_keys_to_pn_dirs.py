from __future__ import annotations

import argparse
import json
from typing import Any, Dict, Iterable, Optional

from loguru import logger

from backend.utils import _build_r2_storage

LEGACY_TASK_PREFIXES = ("ai_analysis", "patent", "ai_review", "ai_reply")


def _normalize_prefix(value: str) -> str:
    return str(value or "").strip().strip("/")


def _iter_legacy_keys(r2_storage: Any, limit: Optional[int]) -> Iterable[str]:
    key_prefix = _normalize_prefix(getattr(r2_storage.config, "key_prefix", ""))
    base_prefix = f"{key_prefix}/" if key_prefix else ""
    scanned = 0
    for legacy_prefix in LEGACY_TASK_PREFIXES:
        list_prefix = f"{base_prefix}{legacy_prefix}/"
        keys = r2_storage.list_keys(prefix=list_prefix, max_keys=1000)
        for key in keys:
            if limit is not None and scanned >= limit:
                return
            scanned += 1
            yield key


def map_legacy_key_to_new_key(r2_storage: Any, source_key: str) -> Optional[str]:
    key_prefix = _normalize_prefix(getattr(r2_storage.config, "key_prefix", ""))
    base_prefix = f"{key_prefix}/" if key_prefix else ""
    normalized_source = str(source_key or "").strip().lstrip("/")
    if not normalized_source:
        return None
    if base_prefix and not normalized_source.startswith(base_prefix):
        return None

    relative_key = normalized_source[len(base_prefix):] if base_prefix else normalized_source
    legacy_prefix, sep, filename = relative_key.partition("/")
    if not sep or not filename or "/" in filename:
        return None

    pn, dot, ext = filename.rpartition(".")
    if not dot or not pn:
        return None
    ext = ext.lower()

    if legacy_prefix == "ai_analysis":
        if ext == "pdf":
            return r2_storage.build_patent_pdf_key(pn)
        if ext == "json":
            return r2_storage.build_analysis_json_key(pn)
        return None
    if legacy_prefix == "patent":
        return r2_storage.build_patent_json_key(pn) if ext == "json" else None
    if legacy_prefix == "ai_review":
        if ext == "pdf":
            return r2_storage.build_ai_review_pdf_key(pn)
        if ext == "json":
            return r2_storage.build_ai_review_json_key(pn)
        return None
    if legacy_prefix == "ai_reply":
        if ext == "pdf":
            return r2_storage.build_ai_reply_pdf_key(pn)
        if ext == "json":
            return r2_storage.build_ai_reply_json_key(pn)
        return None
    return None


def migrate_task_artifacts_to_pn_layout(
    r2_storage: Any,
    *,
    dry_run: bool,
    limit: Optional[int],
    delete_source: bool,
) -> Dict[str, int]:
    stats = {
        "total": 0,
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "overwritten": 0,
        "deleted": 0,
    }

    for source_key in _iter_legacy_keys(r2_storage, limit):
        target_key = map_legacy_key_to_new_key(r2_storage, source_key)
        if not target_key:
            stats["skipped"] += 1
            continue
        if source_key == target_key:
            stats["skipped"] += 1
            continue

        stats["total"] += 1
        target_exists = bool(r2_storage.key_exists(target_key))
        if target_exists:
            stats["overwritten"] += 1

        if dry_run:
            stats["success"] += 1
            logger.info(f"[dry-run] 计划迁移: {source_key} -> {target_key}")
            continue

        copied = bool(r2_storage.copy_key(source_key, target_key))
        if not copied:
            stats["failed"] += 1
            logger.warning(f"迁移失败: {source_key} -> {target_key}")
            continue

        stats["success"] += 1
        logger.info(f"迁移成功: {source_key} -> {target_key}")
        if delete_source:
            if r2_storage.delete_key(source_key):
                stats["deleted"] += 1
            else:
                logger.warning(f"迁移后删除源对象失败: {source_key}")

    return stats


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="迁移任务产物 R2 路径到 <PN>/ 固定文件名布局。")
    parser.add_argument("--dry-run", action="store_true", help="仅输出迁移计划，不实际执行 copy/delete。")
    parser.add_argument("--limit", type=int, default=None, help="最多扫描并处理的旧对象数量。")
    parser.add_argument("--delete-source", action="store_true", help="迁移成功后删除旧对象。")
    args = parser.parse_args(argv)

    if args.limit is not None and args.limit <= 0:
        parser.error("--limit 必须是正整数。")

    if args.dry_run and args.delete_source:
        logger.warning("--dry-run 模式下将忽略 --delete-source。")

    r2_storage = _build_r2_storage()
    if not getattr(r2_storage, "enabled", False):
        logger.error("R2 未启用，无法执行迁移。请先配置 R2 环境变量。")
        return 2

    stats = migrate_task_artifacts_to_pn_layout(
        r2_storage,
        dry_run=args.dry_run,
        limit=args.limit,
        delete_source=(args.delete_source and not args.dry_run),
    )

    logger.info(f"迁移完成: {json.dumps(stats, ensure_ascii=False)}")
    if not args.dry_run and stats["failed"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
