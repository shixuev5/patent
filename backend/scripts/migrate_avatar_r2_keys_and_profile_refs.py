from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse, urlunparse

from loguru import logger

from config import settings
from backend.storage import D1TaskStorage
from backend.utils import _build_r2_storage

AVATAR_READ_PREFIX = "/api/account/profile/avatar/"


def _normalize_prefix(value: str) -> str:
    return str(value or "").strip().strip("/")


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


def _legacy_avatar_prefixes(key_prefix: str) -> list[str]:
    kp = _normalize_prefix(key_prefix)
    if not kp:
        return ["avatars/", "avatar/"]
    return [f"{kp}/avatars/", f"{kp}/avatar/"]


def _map_legacy_avatar_key(key_prefix: str, source_key: str) -> Optional[str]:
    key = str(source_key or "").strip().lstrip("/")
    for prefix in _legacy_avatar_prefixes(key_prefix):
        if key.startswith(prefix):
            remainder = key[len(prefix):].strip("/")
            if not remainder:
                return None
            return f"avatar/{remainder}"
    return None


def migrate_avatar_objects(
    *,
    dry_run: bool,
    delete_source: bool,
    limit: Optional[int],
) -> Dict[str, int]:
    stats = {
        "scanned": 0,
        "migrated": 0,
        "failed": 0,
        "skipped": 0,
        "overwritten": 0,
        "deleted": 0,
    }
    r2_storage = _build_r2_storage()
    if not r2_storage.enabled:
        raise RuntimeError("R2 未启用，无法迁移头像对象。")

    key_prefix = _normalize_prefix(r2_storage.config.key_prefix)
    processed = 0
    for old_prefix in _legacy_avatar_prefixes(key_prefix):
        keys = r2_storage.list_keys(old_prefix, max_keys=1000)
        for source_key in keys:
            if limit is not None and processed >= limit:
                return stats
            processed += 1
            stats["scanned"] += 1

            target_key = _map_legacy_avatar_key(key_prefix, source_key)
            if not target_key or target_key == source_key:
                stats["skipped"] += 1
                continue

            if r2_storage.key_exists(target_key):
                stats["overwritten"] += 1

            if dry_run:
                stats["migrated"] += 1
                logger.info(f"[dry-run] 头像对象迁移: {source_key} -> {target_key}")
                continue

            copied = r2_storage.copy_key(source_key, target_key)
            if not copied:
                stats["failed"] += 1
                logger.warning(f"头像对象迁移失败: {source_key} -> {target_key}")
                continue

            stats["migrated"] += 1
            if delete_source:
                if r2_storage.delete_key(source_key):
                    stats["deleted"] += 1
                else:
                    logger.warning(f"头像对象迁移后删除旧 key 失败: {source_key}")

    return stats


def _build_new_picture_value(picture: str, key_prefix: str) -> Optional[str]:
    text = str(picture or "").strip()
    if not text:
        return None

    def _new_key_if_legacy(key: str) -> Optional[str]:
        return _map_legacy_avatar_key(key_prefix, key)

    if text.startswith("r2/"):
        key = text[3:].strip()
        new_key = _new_key_if_legacy(key)
        if not new_key:
            return None
        return f"r2/{new_key}"

    parsed = urlparse(text)
    path = parsed.path or ""
    if not path.startswith(AVATAR_READ_PREFIX):
        return None
    ref = path[len(AVATAR_READ_PREFIX):].strip()
    if not ref.startswith("r2/"):
        return None
    key = ref[3:].strip()
    new_key = _new_key_if_legacy(key)
    if not new_key:
        return None
    new_ref_path = f"{AVATAR_READ_PREFIX}r2/{new_key}"
    rebuilt = parsed._replace(path=new_ref_path)
    return urlunparse(rebuilt)


def migrate_user_picture_refs_sqlite(
    db_path: Path,
    *,
    dry_run: bool,
    limit: Optional[int],
) -> Dict[str, int]:
    stats = {
        "scanned": 0,
        "updated_users": 0,
        "failed": 0,
        "unchanged": 0,
    }
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    key_prefix = _normalize_prefix(str(os.getenv("R2_KEY_PREFIX", "patent")).strip() or "patent")
    try:
        query = "SELECT owner_id, picture FROM users WHERE picture IS NOT NULL AND TRIM(picture) != ''"
        params: tuple[Any, ...] = ()
        if limit is not None:
            query = f"{query} LIMIT ?"
            params = (limit,)
        rows = conn.execute(query, params).fetchall()
        now_iso = datetime.now().isoformat()
        for row in rows:
            stats["scanned"] += 1
            owner_id = str(row["owner_id"])
            picture = str(row["picture"] or "")
            new_picture = _build_new_picture_value(picture, key_prefix)
            if not new_picture or new_picture == picture:
                stats["unchanged"] += 1
                continue
            stats["updated_users"] += 1
            if dry_run:
                logger.info(f"[dry-run] owner_id={owner_id} picture 将更新")
                continue
            conn.execute(
                "UPDATE users SET picture = ?, updated_at = ? WHERE owner_id = ?",
                (new_picture, now_iso, owner_id),
            )
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    return stats


