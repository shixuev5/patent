"""AI 检索粗筛、精读与特征对比的上下文构造 helper。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List

from agents.ai_search.src.query_constraints import build_search_constraints
from agents.common.search_clients.factory import SearchClientFactory


DEFAULT_COARSE_CHUNK_SIZE = 20
DEFAULT_SHORTLIST_LIMIT = 20
DEFAULT_SELECTED_LIMIT = 10
DEFAULT_KEY_PASSAGES_LIMIT = 6
DEFAULT_PASSAGE_PREVIEW_CHARS = 400


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


def collect_key_terms(search_elements: Dict[str, Any]) -> List[str]:
    terms: List[str] = []
    for element in search_elements.get("search_elements") or []:
        if not isinstance(element, dict):
            continue
        for key in ("keywords_zh", "keywords_en"):
            for value in element.get(key) or []:
                text = str(value or "").strip()
                if text and text not in terms:
                    terms.append(text)
    return terms[:24]


def fallback_passages(text: str, terms: List[str]) -> List[Dict[str, Any]]:
    if not text:
        return []
    paragraphs = [item.strip() for item in re.split(r"\n{2,}", text) if item.strip()]
    scored: List[tuple[int, str]] = []
    for paragraph in paragraphs:
        lowered = paragraph.lower()
        score = sum(1 for term in terms if term.lower() in lowered)
        if score > 0:
            scored.append((score, paragraph))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "document_id": "",
            "passage": paragraph[:DEFAULT_PASSAGE_PREVIEW_CHARS],
            "reason": "关键词命中",
            "location": f"paragraph_{index + 1}",
        }
        for index, (_, paragraph) in enumerate(scored[:DEFAULT_KEY_PASSAGES_LIMIT])
    ]


def prepare_close_read_workspace(base_dir: str | Path, details: List[Dict[str, Any]]) -> Dict[str, str]:
    workspace = Path(base_dir)
    workspace.mkdir(parents=True, exist_ok=True)
    file_map: Dict[str, str] = {}
    for item in details:
        pn = str(item.get("pn") or "").strip().upper()
        if not pn:
            continue
        path = workspace / f"{pn}.txt"
        path.write_text(detail_to_text(item), encoding="utf-8")
        file_map[pn] = str(path.resolve())
    return file_map


def build_coarse_prompt(search_elements: Dict[str, Any], documents: List[Dict[str, Any]]) -> str:
    constraints = build_search_constraints(search_elements)
    return (
        "根据检索要素对下面候选文献做粗筛，只能基于标题、摘要、分类号和来源批次判断。\n"
        f"检索边界:\n{json.dumps(constraints, ensure_ascii=False)}\n"
        f"检索要素:\n{json.dumps(search_elements, ensure_ascii=False)}\n"
        f"候选文献:\n{json.dumps(documents, ensure_ascii=False)}"
    )


def build_close_reader_prompt(
    search_elements: Dict[str, Any],
    documents: List[Dict[str, Any]],
    file_map: Dict[str, str],
) -> str:
    constraints = build_search_constraints(search_elements)
    payload = []
    for item in documents:
        pn = str(item.get("pn") or "").strip().upper()
        payload.append(
            {
                "document_id": item["document_id"],
                "pn": pn,
                "title": item["title"],
                "abstract": item["abstract"],
                "claims_preview": item.get("claims", "")[:2000],
                "description_preview": item.get("description", "")[:2000],
                "fulltext_path": file_map.get(pn),
            }
        )
    return (
        "请根据检索要素对 shortlisted 文献进行精读。优先在 `fulltext_path` 指向的全文文件中使用 grep/read_file 定位证据，再结合标题、摘要、权利要求和说明书做判断。\n"
        "输出 selected/rejected/key_passages/selection_summary。\n"
        f"检索边界:\n{json.dumps(constraints, ensure_ascii=False)}\n"
        f"检索要素:\n{json.dumps(search_elements, ensure_ascii=False)}\n"
        f"shortlist 文献:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def build_feature_prompt(search_elements: Dict[str, Any], selected_documents: List[Dict[str, Any]]) -> str:
    constraints = build_search_constraints(search_elements)
    payload = []
    for item in selected_documents:
        payload.append(
            {
                "document_id": item["document_id"],
                "pn": item["pn"],
                "title": item["title"],
                "abstract": item["abstract"],
                "key_passages": item.get("key_passages_json") or [],
            }
        )
    return (
        "请基于检索要素和已选对比文件，输出特征对比表。\n"
        f"检索边界:\n{json.dumps(constraints, ensure_ascii=False)}\n"
        f"检索要素:\n{json.dumps(search_elements, ensure_ascii=False)}\n"
        f"已选对比文件:\n{json.dumps(payload, ensure_ascii=False)}"
    )
