"""专利详情加载与精读工作区辅助工具。"""

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


def load_document_details(document: Dict[str, Any]) -> Dict[str, Any]:
    source_type = str(document.get("source_type") or "").strip().lower()
    pn = str(document.get("pn") or "").strip().upper()
    if source_type in {"openalex", "semanticscholar", "crossref"} or not pn:
        normalized = {
            "document_id": str(document.get("document_id") or "").strip(),
            "source_type": source_type or "document",
            "pn": pn,
            "title": str(document.get("title") or "").strip(),
            "abstract": str(document.get("abstract") or "").strip(),
            "claims": "",
            "description": "",
            "publication_date": str(document.get("publication_date") or "").strip(),
            "application_date": str(document.get("application_date") or "").strip(),
            "venue": str(document.get("venue") or "").strip(),
            "doi": str(document.get("doi") or "").strip(),
            "url": str(document.get("url") or "").strip(),
            "ipc": [],
            "cpc": [],
            "raw": {"detail_source": "abstract_only"},
            "detail_source": str(document.get("detail_source") or "").strip() or "abstract_only",
        }
        normalized["detail_fingerprint"] = detail_fingerprint(normalized)
        return normalized
    client = SearchClientFactory.get_client("zhihuiya")
    detail = client.get_patent_detail(pn)
    detail = detail if isinstance(detail, dict) else {}
    normalized = {
        "document_id": str(document.get("document_id") or "").strip(),
        "source_type": source_type or "patent",
        "pn": pn,
        "title": str(detail.get("title") or detail.get("basic_info", {}).get("title") or "").strip(),
        "abstract": str(detail.get("abstract") or detail.get("basic_info", {}).get("abstract") or "").strip(),
        "claims": str(detail.get("claims") or detail.get("claims_info") or "").strip(),
        "description": str(detail.get("description") or detail.get("description_info") or "").strip(),
        "publication_date": str(detail.get("publication_date") or "").strip(),
        "application_date": str(detail.get("application_date") or "").strip(),
        "ipc": detail.get("ipc") if isinstance(detail.get("ipc"), list) else [],
        "cpc": detail.get("cpc") if isinstance(detail.get("cpc"), list) else [],
        "raw": detail,
        "detail_source": "patent_detail_api",
    }
    normalized["detail_fingerprint"] = detail_fingerprint(normalized)
    return normalized


def prepare_close_read_workspace(base_dir: str | Path, details: List[Dict[str, Any]]) -> Dict[str, str]:
    workspace = Path(base_dir)
    workspace.mkdir(parents=True, exist_ok=True)
    file_map: Dict[str, str] = {}
    manifest: Dict[str, Dict[str, Any]] = {}
    for item in details:
        document_id = str(item.get("document_id") or "").strip()
        if not document_id:
            continue
        path = workspace / f"{document_id}.txt"
        path.write_text(detail_to_text(item), encoding="utf-8")
        file_map[document_id] = str(path.resolve())
        manifest[document_id] = {
            "document_id": document_id,
            "source_type": str(item.get("source_type") or "").strip(),
            "pn": str(item.get("pn") or "").strip(),
            "title": str(item.get("title") or "").strip(),
            "abstract": str(item.get("abstract") or "").strip(),
            "claims": str(item.get("claims") or "").strip(),
            "description": str(item.get("description") or "").strip(),
            "venue": str(item.get("venue") or "").strip(),
            "doi": str(item.get("doi") or "").strip(),
            "url": str(item.get("url") or "").strip(),
            "detail_source": str(item.get("detail_source") or "").strip(),
            "detail_fingerprint": str(item.get("detail_fingerprint") or "").strip(),
            "fulltext_path": file_map[document_id],
        }
    (workspace / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return file_map
