"""Patent detail loading and close-read workspace helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

from agents.common.search_clients.factory import SearchClientFactory


def detail_to_text(detail: Dict[str, Any]) -> str:
    parts = [
        str(detail.get("title") or ""),
        str(detail.get("abstract") or ""),
        str(detail.get("claims") or ""),
        str(detail.get("description") or ""),
    ]
    return "\n\n".join(part.strip() for part in parts if str(part).strip())


def detail_fingerprint(detail: Dict[str, Any]) -> str:
    text = detail_to_text(detail)
    return hashlib.sha1(text.encode("utf-8")).hexdigest() if text else ""


def load_document_details(pn: str) -> Dict[str, Any]:
    client = SearchClientFactory.get_client("zhihuiya")
    detail = client.get_patent_details(pn)
    detail = detail if isinstance(detail, dict) else {}
    normalized = {
        "pn": pn,
        "title": str(detail.get("title") or detail.get("basic_info", {}).get("title") or "").strip(),
        "abstract": str(detail.get("abstract") or detail.get("basic_info", {}).get("abstract") or "").strip(),
        "claims": str(detail.get("claims") or detail.get("claims_info") or "").strip(),
        "description": str(detail.get("description") or detail.get("description_info") or "").strip(),
        "raw": detail,
    }
    normalized["detail_fingerprint"] = detail_fingerprint(normalized)
    return normalized


def prepare_close_read_workspace(base_dir: str | Path, details: List[Dict[str, Any]]) -> Dict[str, str]:
    workspace = Path(base_dir)
    workspace.mkdir(parents=True, exist_ok=True)
    file_map: Dict[str, str] = {}
    manifest: Dict[str, Dict[str, Any]] = {}
    for item in details:
        pn = str(item.get("pn") or "").strip().upper()
        if not pn:
            continue
        path = workspace / f"{pn}.txt"
        path.write_text(detail_to_text(item), encoding="utf-8")
        file_map[pn] = str(path.resolve())
        manifest[pn] = {
            "pn": pn,
            "title": str(item.get("title") or "").strip(),
            "abstract": str(item.get("abstract") or "").strip(),
            "claims": str(item.get("claims") or "").strip(),
            "description": str(item.get("description") or "").strip(),
            "detail_fingerprint": str(item.get("detail_fingerprint") or "").strip(),
            "fulltext_path": file_map[pn],
        }
    (workspace / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return file_map
