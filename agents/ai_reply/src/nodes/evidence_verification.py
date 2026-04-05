"""
证据核查节点
基于 prepared_materials 中的原权利要求与对比文件内容，对事实争议进行核查
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from typing import Any, Dict, List, Set, Tuple
from loguru import logger
from agents.common.retrieval import LocalEvidenceRetriever
from agents.common.utils.concurrency import submit_with_current_context
from agents.common.utils.llm import get_llm_service
from agents.ai_reply.src.utils import get_node_cache
from agents.ai_reply.src.state import EvidenceAssessment
from config import settings


class EvidenceVerificationNode:
    """证据核查节点（新结构）"""
    _MAX_DOC_CACHE_MARKERS = 3
    _FULL_DOC_CONTEXT_LIMIT = 16000
    _NON_PATENT_RETRIEVAL_QUERY_LIMIT = 4

    def __init__(self, config=None):
        self.config = config
        self.llm_service = get_llm_service()

    def __call__(self, state):
        logger.info("开始证据核查")
        updates = {}

        try:
            cache = get_node_cache(self.config, "evidence_verification")
            assessments = cache.run_step(
                "verify_evidence_v7",
                self._verify_evidence,
                self._state_get(state, "disputes", []),
                self._state_get(state, "prepared_materials", {}),
                self._state_get(state, "claims_old_structured", []),
            )

            if not assessments:
                logger.info("没有需要进行事实核查的争议项")
                return updates

            updates["evidence_assessments"] = [
                item if isinstance(item, EvidenceAssessment) else EvidenceAssessment(**item)
                for item in assessments
            ]
            logger.info(f"完成 {len(assessments)} 个争议项的事实核查")

        except Exception as e:
            logger.error(f"证据核查节点执行失败: {e}")
            updates["errors"] = [{
                "node_name": "evidence_verification",
                "error_message": str(e),
                "error_type": "evidence_verification_error",
            }]

        return updates

    def _verify_evidence(self, disputes, prepared_materials, claims_structured):
        document_disputes = self._get_document_based_disputes(disputes)
        if not document_disputes:
            return []

        prepared = self._to_dict(prepared_materials)
        claims = self._normalize_claims(claims_structured)
        comparison_doc_map = self._build_comparison_doc_map(prepared)
        local_retriever = self._build_local_retriever(prepared)
        grouped_disputes = self._group_disputes_by_docs(document_disputes)

        verification_jobs: List[Dict[str, Any]] = []
        for doc_group in sorted(grouped_disputes.keys()):
            group_items = grouped_disputes[doc_group]
            docs_context, retrieval_docs, missing_doc_ids = self._build_docs_context(doc_group, comparison_doc_map)
            prefix_messages = self._build_prefix_messages(docs_context)

            for dispute in group_items:
                verification_jobs.append(
                    self._build_verification_job(
                        dispute=dispute,
                        claims=claims,
                        doc_group=doc_group,
                        missing_doc_ids=missing_doc_ids,
                        prefix_messages=prefix_messages,
                        docs_context=docs_context,
                        retrieval_docs=retrieval_docs,
                        local_retriever=local_retriever,
                    )
                )

        if not verification_jobs:
            return []

        max_workers = max(
            1,
            min(
                settings.OAR_MAX_CONCURRENCY,
                len(verification_jobs),
            ),
        )
        if len(verification_jobs) == 1:
            job = verification_jobs[0]
            return [self._verify_single_dispute(**self._job_call_kwargs(job))]

        logger.info(
            f"证据核查并行执行: jobs={len(verification_jobs)} workers={max_workers}"
        )
        ordered_results: List[Dict[str, Any] | None] = [None] * len(verification_jobs)
        available_prefixes: Set[Tuple[str, ...]] = set()
        pending_indices = list(range(len(verification_jobs)))
        if len(verification_jobs) > 2 and max_workers > 1:
            warm_index = self._select_warmup_job_index(verification_jobs)
            ordered_results[warm_index] = self._verify_single_dispute(
                **self._job_call_kwargs(verification_jobs[warm_index])
            )
            available_prefixes.update(self._job_cache_prefixes(verification_jobs[warm_index]))
            pending_indices.remove(warm_index)

        if not pending_indices:
            return [item for item in ordered_results if item]

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures: Dict[Any, int] = {}
            while pending_indices or futures:
                while pending_indices and len(futures) < max_workers:
                    next_index = self._select_next_job_index(
                        pending_indices,
                        verification_jobs,
                        available_prefixes,
                    )
                    futures[
                        submit_with_current_context(
                            executor,
                            self._verify_single_dispute,
                            **self._job_call_kwargs(verification_jobs[next_index]),
                        )
                    ] = next_index
                    pending_indices.remove(next_index)

                if not futures:
                    continue

                future = next(as_completed(list(futures.keys())))
                index = futures.pop(future)
                ordered_results[index] = future.result()
                available_prefixes.update(self._job_cache_prefixes(verification_jobs[index]))

        return [item for item in ordered_results if item]

    def _get_document_based_disputes(self, disputes: List[Any]) -> List[Dict[str, Any]]:
        document_disputes: List[Dict[str, Any]] = []
        for item in disputes or []:
            dispute = self._to_dict(item)
            examiner_opinion = self._to_dict(dispute.get("examiner_opinion", {}))
            dispute_type = str(examiner_opinion.get("type", "")).strip()
            if dispute_type in {"document_based", "mixed_basis"}:
                document_disputes.append(dispute)
        return document_disputes

    def _normalize_claims(self, claims_structured: List[Any]) -> List[Dict[str, Any]]:
        return [self._to_dict(claim) for claim in (claims_structured or []) if self._to_dict(claim)]

    def _build_comparison_doc_map(self, prepared_materials: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        comparison_doc_map: Dict[str, Dict[str, Any]] = {}
        for item in prepared_materials.get("comparison_documents", []) or []:
            doc = self._to_dict(item)
            doc_id = str(doc.get("document_id", "")).strip()
            if doc_id:
                comparison_doc_map[doc_id] = doc
        return comparison_doc_map

    def _group_disputes_by_docs(self, disputes: List[Dict[str, Any]]) -> Dict[Tuple[str, ...], List[Dict[str, Any]]]:
        grouped: Dict[Tuple[str, ...], List[Dict[str, Any]]] = defaultdict(list)
        for dispute in disputes:
            examiner_opinion = self._to_dict(dispute.get("examiner_opinion", {}))
            supporting_docs = examiner_opinion.get("supporting_docs", [])
            if not isinstance(supporting_docs, list):
                supporting_docs = []
            normalized_ids = []
            for item in supporting_docs:
                value = str(self._to_dict(item).get("doc_id", "")).strip()
                if value and value not in normalized_ids:
                    normalized_ids.append(value)
            grouped[tuple(sorted(normalized_ids))].append(dispute)
        return grouped

    def _build_docs_context(
        self,
        doc_group: Tuple[str, ...],
        comparison_doc_map: Dict[str, Dict[str, Any]],
    ) -> Tuple[List[Dict[str, str]], List[Dict[str, str]], List[str]]:
        docs_context: List[Dict[str, str]] = []
        retrieval_docs: List[Dict[str, str]] = []
        missing_doc_ids: List[str] = []

        for doc_id in doc_group:
            doc = comparison_doc_map.get(doc_id)
            if not doc:
                missing_doc_ids.append(doc_id)
                continue

            content = self._extract_doc_content(doc)
            if not content:
                missing_doc_ids.append(doc_id)
                continue

            doc_context = {
                "doc_id": doc_id,
                "document_number": str(doc.get("document_number", "")),
                "content": content,
            }
            if self._should_use_retrieval_context(doc, content):
                retrieval_docs.append(doc_context)
                continue
            docs_context.append({
                **doc_context,
                "content": content[: self._FULL_DOC_CONTEXT_LIMIT],
            })

        return docs_context, retrieval_docs, missing_doc_ids

    def _build_verification_job(
        self,
        *,
        dispute: Dict[str, Any],
        claims: List[Dict[str, Any]],
        doc_group: Tuple[str, ...],
        missing_doc_ids: List[str],
        prefix_messages: List[Dict[str, Any]],
        docs_context: List[Dict[str, str]],
        retrieval_docs: List[Dict[str, str]] | None = None,
        local_retriever: LocalEvidenceRetriever | None = None,
    ) -> Dict[str, Any]:
        return {
            "dispute": dispute,
            "claims": claims,
            "doc_group": doc_group,
            "missing_doc_ids": missing_doc_ids,
            "prefix_messages": prefix_messages,
            "retrieval_docs": retrieval_docs or [],
            "local_retriever": local_retriever,
            "_cache_prefix_scores": self._build_cache_prefix_scores(docs_context),
        }

    def _build_cache_prefix_scores(
        self,
        docs_context: List[Dict[str, str]],
    ) -> Dict[Tuple[str, ...], int]:
        prefix_scores: Dict[Tuple[str, ...], int] = {}
        prefix_doc_ids: List[str] = []
        cumulative_chars = 0
        for doc in docs_context[: self._MAX_DOC_CACHE_MARKERS]:
            prefix_doc_ids.append(str(doc.get("doc_id", "")).strip())
            cumulative_chars += len(self._build_doc_context_text(doc))
            prefix_scores[tuple(prefix_doc_ids)] = cumulative_chars
        return prefix_scores

    def _build_doc_context_text(self, doc: Dict[str, str]) -> str:
        return (
            f"对比文件上下文：{doc['doc_id']} ({doc['document_number']})\n"
            f"全文片段如下：\n{doc['content']}"
        )

    @staticmethod
    def _job_call_kwargs(job: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "dispute": job["dispute"],
            "claims": job["claims"],
            "doc_group": job["doc_group"],
            "missing_doc_ids": job["missing_doc_ids"],
            "prefix_messages": job["prefix_messages"],
            "retrieval_docs": job["retrieval_docs"],
            "local_retriever": job["local_retriever"],
        }

    @staticmethod
    def _job_cache_prefixes(job: Dict[str, Any]) -> Set[Tuple[str, ...]]:
        return set(job.get("_cache_prefix_scores", {}).keys())

    def _job_cache_hit_score(
        self,
        job: Dict[str, Any],
        available_prefixes: Set[Tuple[str, ...]],
    ) -> int:
        prefix_scores = job.get("_cache_prefix_scores", {})
        return max(
            (
                int(score)
                for prefix, score in prefix_scores.items()
                if prefix in available_prefixes
            ),
            default=0,
        )

    def _job_seed_value(
        self,
        job: Dict[str, Any],
        candidate_jobs: List[Dict[str, Any]],
    ) -> int:
        available_prefixes = self._job_cache_prefixes(job)
        return sum(
            self._job_cache_hit_score(other_job, available_prefixes)
            for other_job in candidate_jobs
            if other_job is not job
        )

    @staticmethod
    def _job_total_cache_score(job: Dict[str, Any]) -> int:
        return max((int(v) for v in job.get("_cache_prefix_scores", {}).values()), default=0)

    def _select_warmup_job_index(self, jobs: List[Dict[str, Any]]) -> int:
        best_index = 0
        best_key: Tuple[int, int] | None = None
        for index, job in enumerate(jobs):
            key = (
                self._job_seed_value(job, jobs),
                self._job_total_cache_score(job),
            )
            if best_key is None or key > best_key:
                best_index = index
                best_key = key
        return best_index

    def _select_next_job_index(
        self,
        pending_indices: List[int],
        jobs: List[Dict[str, Any]],
        available_prefixes: Set[Tuple[str, ...]],
    ) -> int:
        best_index = pending_indices[0]
        best_key: Tuple[int, int, int] | None = None
        pending_jobs = [jobs[index] for index in pending_indices]

        for index in pending_indices:
            job = jobs[index]
            key = (
                self._job_cache_hit_score(job, available_prefixes),
                self._job_seed_value(job, pending_jobs),
                self._job_total_cache_score(job),
            )
            if best_key is None or key > best_key:
                best_index = index
                best_key = key
        return best_index

    def _extract_doc_content(self, doc: Dict[str, Any]) -> str:
        is_patent = bool(doc.get("is_patent", False))
        data = doc.get("data")

        if is_patent:
            data_dict = self._to_dict(data)
            description = self._to_dict(data_dict.get("description", {}))
            return str(description.get("detailed_description", "")).strip()

        if isinstance(data, str):
            return data.strip()

        if isinstance(data, dict):
            return json.dumps(data, ensure_ascii=False)

        return ""

    def _should_use_retrieval_context(self, doc: Dict[str, Any], content: str) -> bool:
        return not bool(doc.get("is_patent", False)) and len(content) > self._FULL_DOC_CONTEXT_LIMIT

    def _build_prefix_messages(self, docs_context: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self._build_system_prompt()},
        ]

        for index, doc in enumerate(docs_context):
            text = self._build_doc_context_text(doc)
            content_item: Dict[str, Any] = {
                "type": "text",
                "text": text,
            }
            if index < self._MAX_DOC_CACHE_MARKERS:
                content_item["cache_control"] = {"type": "ephemeral"}
            messages.append({
                "role": "user",
                "content": [content_item],
            })

        return messages

    def _build_system_prompt(self) -> str:
        return """你是一位资深的中国国家知识产权局（CNIPA）专利审查专家及严格的事实核查员。
