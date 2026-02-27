"""
API 工具函数
"""
import base64
import json
import os
import re
import shutil
from pathlib import Path
from typing import Optional

from config import settings


PATENT_NUMBER_REGEX = re.compile(r"^[A-Z]{2}\d{6,}[A-Z0-9]*$")
APPLICATION_NUMBER_REGEX = re.compile(r"^\d{8,}\.?\d*$")
RAW_TEXT_PATENT_REGEX = re.compile(r"\b[A-Z]{2}\s*\d{6,}\s*[A-Z0-9]?\b")


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


def _b64_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64_decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + padding)


def _normalize_patent_candidate(value: Optional[str]) -> str:
    if not value:
        return ""
    normalized = "".join(ch for ch in str(value).upper().strip() if ch.isalnum() or ch in {".", "-", "_"})
    return normalized


def _score_patent_candidate(candidate: str) -> int:
    if not candidate:
        return 0
    if PATENT_NUMBER_REGEX.match(candidate):
        return 100
    if APPLICATION_NUMBER_REGEX.match(candidate):
        return 70
    if any(ch.isalpha() for ch in candidate) and any(ch.isdigit() for ch in candidate):
        return 40
    if any(ch.isdigit() for ch in candidate):
        return 20
    return 1


def _build_r2_storage():
    from src.storage.r2_storage import R2Config, R2Storage
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


def _extract_patent_number_from_outputs(
    output_pdf: str,
    fallback_pn: Optional[str] = None,
) -> Optional[str]:
    candidates = []
    if fallback_pn:
        candidates.append(_normalize_patent_candidate(fallback_pn))

    pdf_path = Path(output_pdf)
    output_dir = pdf_path.parent

    patent_json_path = output_dir / "patent.json"
    if patent_json_path.exists():
        try:
            patent_data = json.loads(patent_json_path.read_text(encoding="utf-8"))
            biblio = patent_data.get("bibliographic_data", {}) or {}
            candidates.append(_normalize_patent_candidate(biblio.get("publication_number")))
            candidates.append(_normalize_patent_candidate(biblio.get("application_number")))
        except Exception:
            pass

    raw_md_path = output_dir / settings.MINERU_TEMP_FOLDER / "raw.md"
    if raw_md_path.exists():
        try:
            raw_text = raw_md_path.read_text(encoding="utf-8", errors="ignore")
            match = RAW_TEXT_PATENT_REGEX.search(raw_text.upper())
            if match:
                candidates.append(_normalize_patent_candidate(match.group(0)))
        except Exception:
            pass

    best_candidate = ""
    best_score = 0
    for candidate in candidates:
        if not candidate:
            continue
        score = _score_patent_candidate(candidate)
        if score > best_score:
            best_candidate = candidate
            best_score = score

    return best_candidate or None
