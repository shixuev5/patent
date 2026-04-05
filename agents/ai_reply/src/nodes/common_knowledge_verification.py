"""
公知常识核查节点
基于外部检索（优先）与模型知识（次级）对逻辑争议进行核查
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Set

from loguru import logger

from agents.common.retrieval import LocalEvidenceRetriever
from agents.common.utils.concurrency import submit_with_current_context
from agents.common.utils.llm import get_llm_service
from agents.ai_reply.src.external_evidence import ExternalEvidenceAggregator
from agents.ai_reply.src.retrieval_followup import (
    build_compact_cards,
    has_new_evidence_cards,
    merge_local_retrieval_trace,
    merge_local_retrieval_traces,
    should_run_followup_by_assessment,
)
from agents.ai_reply.src.retrieval_utils import (
    ENGINE_HINTS,
    QuerySpec,
    build_trace_retrieval,
    extract_must_keep_phrases,
    flatten_query_texts,
    make_query_spec,
    normalize_query_specs,
    plan_engine_queries,
)
from agents.ai_reply.src.state import EvidenceAssessment
from agents.ai_reply.src.utils import get_node_cache
from config import settings


class CommonKnowledgeVerificationNode:
    """公知常识核查节点（输出结构与 EvidenceVerificationNode 对齐）"""

    def __init__(self, config=None):
        self.config = config
        self.llm_service = get_llm_service()
        self.external_evidence_aggregator = ExternalEvidenceAggregator()

    def __call__(self, state):
        logger.info("开始公知常识核查")
        updates = {}

        try:
            cache = get_node_cache(self.config, "common_knowledge_verification")
            assessments = cache.run_step(
                "verify_common_knowledge_v9",
                self._verify_common_knowledge,
                self._state_get(state, "disputes", []),
                self._state_get(state, "prepared_materials", {}),
                self._state_get(state, "claims_old_structured", []),
            )

            if not assessments:
                logger.info("没有需要公知常识核查的争议项")
                return updates

            updates["evidence_assessments"] = [
                item if isinstance(item, EvidenceAssessment) else EvidenceAssessment(**item)
                for item in assessments
            ]
            logger.info(f"完成 {len(assessments)} 个逻辑争议项的公知常识核查")

        except Exception as e:
            logger.error(f"公知常识核查节点执行失败: {e}")
            updates["errors"] = [{
                "node_name": "common_knowledge_verification",
                "error_message": str(e),
                "error_type": "common_knowledge_verification_error",
            }]

        return updates

    def _verify_common_knowledge(
        self,
        disputes: List[Any],
        prepared_materials: Any,
        claims_structured: List[Any],
    ) -> List[Dict[str, Any]]:
        common_knowledge_disputes = self._get_common_knowledge_disputes(disputes)
        if not common_knowledge_disputes:
            return []

        prepared = self._to_dict(prepared_materials)
        claims = self._normalize_claims(claims_structured)
        priority_date = self._extract_priority_date(prepared)
        local_retriever = self._build_local_retriever(prepared)
        local_doc_ids = self._extract_comparison_doc_ids(prepared)

        max_workers = max(
            1,
            min(
                settings.OAR_MAX_CONCURRENCY,
                len(common_knowledge_disputes),
            ),
        )
        if len(common_knowledge_disputes) == 1:
            return [
                self._evaluate_common_knowledge_dispute(
                    dispute=common_knowledge_disputes[0],
                    claims=claims,
                    priority_date=priority_date,
                    local_retriever=local_retriever,
                    local_doc_ids=local_doc_ids,
                )
            ]

        logger.info(
            f"公知常识核查并行执行: disputes={len(common_knowledge_disputes)} workers={max_workers}"
        )
        ordered_results: List[Dict[str, Any] | None] = [None] * len(common_knowledge_disputes)
        remaining_disputes = common_knowledge_disputes
        if len(common_knowledge_disputes) > 2 and max_workers > 1:
            ordered_results[0] = self._evaluate_common_knowledge_dispute(
                dispute=common_knowledge_disputes[0],
                claims=claims,
                priority_date=priority_date,
                local_retriever=local_retriever,
                local_doc_ids=local_doc_ids,
            )
            remaining_disputes = common_knowledge_disputes[1:]

        if not remaining_disputes:
            return [item for item in ordered_results if item]

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                submit_with_current_context(
                    executor,
                    self._evaluate_common_knowledge_dispute,
                    dispute=dispute,
                    claims=claims,
                    priority_date=priority_date,
                    local_retriever=local_retriever,
                    local_doc_ids=local_doc_ids,
                ): index
                for index, dispute in enumerate(
                    remaining_disputes,
                    start=1 if ordered_results[0] else 0,
                )
            }
            for future in as_completed(futures):
                index = futures[future]
                ordered_results[index] = future.result()
        return [item for item in ordered_results if item]

    def _evaluate_common_knowledge_dispute(
        self,
        dispute: Dict[str, Any],
        claims: List[Dict[str, Any]],
        priority_date: Optional[str],
        local_retriever: LocalEvidenceRetriever | None,
        local_doc_ids: List[str],
    ) -> Dict[str, Any]:
        claim_text = self._get_claim_text(dispute, claims)
        queries_by_engine = self._build_engine_queries(dispute, claim_text, priority_date)
        external_evidence, retrieval_engines, retrieval_meta = self.external_evidence_aggregator.search_evidence(
            queries=queries_by_engine,
            priority_date=priority_date,
            limit=8,
        )
        if not external_evidence:
            logger.warning("外部证据为空，将仅基于模型知识进行低置信度判断")

        flat_queries = flatten_query_texts(queries_by_engine)
        local_candidates, local_trace = self._search_local_candidates(
            flat_queries=flat_queries,
            local_doc_ids=local_doc_ids,
            local_retriever=local_retriever,
        )
        external_candidates = self._to_external_candidates(external_evidence)
        merged_candidates = self._rerank_candidates(
            candidates=local_candidates + external_candidates,
            flat_queries=flat_queries,
            top_k=settings.LOCAL_RETRIEVAL_RERANK_K,
        )
        card_bundle = self._build_compact_cards(
            candidates=merged_candidates,
            local_retriever=local_retriever,
            context_k=settings.LOCAL_RETRIEVAL_CONTEXT_K,
            max_context_chars=settings.LOCAL_RETRIEVAL_MAX_CONTEXT_CHARS,
            max_quote_chars=settings.LOCAL_RETRIEVAL_MAX_QUOTE_CHARS,
        )

        assessment = self._verify_single_dispute(
            dispute=dispute,
            claim_text=claim_text,
            queries_by_engine=queries_by_engine,
            priority_date=priority_date,
            evidence_cards=card_bundle.get("cards", []),
            retrieval_engines=retrieval_engines,
            retrieval_meta=retrieval_meta,
            local_retrieval_trace=self._merge_local_retrieval_trace(
                local_trace=local_trace,
                card_trace=card_bundle.get("trace", {}),
                flat_queries=flat_queries,
                local_doc_ids=local_doc_ids,
            ),
        )

        first_assessment = self._to_dict(assessment.get("assessment", {}))
        used_doc_ids = self._extract_used_doc_ids(assessment)
        followup_retrieval_trace: Dict[str, Any] = {}
        followup_local_trace: Dict[str, Any] = {}
        if should_run_followup_by_assessment(
            first_assessment,
            used_doc_ids=used_doc_ids,
            evidence_cards=card_bundle.get("cards", []),
        ):
            followup_queries = self._build_followup_engine_queries(
                dispute=dispute,
                claim_text=claim_text,
                priority_date=priority_date,
                primary_queries=queries_by_engine,
                first_assessment=first_assessment,
            )
            primary_query_keys = {
                (
                    " ".join(str((item or {}).get("text", "")).split()),
                    str((item or {}).get("mode", "")).strip().lower(),
                    str((item or {}).get("intent", "")).strip().lower(),
                )
                for engine_queries in queries_by_engine.values()
                for item in engine_queries
            }
            if any(
                (
                    " ".join(str((query or {}).get("text", "")).split()),
                    str((query or {}).get("mode", "")).strip().lower(),
                    str((query or {}).get("intent", "")).strip().lower(),
                ) not in primary_query_keys
                and " ".join(str((query or {}).get("text", "")).split())
                for engine_queries in followup_queries.values()
                for query in engine_queries
            ):
                followup_external_evidence, followup_engines, followup_meta = self.external_evidence_aggregator.search_evidence(
                    queries=followup_queries,
                    priority_date=priority_date,
                    limit=8,
                )
                followup_flat_queries = flatten_query_texts(followup_queries)
                followup_local_candidates, followup_local_trace = self._search_local_candidates(
                    flat_queries=followup_flat_queries,
                    local_doc_ids=local_doc_ids,
                    local_retriever=local_retriever,
                )
                followup_external_candidates = self._to_external_candidates(followup_external_evidence)
                merged_queries = {
                    engine: normalize_query_specs(
                        list(queries_by_engine.get(engine, [])) + list(followup_queries.get(engine, [])),
                        engine=engine,
                        limit=4,
                    )
                    for engine in {"openalex", "zhihuiya", "tavily"}
                }
                merged_candidates = self._rerank_candidates(
                    candidates=local_candidates + external_candidates + followup_local_candidates + followup_external_candidates,
                    flat_queries=flatten_query_texts(merged_queries),
                    top_k=max(settings.LOCAL_RETRIEVAL_RERANK_K, 10),
                )
                expanded_bundle = self._build_compact_cards(
                    candidates=merged_candidates,
                    local_retriever=local_retriever,
                    context_k=max(settings.LOCAL_RETRIEVAL_CONTEXT_K, 10),
                    max_context_chars=max(settings.LOCAL_RETRIEVAL_MAX_CONTEXT_CHARS, 3200),
                    max_quote_chars=settings.LOCAL_RETRIEVAL_MAX_QUOTE_CHARS,
                )
                if has_new_evidence_cards(card_bundle.get("cards", []), expanded_bundle.get("cards", [])):
                    assessment = self._verify_single_dispute(
                        dispute=dispute,
                        claim_text=claim_text,
                        queries_by_engine=merged_queries,
                        priority_date=priority_date,
                        evidence_cards=expanded_bundle.get("cards", []),
                        retrieval_engines=followup_engines or retrieval_engines,
                        retrieval_meta=followup_meta or retrieval_meta,
                        local_retrieval_trace=merge_local_retrieval_trace(
                            local_trace=merge_local_retrieval_traces(local_trace, followup_local_trace),
                            card_trace=expanded_bundle.get("trace", {}),
                            queries=flatten_query_texts(merged_queries),
                            doc_filters=local_doc_ids,
                        ),
                    )
                    queries_by_engine = merged_queries
                    retrieval_engines = followup_engines or retrieval_engines
                    retrieval_meta = followup_meta or retrieval_meta
                    followup_retrieval_trace = build_trace_retrieval(
                        followup_queries,
                        followup_engines,
                        followup_meta,
                    )

        if followup_retrieval_trace:
            assessment.setdefault("trace", {})
            assessment["trace"]["followup_retrieval"] = followup_retrieval_trace
            assessment["trace"]["followup_local_retrieval"] = followup_local_trace
        return assessment

    def _build_followup_engine_queries(
        self,
        dispute: Dict[str, Any],
        claim_text: str,
        priority_date: Optional[str],
        primary_queries: Dict[str, List[QuerySpec]],
        first_assessment: Dict[str, Any],
    ) -> Dict[str, List[QuerySpec]]:
        feature_text = str(dispute.get("feature_text", "")).strip()
        reasoning = str(self._to_dict(first_assessment).get("reasoning", "")).strip()
        extra_fallback_queries = {
                "openalex": normalize_query_specs(
                    [
                        make_query_spec(f"\"{feature_text}\" AND textbook AND handbook", "boolean", "anchor"),
                        make_query_spec(f"\"{feature_text}\" AND standard practice review", "boolean", "expansion"),
                    ],
                    engine="openalex",
                    limit=2,
                ),
                "zhihuiya": normalize_query_specs(
                    [
                        make_query_spec(f"\"{feature_text}\" AND 教材 AND 手册", "lexical", "core_patent"),
                        make_query_spec(f"{feature_text} 常规技术手段 申请日前 公知常识", "semantic", "expansion"),
                    ],
                    engine="zhihuiya",
                    limit=2,
                ),
                "tavily": normalize_query_specs(
                    [
                        make_query_spec(f"{feature_text} 教材 手册 标准 综述 PDF 高校 研究院", "web", "reference"),
                        make_query_spec(f"{feature_text} 技术手段 常见做法 技术公开 论文", "web", "technical"),
                    ],
                    engine="tavily",
                    limit=2,
                ),
        }
        fallback_queries = {
            engine: normalize_query_specs(
                list(primary_queries.get(engine, [])) + list(extra_fallback_queries.get(engine, [])),
                engine=engine,
                limit=4,
            )
            for engine in {"openalex", "zhihuiya", "tavily"}
        }
        user_context = {
            "priority_date": priority_date or "",
            "retrieval_goal": "followup_common_knowledge",
            "engine_hints": dict(ENGINE_HINTS),
            "must_keep_phrases": extract_must_keep_phrases(feature_text, claim_text, primary_queries),
            "feature_text": feature_text,
            "claim_text": claim_text[:240],
            "first_assessment": {
                "verdict": str(self._to_dict(first_assessment).get("verdict", "")).strip(),
                "confidence": self._to_dict(first_assessment).get("confidence", 0.0),
                "reasoning": reasoning[:600],
            },
            "primary_queries": primary_queries,
        }
        return plan_engine_queries(
            llm_service=self.llm_service,
            user_context=user_context,
            fallback_queries=fallback_queries,
            scenario="公知常识核查二次检索",
            per_engine_limit=2,
        )

    def _extract_used_doc_ids(self, assessment: Dict[str, Any]) -> List[str]:
        used_doc_ids: List[str] = []
        for evidence in self._to_dict(assessment).get("evidence", []) or []:
            doc_id = str(self._to_dict(evidence).get("doc_id", "")).strip()
            if doc_id and doc_id not in used_doc_ids:
                used_doc_ids.append(doc_id)
        return used_doc_ids

    def _get_common_knowledge_disputes(self, disputes: List[Any]) -> List[Dict[str, Any]]:
        common_knowledge_disputes: List[Dict[str, Any]] = []
        for item in disputes or []:
            dispute = self._to_dict(item)
            examiner_opinion = self._to_dict(dispute.get("examiner_opinion", {}))
            dispute_type = str(examiner_opinion.get("type", "")).strip()
            if dispute_type in {"common_knowledge_based", "mixed_basis"}:
                common_knowledge_disputes.append(dispute)
        return common_knowledge_disputes

    def _normalize_claims(self, claims_structured: List[Any]) -> List[Dict[str, Any]]:
        return [self._to_dict(claim) for claim in (claims_structured or []) if self._to_dict(claim)]

    def _get_claim_text(self, dispute: Dict[str, Any], claims: List[Dict[str, Any]]) -> str:
        texts: List[str] = []
        for claim_id in self._normalize_claim_ids(dispute.get("claim_ids", [])):
            try:
                index = int(claim_id) - 1
            except Exception:
                continue
            if 0 <= index < len(claims):
                text = str(claims[index].get("claim_text", "")).strip()
                if text:
                    texts.append(f"权利要求{claim_id}: {text}")
        return "\n".join(texts)

    def _extract_priority_date(self, prepared_materials: Dict[str, Any]) -> Optional[str]:
        original_patent = self._to_dict(prepared_materials.get("original_patent", {}))
        patent_data = self._to_dict(original_patent.get("data", {}))
        bibliographic_data = self._to_dict(patent_data.get("bibliographic_data", {}))

        candidates = [
            patent_data.get("priority_date"),
            patent_data.get("application_date"),
            bibliographic_data.get("priority_date"),
            bibliographic_data.get("application_date"),
        ]
        for date_str in candidates:
            normalized = self._normalize_date(date_str)
            if normalized:
                return normalized
        return None

    def _normalize_date(self, value: Any) -> Optional[str]:
        text = str(value or "").strip()
        if not text:
            return None

        patterns = [
            r"(\d{4})(\d{2})(\d{2})",
            r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})",
            r"(\d{4})年(\d{1,2})月(\d{1,2})日?",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            year = match.group(1).zfill(4)
            month = match.group(2).zfill(2)
            day = match.group(3).zfill(2)
            if 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
                return f"{year}-{month}-{day}"
        return None

    def _build_engine_queries(
        self,
        dispute: Dict[str, Any],
        claim_text: str,
        priority_date: Optional[str],
    ) -> Dict[str, List[QuerySpec]]:
        feature_text = str(dispute.get("feature_text", "")).strip()
        examiner_opinion = self._to_dict(dispute.get("examiner_opinion", {}))
        applicant_opinion = self._to_dict(dispute.get("applicant_opinion", {}))
        fallback_queries = {
            "openalex": normalize_query_specs([
                make_query_spec(f"\"{feature_text}\" AND review AND tutorial", "boolean", "anchor"),
                make_query_spec(f"\"{feature_text}\" AND standard practice", "boolean", "expansion"),
            ], engine="openalex", limit=2),
            "zhihuiya": normalize_query_specs([
                make_query_spec(f"\"{feature_text}\" AND 本领域公知常识", "lexical", "core_patent"),
                make_query_spec(f"{feature_text} 技术手段 常见实现 {applicant_opinion.get('core_conflict', '')}", "semantic", "expansion"),
            ], engine="zhihuiya", limit=2),
            "tavily": normalize_query_specs([
                make_query_spec(f"{feature_text} 教材 手册 标准 综述 PDF 高校 研究院", "web", "reference"),
                make_query_spec(f"{feature_text} 技术原理 技术公开 论文 产品文档", "web", "technical"),
            ], engine="tavily", limit=2),
        }
        user_context = {
            "priority_date": priority_date or "",
            "retrieval_goal": "common_knowledge",
            "engine_hints": dict(ENGINE_HINTS),
            "must_keep_phrases": extract_must_keep_phrases(feature_text, claim_text),
            "feature_text": feature_text,
            "claim_text": claim_text[:240],
            "examiner_reasoning": examiner_opinion.get("reasoning", ""),
            "applicant_core_conflict": applicant_opinion.get("core_conflict", ""),
        }
        return plan_engine_queries(
            llm_service=self.llm_service,
            user_context=user_context,
            fallback_queries=fallback_queries,
            scenario="公知常识核查",
            per_engine_limit=2,
        )

    def _build_local_retriever(self, prepared_materials: Dict[str, Any]) -> LocalEvidenceRetriever | None:
        local_meta = self._to_dict(prepared_materials.get("local_retrieval", {}))
        if not local_meta or not bool(local_meta.get("enabled", False)):
            return None
        index_path = str(local_meta.get("index_path", "")).strip()
        if not index_path:
            return None
        return LocalEvidenceRetriever(
            db_path=index_path,
            chunk_chars=int(local_meta.get("chunk_chars") or settings.LOCAL_RETRIEVAL_CHUNK_CHARS),
            chunk_overlap=int(local_meta.get("chunk_overlap") or settings.LOCAL_RETRIEVAL_CHUNK_OVERLAP),
        )

    def _extract_comparison_doc_ids(self, prepared_materials: Dict[str, Any]) -> List[str]:
        doc_ids: List[str] = []
        for item in prepared_materials.get("comparison_documents", []) or []:
            doc = self._to_dict(item)
            doc_id = str(doc.get("document_id", "")).strip()
            if doc_id and doc_id not in doc_ids:
                doc_ids.append(doc_id)
        return doc_ids

    def _search_local_candidates(
        self,
        flat_queries: List[str],
        local_doc_ids: List[str],
        local_retriever: LocalEvidenceRetriever | None,
    ) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
        if not local_retriever:
            return [], {
                "enabled": False,
                "fallback": "no_local_retriever",
                "queries": flat_queries,
                "queries_by_language": {},
                "doc_filters": local_doc_ids,
                "hit_chunks": [],
                "lexical_hits": [],
                "dense_hits": [],
                "fusion_hits": [],
            }

        candidates: List[Dict[str, Any]] = []
        for query in flat_queries[:4]:
            candidates.extend(
                local_retriever.search(
                    query=query,
                    intent="common_knowledge",
                    doc_filters=local_doc_ids,
                    top_k=settings.LOCAL_RETRIEVAL_CANDIDATE_K,
                )
            )

        deduped: Dict[str, Dict[str, Any]] = {}
        for item in candidates:
            chunk_id = str(item.get("chunk_id", "")).strip()
            if not chunk_id:
                continue
            current = deduped.get(chunk_id)
            if not current or float(item.get("relevance_score", 0.0)) > float(current.get("relevance_score", 0.0)):
                deduped[chunk_id] = item
        sorted_items = sorted(
            deduped.values(),
            key=lambda x: float(x.get("relevance_score", 0.0)),
            reverse=True,
        )[: settings.LOCAL_RETRIEVAL_CANDIDATE_K]
        return sorted_items, {
            "enabled": True,
            "fallback": "",
            "queries": flat_queries,
            "queries_by_language": {},
            "doc_filters": local_doc_ids,
            "hit_chunks": [item.get("chunk_id") for item in sorted_items if item.get("chunk_id")],
            "lexical_hits": [
                item.get("chunk_id") for item in sorted_items
                if item.get("chunk_id") and "lexical" in (item.get("retrieval_channels") or [])
            ],
            "dense_hits": [
                item.get("chunk_id") for item in sorted_items
                if item.get("chunk_id") and "dense" in (item.get("retrieval_channels") or [])
            ],
            "fusion_hits": [item.get("chunk_id") for item in sorted_items if item.get("chunk_id")],
        }

    def _to_external_candidates(self, external_evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        for item in external_evidence or []:
            evidence = self._to_dict(item)
            doc_id = str(evidence.get("doc_id", "")).strip()
            snippet = str(evidence.get("snippet", "")).strip()
            if not doc_id or not snippet:
                continue
            candidates.append(
                {
                    "candidate_id": doc_id,
                    "chunk_id": "",
                    "doc_id": doc_id,
                    "source_type": str(evidence.get("source_type", "")).strip() or "external_document",
                    "section_type": "external",
                    "location": str(evidence.get("published", "")).strip() or "external",
                    "text": snippet,
                    "source_url": str(evidence.get("url", "")).strip(),
                    "source_title": str(evidence.get("title", "")).strip(),
                    "relevance_score": float(evidence.get("relevance_score", 0.0) or 0.0),
                    "match_terms": [],
                }
            )
        return candidates

    def _rerank_candidates(
        self,
        candidates: List[Dict[str, Any]],
        flat_queries: List[str],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        _ = flat_queries
        scored: List[Dict[str, Any]] = []
        for item in candidates or []:
            row = self._to_dict(item)
            text = str(row.get("text", "")).strip()
            if not text:
                continue
            source_type = str(row.get("source_type", "")).strip()
            if source_type == "comparison_document":
                base_score = float(row.get("relevance_score", row.get("fusion_score", 0.0)) or 0.0)
            else:
                base_score = float(row.get("relevance_score", 0.0) or 0.0)
            row["relevance_score"] = round(max(0.0, min(1.0, base_score)), 6)
            scored.append(row)

        deduped: Dict[str, Dict[str, Any]] = {}
        for item in sorted(scored, key=lambda x: float(x.get("relevance_score", 0.0)), reverse=True):
            candidate_id = str(item.get("candidate_id", "")).strip() or str(item.get("chunk_id", "")).strip() or str(item.get("doc_id", "")).strip()
            if not candidate_id or candidate_id in deduped:
                continue
            deduped[candidate_id] = item
            if len(deduped) >= max(1, int(top_k)):
                break
        return list(deduped.values())

    def _build_compact_cards(
        self,
        candidates: List[Dict[str, Any]],
        local_retriever: LocalEvidenceRetriever | None,
        context_k: int,
        max_context_chars: int,
        max_quote_chars: int,
    ) -> Dict[str, Any]:
        return build_compact_cards(
            candidates,
            local_retriever,
            context_k=context_k,
            max_context_chars=max_context_chars,
            max_quote_chars=max_quote_chars,
            read_window=1,
        )

    def _merge_local_retrieval_trace(
        self,
        local_trace: Dict[str, Any],
        card_trace: Dict[str, Any],
        flat_queries: List[str],
        local_doc_ids: List[str],
    ) -> Dict[str, Any]:
        return merge_local_retrieval_trace(
            local_trace=local_trace,
            card_trace=card_trace,
            queries=flat_queries,
            doc_filters=local_doc_ids,
        )

    def _build_system_prompt(self) -> str:
        return """你是资深的专利审查与复审专家AI，当前任务是基于外部证据或模型知识，核查“审查员将某技术特征认定为公知常识/常规技术手段”的逻辑争议，并判断申请人的反驳是否成立。