你的核心任务是：基于提供的【对比文件原文】，对【审查员的驳回意见】与【申请人的反驳意见】之间的争议进行客观、中立的事实核查。

### 事实核查标准与判定逻辑（严格遵守）
1. **证据为王**：必须且只能基于下文提供的对比文件（D1/D2等）的原文片段进行判定，严禁引入任何外部知识或主观推测。
2. **特征比对**：仔细拆解争议的“权利要求技术特征”，在对比文件中寻找是否存在对应的公开内容（明示或本领域隐含公开）。
3. **裁决标准 (verdict)**：
   - `EXAMINER_CORRECT`：对比文件确实公开了该特征，或审查员的结合逻辑在原文中有坚实支撑。申请人的反驳不成立。
   - `APPLICANT_CORRECT`：对比文件并未公开该特征，或原文含义被审查员曲解/误读。申请人的事实主张成立。
   - `INCONCLUSIVE`：提供的对比文件内容缺失、乱码，或提供的信息不足以做出判定。

### 特殊字段约束：examiner_rejection_rationale（极度重要）
在专利审查实务的自动化流程中，当审查员最初的某项认定被指出错误时，系统需尝试寻找新的驳回理由。
- **当 verdict = "EXAMINER_CORRECT" 或 "INCONCLUSIVE" 时**：该字段必须为空字符串 `""`。
- **当 verdict = "APPLICANT_CORRECT" 时**：你必须基于核查后发现的真实对比文件内容，提供一段**替代性驳回逻辑要点**。
  - **内容要求（强制）**：只需客观说明审查员仍可依据什么新的证据映射、组合逻辑或技术常识继续维持驳回。
  - **表达要求（强制）**：只写逻辑骨架，不需要正式公文口吻，不要写成完整通知书正文。
  - **禁止事项**：严禁脱离当前核查证据新增事实，严禁输出策略性元话术。

