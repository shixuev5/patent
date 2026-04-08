"""Non-persistent probe tools for plan drafting."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from agents.common.search_clients.factory import SearchClientFactory


def _json_dumps(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _trim_probe_results(raw_items: Any, *, limit: int) -> List[Dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []
    items: List[Dict[str, Any]] = []
    for item in raw_items[: max(int(limit or 5), 1)]:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "pn": str(item.get("pn") or "").strip().upper(),
                "title": str(item.get("title") or "").strip(),
                "abstract": str(item.get("abstract") or "").strip(),
                "score": item.get("score"),
                "ipc_cpc": item.get("cpc") or item.get("ipc_cpc_json") or [],
            }
        )
    return items


def build_plan_prober_tools(_context: Any) -> List[Any]:
    def probe_search_semantic(query_text: str, limit: int = 5) -> str:
        """执行非持久化语义预检。"""
        client = SearchClientFactory.get_client("zhihuiya")
        result = client.search_semantic(str(query_text or "").strip(), limit=max(int(limit or 5), 1))
        payload = result if isinstance(result, dict) else {}
        items = _trim_probe_results(payload.get("results"), limit=limit)
        return _json_dumps({"query_text": str(query_text or "").strip(), "count": len(items), "items": items})

    def probe_search_boolean(query_text: str, limit: int = 5) -> str:
        """执行非持久化布尔预检。"""
        client = SearchClientFactory.get_client("zhihuiya")
        result = client.search(str(query_text or "").strip(), limit=max(int(limit or 5), 1))
        payload = result if isinstance(result, dict) else {}
        items = _trim_probe_results(payload.get("results"), limit=limit)
        return _json_dumps(
            {
                "query_text": str(query_text or "").strip(),
                "count": int(payload.get("total") or len(items) or 0),
                "items": items,
            }
        )

    def probe_count_boolean(query_text: str) -> str:
        """估算布尔检索结果规模。"""
        client = SearchClientFactory.get_client("zhihuiya")
        count = 0
        if hasattr(client, "_query_patent_info_by_count"):
            info = client._query_patent_info_by_count(str(query_text or "").strip())  # type: ignore[attr-defined]
            if isinstance(info, dict):
                count = int(info.get("TOTAL") or info.get("total") or 0)
        return _json_dumps({"query_text": str(query_text or "").strip(), "count": count})

    return [probe_search_semantic, probe_search_boolean, probe_count_boolean]