【公知常识判定标准】
- 只有记载在教科书、技术词典、技术手册中的知识，或本领域中广泛使用的常规技术手段，才能被轻易认定为公知常识。
- 仅仅在一两篇普通专利文献中公开的技术，通常不足以直接证明其为“公知常识”（除非文献明确记载该技术为本领域公知）。

【判定优先级】
1. 外部证据优先：若检索到的外部证据（EXT*）明确支持或否定该特征为公知常识，必须以此为主要依据。
2. 模型知识次之：仅当外部证据缺失或极度不足时，允许调用你的内部模型专业知识进行判定，但需适当降低 confidence。
3. 结论存疑：若证据不足且模型知识也无法形成明确、稳定的结论，必须判定为 INCONCLUSIVE。

【结论映射规则 (verdict)】
- EXAMINER_CORRECT：审查员正确。证据/知识表明该技术特征在申请日前确实属于本领域公知常识或常规设计。
- APPLICANT_CORRECT：申请人反驳成立。证据/知识表明该技术特征并非公知常识，具有特定技术壁垒或属于非显而易见的改进。
- INCONCLUSIVE：现有信息无法得出可靠结论。

【字段输出与审查员说理约束 (极其重要)】
由于系统管线要求，你需要遵守以下严格的条件约束：
- reasoning：在此字段中进行详尽的逻辑推演（分析特征 -> 评估证据 -> 得出结论）。
- confidence：0.0 到 1.0 之间的浮点数。外部证据确凿时 >0.8；仅靠模型知识时 0.5~0.7；存疑时 <0.5。
- examiner_rejection_rationale：
  *[强制规则 1] 仅当 verdict 为 "APPLICANT_CORRECT" 时，本字段【必须】填写内容。其业务逻辑是：虽然申请人成功反驳了该特征是“公知常识”，但系统仍需要一段与当前证据一致的“替代性驳回逻辑要点”（例如：指出该特征虽非公知常识，但结合具体应用场景仍属于容易想到的常规变形）。
  *[强制规则 2] 当 verdict 为 "EXAMINER_CORRECT" 或 "INCONCLUSIVE" 时，本字段【必须】为空字符串 ""。
  *[表达约束] 只写逻辑骨架，不要写成正式审查意见通知书正文，不要使用策略性元话术。

