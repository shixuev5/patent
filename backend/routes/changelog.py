"""
更新日志路由：解析项目根目录 CHANGELOG.md 并返回结构化数据。
"""
from pathlib import Path
import re
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query

from config import settings


router = APIRouter()

_RELEASE_PATTERN = re.compile(
    r"^##\s*v?(?P<version>[0-9A-Za-z._-]+)\s*[-—]\s*(?P<date>\d{4}-\d{2}-\d{2})(?:\s*[-—]\s*(?P<title>.+))?$"
)
_ITEM_BRACKET_PATTERN = re.compile(r"^\[(?P<type>[^\]]+)\]\s*(?P<text>.+)$")
_ITEM_COLON_PATTERN = re.compile(r"^(?P<type>[^:：]+)\s*[:：]\s*(?P<text>.+)$")

_TYPE_ALIAS = {
    "feature": "feature",
    "新增": "feature",
    "improvement": "improvement",
    "优化": "improvement",
    "fix": "fix",
    "修复": "fix",
    "breaking": "breaking",
    "变更": "breaking",
}


def _resolve_changelog_path() -> Path:
    candidates = [settings.BASE_DIR / "CHANGELOG.md", settings.BASE_DIR / "CAHNGELOG.md"]
    for path in candidates:
        if path.exists():
            return path
    raise HTTPException(
        status_code=404,
        detail="未找到 CHANGELOG.md（兼容 CAHNGELOG.md）。请在项目根目录创建该文件。",
    )


def _normalize_item_type(raw_type: str) -> str:
    normalized = raw_type.strip().lower()
    return _TYPE_ALIAS.get(normalized) or _TYPE_ALIAS.get(raw_type.strip()) or "improvement"


def _parse_item(line: str) -> Dict[str, str] | None:
    body = line[2:].strip()
    if not body:
        return None

    match = _ITEM_BRACKET_PATTERN.match(body)
    if match:
        return {
            "type": _normalize_item_type(match.group("type")),
            "text": match.group("text").strip(),
        }

    match = _ITEM_COLON_PATTERN.match(body)
    if match:
        return {
            "type": _normalize_item_type(match.group("type")),
            "text": match.group("text").strip(),
        }

    # 未标注类型时默认按优化处理，避免前端渲染中断
    return {"type": "improvement", "text": body}


def _parse_changelog(content: str) -> List[Dict[str, Any]]:
    releases: List[Dict[str, Any]] = []
    current: Dict[str, Any] | None = None

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        header = _RELEASE_PATTERN.match(line)
        if header:
            if current:
                releases.append(current)
            current = {
                "version": header.group("version"),
                "date": header.group("date"),
                "title": (header.group("title") or "").strip() or None,
                "items": [],
            }
            continue

        if current is None:
            continue

        if line.startswith("> ") and not current.get("title"):
            current["title"] = line[2:].strip()
            continue

        if line.startswith("- "):
            item = _parse_item(line)
            if item:
                current["items"].append(item)

    if current:
        releases.append(current)
    return releases


@router.get("/api/changelog")
async def get_changelog(limit: int = Query(default=20, ge=1, le=100)):
    path = _resolve_changelog_path()

    try:
        content = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as error:
        raise HTTPException(status_code=500, detail=f"CHANGELOG 文件编码解析失败：{error}") from error

    releases = _parse_changelog(content)
    return {
        "source": path.name,
        "format": "markdown-v1",
        "total": len(releases),
        "releases": releases[:limit],
    }

