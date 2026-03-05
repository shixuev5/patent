"""
API 工具函数
"""
import os
import shutil
from pathlib import Path
from typing import Optional


def _parse_bool(value: Optional[str]) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value: Optional[str], default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _cleanup_path(path: Optional[str]):
    if not path:
        return
    try:
        target = Path(path)
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            target.unlink(missing_ok=True)
    except Exception as exc:
        print(f"[清理] 删除资源失败: {path} - {exc}")


def _read_local_pdf_bytes(pdf_path: str) -> Optional[bytes]:
    if not pdf_path:
        return None

    path = Path(pdf_path)
    if not path.exists() or not path.is_file():
        return None
    return path.read_bytes()


def _build_r2_storage():
    from backend.storage.r2_storage import R2Config, R2Storage
    config = R2Config(
        endpoint_url=os.getenv("R2_ENDPOINT_URL", ""),
        access_key_id=os.getenv("R2_ACCESS_KEY_ID", ""),
        secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY", ""),
        bucket=os.getenv("R2_BUCKET", ""),
        enabled=_parse_bool(os.getenv("R2_ENABLED", "false")),
        region=os.getenv("R2_REGION", "auto"),
        key_prefix=os.getenv("R2_KEY_PREFIX", "patent"),
    )
    return R2Storage(config)