def migrate_user_picture_refs_d1(
    *,
    dry_run: bool,
    limit: Optional[int],
) -> Dict[str, int]:
    stats = {
        "scanned": 0,
        "updated_users": 0,
        "failed": 0,
        "unchanged": 0,
    }
    key_prefix = _normalize_prefix(str(os.getenv("R2_KEY_PREFIX", "patent")).strip() or "patent")
    d1 = _build_d1_storage_from_env()
    rows = d1._fetchall("SELECT owner_id, picture FROM users WHERE picture IS NOT NULL AND TRIM(picture) != ''")
    now_iso = datetime.now().isoformat()
    for row in rows:
        if limit is not None and stats["scanned"] >= limit:
            break
        stats["scanned"] += 1
        owner_id = str(row.get("owner_id") or "").strip()
        picture = str(row.get("picture") or "")
        new_picture = _build_new_picture_value(picture, key_prefix)
        if not new_picture or new_picture == picture:
            stats["unchanged"] += 1
            continue
        stats["updated_users"] += 1
        if dry_run:
            logger.info(f"[dry-run] owner_id={owner_id} picture 将更新")
            continue
        try:
            result = d1._request(
                "UPDATE users SET picture = ?, updated_at = ? WHERE owner_id = ?",
                [new_picture, now_iso, owner_id],
            )
            changes = int((result.get("meta") or {}).get("changes") or 0)
            if changes <= 0:
                stats["failed"] += 1
                logger.warning(f"owner_id={owner_id} picture 更新未生效")
        except Exception:
            stats["failed"] += 1
            logger.exception(f"owner_id={owner_id} picture 更新异常")
    return stats


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="迁移头像 R2 key 到顶层 avatar/，并回写 users.picture 引用。")
    parser.add_argument("--backend", choices=["auto", "sqlite", "d1"], default="auto")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delete-source", action="store_true", help="头像对象迁移成功后删除旧 key。")
    parser.add_argument("--skip-r2", action="store_true", help="仅回写数据库 picture，不迁移 R2 对象。")
    parser.add_argument("--skip-db", action="store_true", help="仅迁移 R2 对象，不回写数据库 picture。")
    parser.add_argument(
        "--db-path",
        default=str(settings.DATA_DIR / "tasks.db"),
        help="SQLite 路径（backend=sqlite 时生效）",
    )
    args = parser.parse_args(argv)

    if args.limit is not None and args.limit <= 0:
        parser.error("--limit 必须是正整数。")
    if args.skip_r2 and args.skip_db:
        parser.error("--skip-r2 与 --skip-db 不能同时指定。")

    backend = str(args.backend or "auto").strip().lower()
    if backend == "auto":
        backend = str(os.getenv("TASK_STORAGE_BACKEND", "sqlite")).strip().lower() or "sqlite"

    r2_stats = None
    if not args.skip_r2:
        r2_stats = migrate_avatar_objects(
            dry_run=args.dry_run,
            delete_source=(args.delete_source and not args.dry_run),
            limit=args.limit,
        )
        logger.info(f"头像对象迁移结果: {json.dumps(r2_stats, ensure_ascii=False)}")

    db_stats = None
    if not args.skip_db:
        if backend == "d1":
            db_stats = migrate_user_picture_refs_d1(
                dry_run=args.dry_run,
                limit=args.limit,
            )
        else:
            db_path = Path(str(args.db_path)).expanduser().resolve()
            if not db_path.exists():
                logger.error(f"SQLite 数据库不存在: {db_path}")
                return 2
            db_stats = migrate_user_picture_refs_sqlite(
                db_path,
                dry_run=args.dry_run,
                limit=args.limit,
            )
        logger.info(f"头像引用回写结果: {json.dumps(db_stats, ensure_ascii=False)}")

    if not args.dry_run:
        if r2_stats and r2_stats["failed"] > 0:
            return 1
        if db_stats and db_stats["failed"] > 0:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
