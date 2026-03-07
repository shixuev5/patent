from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from agents.common.retrieval import QueryRewriteService, retrieve_segments
from agents.common.utils.llm import get_llm_service


@dataclass
class ContextSelectionResult:
    context: str
    trace: Dict[str, Any]
    fallback_used: bool


class ContextSelector:
    """Session-aware retrieval context selector with rewrite and fallback."""
    RAG_MIN_CHARS = 1800
    REWRITE_MIN_CHARS = 6000
    _GLOBAL_SESSION_SEEDED = set()

    def __init__(
        self,
        patent_data: Dict[str, Any],
        llm_service: Any = None,
        retrieval_session_id: str = "",
    ):
        self.llm_service = llm_service or get_llm_service()
        self.rewriter = QueryRewriteService(llm_service=self.llm_service)
        self.retrieval_session_id = str(retrieval_session_id or "").strip()
        self._session_seeded = False
        if self.retrieval_session_id and self.retrieval_session_id in self._GLOBAL_SESSION_SEEDED:
            self._session_seeded = True
        self._inputs = self._build_retrieval_inputs(patent_data)

    @classmethod
    def clear_session_seeded(cls, session_id: str) -> None:
        key = str(session_id or "").strip()
        if not key:
            return
        cls._GLOBAL_SESSION_SEEDED.discard(key)

    def select_context(
        self,
        task_intent: str,
        raw_query: str,
        fallback_text: str,
        max_chars: int = 3200,
        top_n: int = 24,
        top_k: int = 6,
        mode: Optional[str] = None,
        policy: str = "always",
        rag_threshold_chars: int = RAG_MIN_CHARS,
        rewrite_threshold_chars: int = REWRITE_MIN_CHARS,
    ) -> ContextSelectionResult:
        chosen_mode = str(mode or "").strip().lower() or ("session" if self.retrieval_session_id else "ephemeral")
        if chosen_mode == "session" and not self.retrieval_session_id:
            chosen_mode = "ephemeral"

        raw = " ".join(str(raw_query or "").split()).strip()
        fallback = str(fallback_text or "")
        policy_value = str(policy or "always").strip().lower() or "always"
        source_length = len(fallback.strip())

        use_retrieval = bool(raw)
        if policy_value == "off":
            use_retrieval = False
        elif policy_value == "auto" and source_length <= max(0, int(rag_threshold_chars or 0)):
            use_retrieval = False
        elif policy_value in {"fact", "fact_strict"}:
            use_retrieval = bool(raw)

        if not use_retrieval:
            return ContextSelectionResult(
                context=fallback[:max_chars],
                trace={
                    "mode": chosen_mode,
                    "policy": policy_value,
                    "source_length": source_length,
                    "queries": [],
                    "result_count": 0,
                    "retrieval_skipped": True,
                },
                fallback_used=True,
            )

        use_rewrite = (
            policy_value in {"fact", "fact_strict"}
            or source_length > max(0, int(rewrite_threshold_chars or 0))
        )
        rewrite_result = (
            self.rewriter.rewrite(task_intent=task_intent, raw_query=raw)
            if use_rewrite
            else {"query": raw, "alt_queries": [], "rewritten": False}
        )
        queries = self._build_query_list(raw, rewrite_result)

        all_hits: List[Dict[str, Any]] = []
        query_errors: List[str] = []
        for idx, query in enumerate(queries):
            try:
                if chosen_mode == "session":
                    inputs = self._inputs if not self._session_seeded else []
                    hits = retrieve_segments(
                        query_text=query,
                        inputs=inputs,
                        mode="session",
                        session_id=self.retrieval_session_id,
                        top_n=top_n,
                        top_k=max(top_k, 8),
                        filters={"sources": ["patent"]},
                    )
                    self._session_seeded = True
                    self._GLOBAL_SESSION_SEEDED.add(self.retrieval_session_id)
                else:
                    inputs = self._inputs
                    hits = retrieve_segments(
                        query_text=query,
                        inputs=inputs,
                        mode="ephemeral",
                        top_n=top_n,
                        top_k=max(top_k, 8),
                        filters={"sources": ["patent"]},
                    )

                normalized_hits = self._normalize_hits(hits, query_index=idx)
                all_hits.extend(normalized_hits)
            except Exception as ex:
                query_errors.append(str(ex))

        merged_hits = self._merge_hits(all_hits)
        selected_hits = self._select_hits(merged_hits, top_k=top_k)
        context = self._render_context(selected_hits, max_chars=max_chars)

        fallback_used = False
        if not context:
            context = fallback[:max_chars]
            fallback_used = True

        trace = {
            "mode": chosen_mode,
            "policy": policy_value,
            "source_length": source_length,
            "queries": queries,
            "rewritten": bool(rewrite_result.get("rewritten", False)),
            "rewrite_enabled": use_rewrite,
            "result_count": len(selected_hits),
            "query_errors": query_errors,
            "results": [
                {
                    "doc_id": item.get("doc_id", ""),
                    "section": item.get("section", ""),
                    "score": item.get("score", 0.0),
                    "source_query_index": item.get("query_index", -1),
                }
                for item in selected_hits
            ],
        }
        return ContextSelectionResult(context=context, trace=trace, fallback_used=fallback_used)

    def _build_retrieval_inputs(self, patent_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        biblio = patent_data.get("bibliographic_data", {}) if isinstance(patent_data, dict) else {}
        description = patent_data.get("description", {}) if isinstance(patent_data, dict) else {}
        claims = patent_data.get("claims", []) if isinstance(patent_data, dict) else []

        inputs: List[Dict[str, Any]] = []

        def add_input(doc_id: str, title: str, content: str) -> None:
            text = str(content or "").strip()
            if not text:
                return
            markdown = f"# {title}\n\n{text}"
            inputs.append(
                {
                    "doc_id": doc_id,
                    "source_type": "patent",
                    "title": title,
                    "markdown": markdown,
                }
            )

        add_input("BIBLIO_ABSTRACT", "abstract", str(biblio.get("abstract", "")))
        add_input("DESC_SUMMARY", "summary_of_invention", str(description.get("summary_of_invention", "")))
        add_input("DESC_BRIEF_DRAWINGS", "brief_description_of_drawings", str(description.get("brief_description_of_drawings", "")))
        add_input("DESC_DETAILED", "detailed_description", str(description.get("detailed_description", "")))

        claim_blocks: List[str] = []
        if isinstance(claims, list):
            for idx, claim in enumerate(claims, start=1):
                claim_dict = claim if isinstance(claim, dict) else {}
                claim_id = str(claim_dict.get("claim_id", "")).strip() or str(idx)
                claim_text = str(claim_dict.get("claim_text", "")).strip()
                if not claim_text:
                    continue
                claim_blocks.append(f"## claim_{claim_id}\n{claim_text}")
        if claim_blocks:
            add_input("CLAIMS_ALL", "claims", "\n\n".join(claim_blocks))

        return inputs

    def _build_query_list(self, raw_query: str, rewrite_result: Dict[str, Any]) -> List[str]:
        output: List[str] = []
        for value in [raw_query, rewrite_result.get("query", "")] + list(rewrite_result.get("alt_queries", []) or []):
            text = " ".join(str(value or "").split()).strip()
            if not text or text in output:
                continue
            output.append(text[:360])
        return output

    def _normalize_hits(self, hits: List[Dict[str, Any]], query_index: int) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for item in hits or []:
            hit = item if isinstance(item, dict) else {}
            chunk_id = str(hit.get("chunk_id", "")).strip()
            if not chunk_id:
                continue
            excerpt = str(hit.get("excerpt", "")).strip()
            if not excerpt:
                continue
            heading_path = str(hit.get("heading_path", "")).strip()
            doc_id = str(hit.get("doc_id", "")).strip()
            section = heading_path.split(" / ")[0].strip() if heading_path else doc_id
            normalized.append(
                {
                    "chunk_id": chunk_id,
                    "doc_id": doc_id,
                    "section": section,
                    "excerpt": excerpt[:550],
                    "score": float(hit.get("score", 0.0) or 0.0),
                    "query_index": query_index,
                }
            )
        return normalized

    def _merge_hits(self, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        for hit in hits:
            chunk_id = hit["chunk_id"]
            current = merged.get(chunk_id)
            if current is None or float(hit.get("score", 0.0)) > float(current.get("score", 0.0)):
                merged[chunk_id] = hit
        output = list(merged.values())
        output.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
        return output

    def _select_hits(self, hits: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
        if not hits:
            return []

        selected: List[Dict[str, Any]] = []
        seen_sections = set()
        seen_chunks = set()

        for hit in hits:
            if len(selected) >= top_k:
                break
            chunk_id = hit["chunk_id"]
            section = str(hit.get("section", "")).strip()
            if chunk_id in seen_chunks:
                continue
            if section and section not in seen_sections:
                selected.append(hit)
                seen_chunks.add(chunk_id)
                seen_sections.add(section)

        for hit in hits:
            if len(selected) >= top_k:
                break
            chunk_id = hit["chunk_id"]
            if chunk_id in seen_chunks:
                continue
            selected.append(hit)
            seen_chunks.add(chunk_id)

        return selected

    def _render_context(self, hits: List[Dict[str, Any]], max_chars: int) -> str:
        parts: List[str] = []
        total = 0
        for idx, hit in enumerate(hits, start=1):
            block = (
                f"[证据片段 {idx}] section={hit.get('section', '')} "
                f"doc={hit.get('doc_id', '')} score={float(hit.get('score', 0.0)):.4f}\n"
                f"{hit.get('excerpt', '')}"
            )
            block = block.strip()
            if not block:
                continue
            add_len = len(block) + (2 if parts else 0)
            if total + add_len > max_chars:
                break
            parts.append(block)
            total += add_len
        return "\n\n".join(parts)