【输出格式要求】
1. 必须且只能输出合法的 JSON 对象，不要包含 ```json 等任何 Markdown 标记，不要输出额外说明文本。
2. JSON 结构必须严格如下：
{
  "assessment": {
    "verdict": "APPLICANT_CORRECT | EXAMINER_CORRECT | INCONCLUSIVE",
    "reasoning": "详细的判定理由，包含对技术特征、证据内容以及公知常识属性的分析。",
    "confidence": 0.85,
    "examiner_rejection_rationale": "遵守上述强制规则。若需填写，示例：现有证据虽不足以将该特征直接认定为公知常识，但结合D1已公开的基础结构与本领域常规设计推演，相关权利要求仍可能不具备创造性。"
  },
  "evidence":[
    {
      "doc_id": "EXT1",
      "quote": "原文核心证据片段摘录",
      "location": "如：文献摘要/第X段/摘要",
      "analysis": "该证据如何支持或反驳公知常识的认定",
      "source_url": "https://...",
      "source_title": "文献标题",
      "source_type": "openalex 或 zhihuiya 或 tavily 或 model_knowledge"
    }
  ]
}

【证据引用说明】
- 优先引用提供的外部证据（doc_id 必须对应提供的 EXT 编号）。
- 若完全没有外部证据，允许生成一条基于模型自身知识的证据，此时 doc_id 必须固定为 "MODEL"，source_type 固定为 "model_knowledge"。"""

    def _build_prefix_messages(
        self,
        evidence_cards: List[Dict[str, Any]],
        priority_date: Optional[str],
    ) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": self._build_system_prompt()},
        ]

        if priority_date:
            messages.append({
                "role": "user",
                "content": f"时间边界：请以 {priority_date}（含）之前可公开获得的技术知识为准。",
            })

        if not evidence_cards:
            messages.append({
                "role": "user",
                "content": "当前未检索到有效证据卡。可使用模型通用知识进行低置信度分析；若仍不确定输出 INCONCLUSIVE。",
            })
            return messages

        for item in evidence_cards:
            item_dict = self._to_dict(item)
            messages.append({
                "role": "user",
                "content": (
                    f"证据卡 {item_dict.get('doc_id', '')} ({item_dict.get('source_type', 'evidence')})\n"
                    f"位置: {item_dict.get('location', '')}\n"
                    f"标题: {item_dict.get('source_title', '')}\n"
                    f"链接: {item_dict.get('source_url', '')}\n"
                    f"引用: {item_dict.get('quote', '')}\n"
                    f"说明: {item_dict.get('analysis', '')}"
                ),
            })

        return messages

    def _verify_single_dispute(
        self,
        dispute: Dict[str, Any],
        claim_text: str,
        queries_by_engine: Dict[str, List[QuerySpec]],
        priority_date: Optional[str],
        evidence_cards: List[Dict[str, Any]],
        retrieval_engines: List[str],
        retrieval_meta: Dict[str, Any],
        local_retrieval_trace: Dict[str, Any],
    ) -> Dict[str, Any]:
        examiner_opinion = self._to_dict(dispute.get("examiner_opinion", {}))
        applicant_opinion = self._to_dict(dispute.get("applicant_opinion", {}))

        prefix_messages = self._build_prefix_messages(evidence_cards, priority_date)
        flat_queries = flatten_query_texts(queries_by_engine)
        dispute_prompt = f"""请核查以下逻辑争议项：
dispute_id: {dispute.get("dispute_id", "")}
claim_ids: {json.dumps(self._normalize_claim_ids(dispute.get("claim_ids", [])), ensure_ascii=False)}
claim_text: {claim_text}
feature_text: {dispute.get("feature_text", "")}
examiner_opinion: {json.dumps(examiner_opinion, ensure_ascii=False)}
applicant_opinion: {json.dumps(applicant_opinion, ensure_ascii=False)}
retrieval_queries: {json.dumps(flat_queries, ensure_ascii=False)}
retrieval_queries_by_engine: {json.dumps(queries_by_engine, ensure_ascii=False)}
"""
        messages = prefix_messages + [{"role": "user", "content": dispute_prompt}]

        external_doc_ids = {str(item.get("doc_id", "")).strip() for item in evidence_cards if item.get("doc_id")}
        allowed_doc_ids = set(external_doc_ids)
        allowed_doc_ids.add("MODEL")
        external_doc_map = {
            str(item.get("doc_id", "")).strip(): self._to_dict(item)
            for item in evidence_cards
            if item.get("doc_id")
        }

        response = self.llm_service.invoke_text_json(
            messages=messages,
            task_kind="oar_common_knowledge_verification",
            temperature=0.05,
        )
        parsed = self._normalize_llm_output(response, allowed_doc_ids, external_doc_map)

        claim_ids = self._normalize_claim_ids(dispute.get("claim_ids", []))
        feature_text = str(dispute.get("feature_text", "")).strip()
        claim_key = "_".join(claim_ids[:4]) if claim_ids else "UNKNOWN"
        used_doc_ids: List[str] = []
        for evidence in parsed.get("evidence", []):
            doc_id = str(evidence.get("doc_id", "")).strip()
            if doc_id and doc_id not in used_doc_ids:
                used_doc_ids.append(doc_id)

        return {
            "dispute_id": str(dispute.get("dispute_id", f"DSP_{claim_key}_{feature_text[:8]}")),
            "origin": str(dispute.get("origin", "response_dispute")).strip() or "response_dispute",
            "source_argument_id": str(dispute.get("source_argument_id", "")).strip(),
            "source_feature_id": str(dispute.get("source_feature_id", "")).strip(),
            "claim_ids": claim_ids,
            "claim_text": claim_text,
            "feature_text": feature_text,
            "examiner_opinion": examiner_opinion,
            "applicant_opinion": applicant_opinion,
            "assessment": parsed["assessment"],
            "evidence": parsed["evidence"],
            "trace": {
                "used_doc_ids": used_doc_ids,
                "missing_doc_ids": [],
                "local_retrieval": local_retrieval_trace,
                "retrieval": build_trace_retrieval(queries_by_engine, retrieval_engines, retrieval_meta),
            },
        }

    def _normalize_llm_output(
        self,
        response: Dict[str, Any],
        allowed_doc_ids: Set[str],
        external_doc_map: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        output = self._to_dict(response)
        assessment = self._to_dict(output.get("assessment", {}))

        verdict = str(assessment.get("verdict", "")).strip()
        if verdict not in {"APPLICANT_CORRECT", "EXAMINER_CORRECT", "INCONCLUSIVE"}:
            raise ValueError(f"common_knowledge_verification 输出非法 verdict: {verdict}")

        confidence = assessment.get("confidence", 0.0)
        try:
            confidence = float(confidence)
        except Exception as e:
            raise ValueError(f"common_knowledge_verification 输出非法 confidence: {confidence}") from e
        if confidence < 0.0 or confidence > 1.0:
            raise ValueError(f"common_knowledge_verification 输出非法 confidence 范围: {confidence}")

        reasoning = str(assessment.get("reasoning", "")).strip()
        if "examiner_rejection_rationale" not in assessment:
            raise ValueError("common_knowledge_verification 输出缺少 assessment.examiner_rejection_rationale")
        rejection_rationale = str(assessment.get("examiner_rejection_rationale", "")).strip()
        if verdict == "APPLICANT_CORRECT" and not rejection_rationale:
            raise ValueError(
                "common_knowledge_verification 输出非法: verdict=APPLICANT_CORRECT 时 examiner_rejection_rationale 不能为空"
            )
        if verdict != "APPLICANT_CORRECT":
            rejection_rationale = ""

        evidence_items: List[Dict[str, Any]] = []
        for item in output.get("evidence", []) or []:
            evidence = self._to_dict(item)
            doc_id = str(evidence.get("doc_id", "")).strip()
            if not doc_id:
                continue
            if allowed_doc_ids and doc_id not in allowed_doc_ids:
                continue

            source_item = external_doc_map.get(doc_id, {})
            evidence_items.append({
                "doc_id": doc_id,
                "quote": str(evidence.get("quote", "")).strip(),
                "location": str(evidence.get("location", "")).strip(),
                "analysis": str(evidence.get("analysis", "")).strip(),
                "source_url": str(
                    evidence.get("source_url")
                    or source_item.get("source_url")
                    or source_item.get("url")
                    or ""
                ).strip() or None,
                "source_title": str(
                    evidence.get("source_title")
                    or source_item.get("source_title")
                    or source_item.get("title")
                    or ""
                ).strip() or None,
                "source_type": str(evidence.get("source_type") or source_item.get("source_type") or "").strip() or None,
            })

        return {
            "assessment": {
                "verdict": verdict,
                "reasoning": reasoning,
                "confidence": confidence,
                "examiner_rejection_rationale": rejection_rationale,
            },
            "evidence": evidence_items,
        }

    def _state_get(self, state: Any, key: str, default=None):
        if isinstance(state, dict):
            return state.get(key, default)
        return getattr(state, key, default)

    def _to_dict(self, item: Any) -> Dict[str, Any]:
        if isinstance(item, dict):
            return item
        if hasattr(item, "model_dump"):
            return item.model_dump()
        if hasattr(item, "dict"):
            return item.dict()
        return {}

    def _normalize_claim_ids(self, value: Any) -> List[str]:
        claim_ids: List[str] = []
        candidates = value if isinstance(value, list) else [value]
        for raw in candidates:
            text = str(raw or "").strip()
            if not text:
                continue
            for piece in re.split(r"[，,\s]+", text):
                part = piece.strip()
                if not part or not part.isdigit():
                    continue
                if part not in claim_ids:
                    claim_ids.append(part)
        return claim_ids
