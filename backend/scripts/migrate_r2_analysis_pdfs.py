from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import List

from backend.utils import _build_r2_storage


@dataclass
class MigrationStats:
    total: int = 0
    success: int = 0
    skipped_exists: int = 0
    skipped_invalid: int = 0
    failed: int = 0
    deleted: int = 0


def _extract_pn_from_key(key: str) -> str:
    name = Path(str(key or "")).name
    stem = Path(name).stem
    return str(stem or "").strip().upper()


def migrate_old_report_pdfs(delete_source: bool = False, limit: int = 0) -> MigrationStats:
    storage = _build_r2_storage()
    if not storage.enabled:
        raise RuntimeError("R2 未启用，无法执行迁移。")

    legacy_prefix = f"{storage.config.key_prefix}/reports/"
    keys = storage.list_keys(legacy_prefix)
    if limit > 0:
        keys = keys[:limit]

    stats = MigrationStats(total=len(keys))
    for source_key in keys:
        if not str(source_key).lower().endswith(".pdf"):
            stats.skipped_invalid += 1
            continue

        pn = _extract_pn_from_key(source_key)
        if not pn:
            stats.skipped_invalid += 1
            continue

        target_key = storage.build_patent_pdf_key(pn)
        if source_key == target_key:
            stats.skipped_invalid += 1
            continue

        if storage.key_exists(target_key):
            stats.skipped_exists += 1
            continue

        ok = storage.copy_key(source_key, target_key)
        if not ok:
            stats.failed += 1
            continue

        stats.success += 1
        if delete_source:
            if storage.delete_key(source_key):
                stats.deleted += 1

    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移 R2 旧 reports/*.pdf 到 analysis/*.pdf")
    parser.add_argument(
        "--delete-source",
        action="store_true",
        help="迁移成功后删除旧 key（默认不删除）。",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="仅处理前 N 条 key，0 表示全部。",
    )
    args = parser.parse_args()

    stats = migrate_old_report_pdfs(delete_source=bool(args.delete_source), limit=int(args.limit or 0))
    print(
        "迁移完成: "
        f"total={stats.total} success={stats.success} skipped_exists={stats.skipped_exists} "
        f"skipped_invalid={stats.skipped_invalid} failed={stats.failed} deleted={stats.deleted}"
    )
    return 0 if stats.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
