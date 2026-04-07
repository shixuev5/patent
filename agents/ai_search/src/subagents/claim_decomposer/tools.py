"""Claim-decomposer specialist tools."""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List

from langchain.tools import ToolRuntime

from agents.ai_search.src.claim_support import (
    build_claim_packets as build_claim_packets_rows,
    expand_claim_dependency as expand_claim_dependency_rows,
    load_structured_claims_from_markdown,
    load_structured_claims_from_patent_data,
)
from agents.ai_search.src.runtime import extract_json_object
from agents.ai_search.src.state import PHASE_CLAIM_DECOMPOSITION


def build_claim_decomposer_tools(context: Any) -> List[Any]:
    def load_structured_claims(
        markdown_text: str = "",
        source: str = "auto",
        runtime: ToolRuntime | None = None,
    ) -> str:
        """加载源专利的结构化权利要求。"""
        source_text = str(source or "").strip().lower()
        claims: List[Dict[str, Any]] = []
        if str(markdown_text or "").strip():
            claims = load_structured_claims_from_markdown(markdown_text)
            source_text = "markdown"
        else:
            patent_data = context.load_source_patent_data() if source_text in {"", "auto", "analysis"} else {}
            if patent_data:
                claims = load_structured_claims_from_patent_data(patent_data)
                source_text = "source_patent_json"
        context.update_task_phase(PHASE_CLAIM_DECOMPOSITION, runtime=runtime, current_task="claim_decomposition")
        return json.dumps({"source": source_text or "unknown", "claims": claims}, ensure_ascii=False)

    def expand_claim_dependency(
        claims_json: str,
        claim_ids_json: str = "",
        runtime: ToolRuntime | None = None,
    ) -> str:
        """展开从属权利要求依赖链。"""
        try:
            claims_payload = json.loads(claims_json) if claims_json else []
        except Exception:
            claims_payload = []
        try:
            claim_ids_payload = json.loads(claim_ids_json) if claim_ids_json else []
        except Exception:
            claim_ids_payload = []
        if isinstance(claims_payload, dict):
            claims_payload = claims_payload.get("claims") or []
        expanded = expand_claim_dependency_rows(
            claims_payload,
            claim_ids_payload if isinstance(claim_ids_payload, list) else [],
        )
        context.update_task_phase(PHASE_CLAIM_DECOMPOSITION, runtime=runtime, current_task="claim_decomposition")
        return json.dumps({"expanded_claims": expanded}, ensure_ascii=False)

    def build_claim_packets(
        expanded_claims_json: str,
        search_elements_json: str = "",
        claim_ids_json: str = "",
        runtime: ToolRuntime | None = None,
    ) -> str:
        """把 expanded claims 与 search elements 组装成 claim packets。"""
        try:
            expanded_payload = json.loads(expanded_claims_json) if expanded_claims_json else []
        except Exception:
            expanded_payload = []
        if isinstance(expanded_payload, dict):
            expanded_payload = expanded_payload.get("expanded_claims") or expanded_payload.get("claims") or []
        try:
            search_elements_payload = json.loads(search_elements_json) if search_elements_json else {}
        except Exception:
            search_elements_payload = {}
        elements = search_elements_payload.get("search_elements") if isinstance(search_elements_payload, dict) else []
        packets = build_claim_packets_rows(expanded_payload, elements if isinstance(elements, list) else [])
        try:
            claim_ids_payload = json.loads(claim_ids_json) if claim_ids_json else []
        except Exception:
            claim_ids_payload = []
        requested = {
            str(item).strip()
            for item in (claim_ids_payload if isinstance(claim_ids_payload, list) else [])
            if str(item).strip()
        }
        if requested:
            packets = [item for item in packets if str(item.get("claim_id") or "").strip() in requested]
        context.update_task_phase(PHASE_CLAIM_DECOMPOSITION, runtime=runtime, current_task="claim_decomposition")
        return json.dumps({"claim_packets": packets}, ensure_ascii=False)

    def save_claim_decomposition(payload_json: str, runtime: ToolRuntime | None = None) -> str:
        """保存 claim decomposition 结果。"""
        payload = extract_json_object(payload_json)
        context.storage.create_ai_search_message(
            {
                "message_id": uuid.uuid4().hex,
                "task_id": context.task_id,
                "role": "assistant",
                "kind": "claim_decomposition",
                "content": str(payload.get("decomposition_summary") or "").strip() or None,
                "stream_status": "completed",
                "metadata": payload,
            }
        )
        context.update_task_phase(PHASE_CLAIM_DECOMPOSITION, runtime=runtime, current_task="claim_decomposition")
        return "claim decomposition saved"

    return [
        load_structured_claims,
        expand_claim_dependency,
        build_claim_packets,
        save_claim_decomposition,
    ]