### JSON 输出格式与字段定义
你必须输出且仅输出一个合法的 JSON 对象，不要包含任何 Markdown 格式标记（如 ```json），不要有任何前言或后语。JSON 结构如下：

{
  "assessment": {
    "verdict": "必须是 APPLICANT_CORRECT, EXAMINER_CORRECT, INCONCLUSIVE 之一",
    "reasoning": "你的核查分析过程。指出审查员和申请人谁对谁错，以及为什么。逻辑需严密。",
    "confidence": 0.95, // 0.0到1.0之间的浮点数，表示你对该判定的信心
    "examiner_rejection_rationale": "严格遵循上述【特殊字段约束】的要求填写"
  },
  "evidence":[
    {
      "doc_id": "必须是当前争议项 supporting_docs 中给出的 doc_id（如 D1）",
      "quote": "必须从对比文件中【一字不差】地复制支撑你结论的原句，严禁洗稿或概括！",
      "location": "描述引用内容在文档中的大概位置或上下文环境",
      "analysis": "解释这段引用原文是如何支撑 assessment 结论的"
    }
  ]
}

### 绝对禁止事项（红线）
1. 严禁捏造或篡改 `evidence.quote` 中的原文内容，若找不到原话，说明对比文件未公开。
2. 严禁输出 JSON 之外的任何多余字符。
3. `confidence` 不得输出为字符串。
4. 即使你使用了思维链（Thinking），最终的输出也必须只保留 JSON 结果。"""

    def _verify_single_dispute(
        self,
        dispute: Dict[str, Any],
        claims: List[Dict[str, Any]],
        doc_group: Tuple[str, ...],
        missing_doc_ids: List[str],
        prefix_messages: List[Dict[str, str]],
        retrieval_docs: List[Dict[str, str]],
        local_retriever: LocalEvidenceRetriever | None,
    ) -> Dict[str, Any]:
        claim_text = self._get_claim_text(dispute, claims)
        examiner_opinion = self._to_dict(dispute.get("examiner_opinion", {}))
        applicant_opinion = self._to_dict(dispute.get("applicant_opinion", {}))
        messages: List[Dict[str, Any]] = list(prefix_messages)

        if retrieval_docs:
            messages.extend(
                self._build_long_non_patent_messages(
                    dispute=dispute,
                    claim_text=claim_text,
                    examiner_opinion=examiner_opinion,
                    applicant_opinion=applicant_opinion,
                    retrieval_docs=retrieval_docs,
                    local_retriever=local_retriever,
                )
            )
        elif len(messages) == 1:
            messages.append({
                "role": "user",
                "content": "当前争议项未提供任何可用对比文件全文内容。请在输出中给出 INCONCLUSIVE。",
            })

        dispute_prompt = f"""请核查以下争议项：
claim_ids: {json.dumps(self._normalize_claim_ids(dispute.get("claim_ids", [])), ensure_ascii=False)}
claim_text: {claim_text}
feature_text: {dispute.get("feature_text", "")}
examiner_opinion: {json.dumps(examiner_opinion, ensure_ascii=False)}
applicant_opinion: {json.dumps(applicant_opinion, ensure_ascii=False)}
supporting_docs_doc_ids: {json.dumps(list(doc_group), ensure_ascii=False)}
missing_doc_ids: {json.dumps(missing_doc_ids, ensure_ascii=False)}
"""
        messages.append({"role": "user", "content": dispute_prompt})

        response = self.llm_service.invoke_text_json(
            messages=messages,
            task_kind="oar_evidence_verification",
            temperature=0.05,
        )
        parsed = self._normalize_llm_output(response, set(doc_group))

        claim_ids = self._normalize_claim_ids(dispute.get("claim_ids", []))
        feature_text = str(dispute.get("feature_text", "")).strip()
        claim_key = "_".join(claim_ids[:4]) if claim_ids else "UNKNOWN"

        return {
            "dispute_id": str(dispute.get("dispute_id", f"{claim_key}_{feature_text[:30]}")),
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
                "used_doc_ids": list(doc_group),
                "missing_doc_ids": list(missing_doc_ids),
            },
        }

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

    def _build_long_non_patent_messages(
        self,
        dispute: Dict[str, Any],
        claim_text: str,
        examiner_opinion: Dict[str, Any],
        applicant_opinion: Dict[str, Any],
        retrieval_docs: List[Dict[str, str]],
        local_retriever: LocalEvidenceRetriever | None,
    ) -> List[Dict[str, Any]]:
        queries = self._build_non_patent_queries(
            dispute=dispute,
            claim_text=claim_text,
            examiner_opinion=examiner_opinion,
            applicant_opinion=applicant_opinion,
        )
        messages: List[Dict[str, Any]] = []
        for doc in retrieval_docs:
            cards = self._search_non_patent_evidence_cards(
                doc_id=str(doc.get("doc_id", "")).strip(),
                queries=queries,
                local_retriever=local_retriever,
            )
            if cards:
                messages.append({
                    "role": "user",
                    "content": self._build_non_patent_card_prompt(doc, queries, cards),
                })
                continue
            messages.append({
                "role": "user",
                "content": self._build_non_patent_fallback_prompt(doc),
            })
        return messages

    def _build_non_patent_queries(
        self,
        dispute: Dict[str, Any],
        claim_text: str,
        examiner_opinion: Dict[str, Any],
        applicant_opinion: Dict[str, Any],
    ) -> List[str]:
        feature_text = str(dispute.get("feature_text", "")).strip()
        candidates = [
            feature_text,
            f"{feature_text} {claim_text[:180]}".strip(),
            str(examiner_opinion.get("reasoning", "")).strip(),
            str(applicant_opinion.get("reasoning", "")).strip(),
            str(applicant_opinion.get("core_conflict", "")).strip(),
        ]
        queries: List[str] = []
        for raw in candidates:
            value = re.sub(r"\s+", " ", str(raw or "")).strip()
            if len(value) < 4 or value in queries:
                continue
            queries.append(value[:240])
            if len(queries) >= self._NON_PATENT_RETRIEVAL_QUERY_LIMIT:
                break
        return queries

    def _search_non_patent_evidence_cards(
        self,
        doc_id: str,
        queries: List[str],
        local_retriever: LocalEvidenceRetriever | None,
    ) -> List[Dict[str, Any]]:
        if not local_retriever or not doc_id or not queries:
            return []

        candidates: List[Dict[str, Any]] = []
        for query in queries:
            candidates.extend(
                local_retriever.search(
                    query=query,
                    intent="fact_verification",
                    doc_filters=[doc_id],
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

        reranked = sorted(
            deduped.values(),
            key=lambda x: float(x.get("relevance_score", 0.0)),
            reverse=True,
        )[: settings.LOCAL_RETRIEVAL_RERANK_K]
        if not reranked:
            return []

        card_bundle = local_retriever.build_evidence_cards(
            candidates=reranked,
            context_k=max(settings.LOCAL_RETRIEVAL_CONTEXT_K, 4),
            max_context_chars=max(settings.LOCAL_RETRIEVAL_MAX_CONTEXT_CHARS, 2600),
            max_quote_chars=max(settings.LOCAL_RETRIEVAL_MAX_QUOTE_CHARS, 220),
            read_window=1,
        )
        cards: List[Dict[str, Any]] = []
        for item in card_bundle.get("cards", []) or []:
            card = self._to_dict(item)
            cards.append(
                {
                    "doc_id": str(card.get("doc_id", "")).strip(),
                    "quote": str(card.get("quote", "")).strip(),
                    "location": str(card.get("location", "")).strip(),
                    "analysis": str(card.get("analysis", "")).strip(),
                }
            )
        return cards

    def _build_non_patent_card_prompt(
        self,
        doc: Dict[str, str],
        queries: List[str],
        cards: List[Dict[str, Any]],
    ) -> str:
        return (
            "以下为超长非专对比文件的检索证据卡，仅用于在原文中定位相关段落；"
            "这些内容不作为显式缓存前缀。\n"
            f"doc_id: {doc.get('doc_id', '')}\n"
            f"document_number: {doc.get('document_number', '')}\n"
            f"retrieval_queries: {json.dumps(queries, ensure_ascii=False)}\n"
            f"evidence_cards: {json.dumps(cards, ensure_ascii=False)}"
        )

    def _build_non_patent_fallback_prompt(self, doc: Dict[str, str]) -> str:
        excerpt = str(doc.get("content", ""))[: self._FULL_DOC_CONTEXT_LIMIT]
        return (
            "以下为超长非专对比文件的检索回退原文片段；由于未命中稳定证据卡，现提供截断原文供核查。"
            "这些内容不作为显式缓存前缀。\n"
            f"doc_id: {doc.get('doc_id', '')}\n"
            f"document_number: {doc.get('document_number', '')}\n"
            f"全文片段如下：\n{excerpt}"
        )

    def _normalize_llm_output(self, response: Dict[str, Any], allowed_doc_ids: set[str]) -> Dict[str, Any]:
        output = self._to_dict(response)
        assessment = self._to_dict(output.get("assessment", {}))

        verdict = str(assessment.get("verdict", "")).strip()
        if verdict not in {"APPLICANT_CORRECT", "EXAMINER_CORRECT", "INCONCLUSIVE"}:
            raise ValueError(f"evidence_verification 输出非法 verdict: {verdict}")

        confidence = assessment.get("confidence", 0.0)
        try:
            confidence = float(confidence)
        except Exception as e:
            raise ValueError(f"evidence_verification 输出非法 confidence: {confidence}") from e
        if confidence < 0.0 or confidence > 1.0:
            raise ValueError(f"evidence_verification 输出非法 confidence 范围: {confidence}")

        reasoning = str(assessment.get("reasoning", "")).strip()
        if "examiner_rejection_rationale" not in assessment:
            raise ValueError("evidence_verification 输出缺少 assessment.examiner_rejection_rationale")
        rejection_rationale = str(assessment.get("examiner_rejection_rationale", "")).strip()
        if verdict == "APPLICANT_CORRECT" and not rejection_rationale:
            raise ValueError("evidence_verification 输出非法: verdict=APPLICANT_CORRECT 时 examiner_rejection_rationale 不能为空")
        if verdict != "APPLICANT_CORRECT":
            rejection_rationale = ""

        evidence_items = []
        for item in output.get("evidence", []) or []:
            evidence = self._to_dict(item)
            doc_id = str(evidence.get("doc_id", "")).strip()
            if doc_id and allowed_doc_ids and doc_id not in allowed_doc_ids:
                continue
            evidence_items.append({
                "doc_id": doc_id,
                "quote": str(evidence.get("quote", "")).strip(),
                "location": str(evidence.get("location", "")).strip(),
                "analysis": str(evidence.get("analysis", "")).strip(),
                "source_url": str(evidence.get("source_url", "")).strip() or None,
                "source_title": str(evidence.get("source_title", "")).strip() or None,
                "source_type": str(evidence.get("source_type", "")).strip() or None,
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
