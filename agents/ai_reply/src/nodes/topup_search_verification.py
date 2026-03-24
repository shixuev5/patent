"""
补充检索与评判节点
针对新增特征执行对比文件深扫与外部检索，并由大模型给出最终裁决
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Set, Tuple

from loguru import logger

from agents.common.retrieval import LocalEvidenceRetriever
from agents.common.utils.concurrency import submit_with_current_context
from agents.common.utils.llm import get_llm_service
from agents.ai_reply.src.external_evidence import ExternalEvidenceAggregator
from agents.ai_reply.src.retrieval_utils import (
    build_trace_retrieval,
    normalize_query_list,
    plan_engine_queries,
)
from agents.ai_reply.src.state import Dispute, EvidenceAssessment
from agents.ai_reply.src.utils import get_node_cache
from config import settings


class TopupSearchVerificationNode:
    """补充检索与评判节点（LLM主判）"""

    def __init__(self, config=None):
        self.config = config
        self.llm_service = get_llm_service()
        self.external_evidence_aggregator = ExternalEvidenceAggregator()

    def __call__(self, state):
        logger.info("开始补充检索与评判")
        updates = {}

        try:
            topup_tasks = self._state_get(state, "topup_tasks", []) or[]
            if not topup_tasks:
                logger.info("无补充检索任务，跳过")
                return updates

            cache = get_node_cache(self.config, "topup_search_verification")
            result = cache.run_step(
                "verify_topup_v8",
                self._verify_topup,
                topup_tasks,
                self._state_get(state, "prepared_materials", {}),
                self._state_get(state, "claims_effective_structured", []),
            )

            updates["disputes"] =[
                item if isinstance(item, Dispute) else Dispute(**item)
                for item in result.get("disputes", [])
            ]
            updates["evidence_assessments"] =[
                item if isinstance(item, EvidenceAssessment) else EvidenceAssessment(**item)
                for item in result.get("evidence_assessments", [])
            ]
            logger.info(
                f"补充检索完成，新增争议项: {len(updates.get('disputes', []))}，核查结果: {len(updates.get('evidence_assessments',[]))}"
            )
        except Exception as e:
            logger.error(f"补充检索与评判失败: {e}")
            updates["errors"] =[{
                "node_name": "topup_search_verification",
                "error_message": str(e),
                "error_type": "topup_search_verification_error",
            }]

        return updates

    def _verify_topup(self, topup_tasks, prepared_materials, claims_structured):
        tasks =[self._to_dict(item) for item in (topup_tasks or [])]
        prepared = self._to_dict(prepared_materials)

        claims = self._normalize_claims(claims_structured)
        comparison_docs = self._build_comparison_docs(prepared)
        priority_date = self._extract_priority_date(prepared)
        local_retriever = self._build_local_retriever(prepared)

        max_workers = max(1, min(settings.OAR_MAX_CONCURRENCY, len(tasks)))
        if len(tasks) == 1:
            dispute, assessment = self._evaluate_task(
                task=tasks[0],
                claims=claims,
                comparison_docs=comparison_docs,
                priority_date=priority_date,
                local_retriever=local_retriever,
            )
            return {
                "disputes": [dispute],
                "evidence_assessments": [assessment],
            }
        logger.info(f"补充检索并行执行: tasks={len(tasks)} workers={max_workers}")
        ordered_results: List[Tuple[Dict[str, Any], Dict[str, Any]] | None] = [None] * len(tasks)
        remaining_tasks = tasks
        if len(tasks) > 2 and max_workers > 1:
            warm_dispute, warm_assessment = self._evaluate_task(
                task=tasks[0],
                claims=claims,
                comparison_docs=comparison_docs,
                priority_date=priority_date,
                local_retriever=local_retriever,
            )
            ordered_results[0] = (warm_dispute, warm_assessment)
            remaining_tasks = tasks[1:]

        if not remaining_tasks:
            disputes = [item[0] for item in ordered_results if item]
            assessments = [item[1] for item in ordered_results if item]
            return {"disputes": disputes, "evidence_assessments": assessments}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                submit_with_current_context(
                    executor,
                    self._evaluate_task,
                    task=task,
                    claims=claims,
                    comparison_docs=comparison_docs,
                    priority_date=priority_date,
                    local_retriever=local_retriever,
                ): index
                for index, task in enumerate(
                    remaining_tasks,
                    start=1 if ordered_results[0] else 0,
                )
            }
            for future in as_completed(futures):
                index = futures[future]
                ordered_results[index] = future.result()

        disputes: List[Dict[str, Any]] = []
        assessments: List[Dict[str, Any]] = []
        for result in ordered_results:
            if not result:
                continue
            dispute, assessment = result
            disputes.append(dispute)
            assessments.append(assessment)

        return {
            "disputes": disputes,
            "evidence_assessments": assessments,
        }

    def _evaluate_task(
        self,
        task: Dict[str, Any],
        claims: List[Dict[str, Any]],
        comparison_docs: Dict[str, Dict[str, Any]],
        priority_date: str,
        local_retriever: LocalEvidenceRetriever | None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        task_id = str(task.get("task_id", "")).strip() or "New_FX"
        claim_ids = self._normalize_claim_ids(task.get("claim_ids", [])) or ["1"]
        feature_text = str(task.get("feature_text", "")).strip()
        dispute_id = f"TOPUP_{task_id}"
        claim_text = self._get_claim_text(claim_ids, claims)

        local_evidence, local_retrieval_trace = self._search_local_evidence(
            feature_text=feature_text,
            claim_text=claim_text,
            comparison_docs=comparison_docs,
            local_retriever=local_retriever,
        )
        external_queries = self._build_engine_queries(task, claim_text, feature_text, priority_date)
        external_candidates, retrieval_engines, retrieval_meta = self.external_evidence_aggregator.search_evidence(
            queries=external_queries,
            priority_date=priority_date,
            limit=6,
        )
        external_evidence = self._to_external_evidence_items(external_candidates)

        evidence_context = local_evidence + external_evidence
        allowed_doc_ids: Set[str] = {str(item.get("doc_id", "")).strip() for item in evidence_context if item.get("doc_id")}
        allowed_doc_ids.add("MODEL")
        evidence_map = {
            str(item.get("doc_id", "")).strip(): item
            for item in evidence_context
            if item.get("doc_id")
        }

        messages =[
            {"role": "system", "content": self._build_system_prompt()},
            {
                "role": "user",
                "content": self._build_user_prompt(
                    task=task,
                    claim_ids=claim_ids,
                    claim_text=claim_text,
                    feature_text=feature_text,
                    local_evidence=local_evidence,
                    external_evidence=external_evidence,
                    external_queries=external_queries,
                ),
            },
        ]
        response = self.llm_service.invoke_text_json(
            messages=messages,
            task_kind="oar_topup_search_verification",
            temperature=0.05,
        )
        parsed = self._normalize_llm_output(response, allowed_doc_ids, evidence_map)

        examiner_opinion = parsed["examiner_opinion"]
        applicant_opinion = parsed["applicant_opinion"]
        dispute = {
            "dispute_id": dispute_id,
            "claim_ids": claim_ids,
            "feature_text": feature_text,
            "examiner_opinion": examiner_opinion,
            "applicant_opinion": applicant_opinion,
        }
        assessment = {
            "dispute_id": dispute_id,
            "claim_ids": claim_ids,
            "claim_text": claim_text,
            "feature_text": feature_text,
            "examiner_opinion": examiner_opinion,
            "applicant_opinion": applicant_opinion,
            "assessment": parsed["assessment"],
            "evidence": parsed["evidence"],
            "trace": {
                "used_doc_ids": parsed["used_doc_ids"],
                "missing_doc_ids":[],
                "local_retrieval": local_retrieval_trace,
                "retrieval": build_trace_retrieval(external_queries, retrieval_engines, retrieval_meta),
            },
        }
        return dispute, assessment

    def _build_system_prompt(self) -> str:
        return """你是一位资深的中国专利局审查专家（熟练掌握《专利审查指南》）。你的核心任务是：针对申请人修改/新增的权利要求特征，基于提供的【本地对比文件证据(D*)】和【外部检索证据(EXT*)】，进行严谨的比对评判，并给出最终裁决。

### 👨‍⚖️ 审查评判准则（思维要求）
在生成最终结果前，请在内心遵循以下逻辑进行比对分析：
1. **特征解构**：准确理解“新增特征(feature_text)”解决的技术问题和达到的技术效果。
2. **证据比对**：
   - 优先在提供的 D* 或 EXT* 证据中寻找该特征的**直接、明确的公开**（新颖性）。
   - 若未直接公开，评估该特征是否属于本领域的**常规技术手段/公知常识**，或者证据中是否存在将其应用于本发明的**技术启示**（创造性）。
3. **裁决标准(verdict)**：
   - `EXAMINER_CORRECT` (审查员正确)：提供的证据明确公开了新增特征，或该特征属于毫无争议的公知常识，新增特征**不足以**克服原驳回理由。
   - `APPLICANT_CORRECT` (申请人正确)：所有证据均未公开该特征，且不属于公知常识，新增特征具有实质性贡献，**足以**克服原驳回理由。
   - `INCONCLUSIVE` (无法定论)：证据存在高度歧义，或缺乏关键上下文，无法得出确定性结论。

### 📄 输出数据结构（仅限输出合法的JSON格式对象）
{
  "examiner_opinion": {
    "type": "document_based", 
    "supporting_docs":[
      {
        "doc_id": "D1",
        "cited_text": "严格提取证据中的原文片段，不可篡改"
      }
    ],
    "reasoning": "审查员视角的反驳或认定逻辑，需结合证据原文具体说明特征是如何被公开的。"
  },
  "applicant_opinion": {
    "type": "fact_dispute",
    "reasoning": "模拟申请人的答辩逻辑（例如：强调证据未公开特定细节，或强调技术效果的差异）。",
    "core_conflict": "高度概括审查员与申请人之间的核心争议点（15-30字）。"
  },
  "assessment": {
    "verdict": "APPLICANT_CORRECT",
    "reasoning": "作为中立专家的最终裁决理由，解释为什么做出该判决。",
    "confidence": 0.85,
    "examiner_rejection_rationale": "【关键】当判决倾向申请人时，基于当前证据仍可继续维持驳回的逻辑要点（见下方约束）。"
  },
  "evidence":[
    {
      "doc_id": "D1",
      "quote": "相关证据片段",
      "location": "证据的具体位置/段落",
      "analysis": "该证据与新增特征的具体映射关系分析（为何引用它）",
      "source_url": "https://...",
      "source_title": "标题",
      "source_type": "comparison_document"
    }
  ]
}

### ⚠️ 严格约束条件（违反将导致系统崩溃）
【字段值枚举约束】
- examiner_opinion.type 只能是：`document_based` (纯证据)、`common_knowledge_based` (纯公知常识)、`mixed_basis` (证据+公知常识)。
- applicant_opinion.type 固定为：`fact_dispute`。
- assessment.verdict 只能是：`APPLICANT_CORRECT`、`EXAMINER_CORRECT`、`INCONCLUSIVE`。
- assessment.confidence 必须是 0.0 到 1.0 之间的浮点数。

【证据引用约束】
- 当 examiner_opinion.type 为 `document_based` 或 `mixed_basis` 时，`supporting_docs` 数组**必须非空**。
- 当 examiner_opinion.type 为 `common_knowledge_based` 时，`supporting_docs` 数组**必须为空**。
- evidence数组中的 `doc_id` **必须**来自给定证据列表中的 ID（如 D1, EXT_1 等）。
- 如果实在没有外部证据且特征确实是公认的公知常识，`doc_id` 可使用 `"MODEL"`，但必须谨慎使用。

【替代性驳回逻辑约束 (examiner_rejection_rationale) —— 极度重要❗】
- **触发条件**：仅当 verdict 为 `APPLICANT_CORRECT` 时填写；若 verdict 不为 APPLICANT_CORRECT，必须留空字符串 `""`。
- **内容要求**：只需概括基于当前证据仍可继续维持驳回的逻辑骨架，例如新的证据映射、文献结合路径或常规技术推演。
- **表达要求**：不要写成正式通知书正文，不要追求官方修辞。
- **绝对禁止**：禁止脱离当前证据新增事实，禁止输出“建议”“可以尝试”“为了维持驳回”等策略性元语言。
"""

    def _build_user_prompt(
        self,
        task: Dict[str, Any],
        claim_ids: List[str],
        claim_text: str,
        feature_text: str,
        local_evidence: List[Dict[str, Any]],
        external_evidence: List[Dict[str, Any]],
        external_queries: Dict[str, List[str]],
    ) -> str:
        # 使用 default=str 避免序列化特殊对象（如 datetime、Set 等）崩溃
        return f"""请评判以下补充检索任务：

【任务】
{json.dumps(task, ensure_ascii=False, indent=2, default=str)}

【权利要求】
claim_ids: {json.dumps(claim_ids, ensure_ascii=False)}
claim_text: {claim_text}
feature_text: {feature_text}

【本地对比文件证据（D*）】
{json.dumps(local_evidence, ensure_ascii=False, indent=2, default=str)}

【外部检索证据（EXT*）】
{json.dumps(external_evidence, ensure_ascii=False, indent=2, default=str)}

【外部检索查询】
{json.dumps(external_queries, ensure_ascii=False, indent=2, default=str)}"""

    def _build_engine_queries(
        self,
        task: Dict[str, Any],
        claim_text: str,
        feature_text: str,
        priority_date: str,
    ) -> Dict[str, List[str]]:
        fallback_queries = {
            "openalex": normalize_query_list([
                f"{feature_text} prior art conventional method",
                f"{feature_text} patent disclosure implementation",
            ], limit=2),
            "zhihuiya": normalize_query_list([
                f"{feature_text} 专利 技术公开",
                f"{feature_text} 本领域公知",
            ], limit=2),
            "tavily": normalize_query_list([
                f"{feature_text} 技术公开资料",
                f"{feature_text} {claim_text[:120]}",
            ], limit=2),
        }
        user_context = {
            "priority_date": priority_date,
            "task": task,
            "feature_text": feature_text,
            "claim_text": claim_text[:240],
        }
        return plan_engine_queries(
            llm_service=self.llm_service,
            user_context=user_context,
            fallback_queries=fallback_queries,
            scenario="补充检索核查",
            per_engine_limit=2,
        )

    def _normalize_llm_output(
        self,
        response: Dict[str, Any],
        allowed_doc_ids: Set[str],
        evidence_map: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        output = self._to_dict(response)

        # 软回退容错：不再轻易 raise ValueError，而是使用合法默认值
        examiner_opinion = self._to_dict(output.get("examiner_opinion", {}))
        examiner_type = str(examiner_opinion.get("type", "mixed_basis")).strip()
        if examiner_type not in {"document_based", "common_knowledge_based", "mixed_basis"}:
            examiner_type = "mixed_basis"

        supporting_docs_raw = examiner_opinion.get("supporting_docs",[])
        if not isinstance(supporting_docs_raw, list):
            supporting_docs_raw = []

        supporting_docs =[]
        for item in supporting_docs_raw:
            item_dict = self._to_dict(item)
            doc_id = str(item_dict.get("doc_id", "")).strip()
            if not doc_id:
                continue
            if doc_id not in allowed_doc_ids and doc_id != "MODEL":
                continue  # 忽略无效 doc_id 而非报错
            supporting_docs.append({
                "doc_id": doc_id,
                "cited_text": str(item_dict.get("cited_text", "")).strip(),
            })

        deduped_supporting_docs =[]
        seen_doc_ids = set()
        for item in supporting_docs:
            doc_id = item["doc_id"]
            if doc_id in seen_doc_ids:
                continue
            seen_doc_ids.add(doc_id)
            deduped_supporting_docs.append(item)
        supporting_docs = deduped_supporting_docs

        if examiner_type in {"document_based", "mixed_basis"} and not supporting_docs:
            examiner_type = "common_knowledge_based"
        if examiner_type == "common_knowledge_based":
            supporting_docs =[]

        applicant_opinion = self._to_dict(output.get("applicant_opinion", {}))
        applicant_type = str(applicant_opinion.get("type", "fact_dispute")).strip()
        if applicant_type not in {"fact_dispute", "logic_dispute"}:
            applicant_type = "fact_dispute"

        assessment = self._to_dict(output.get("assessment", {}))
        verdict = str(assessment.get("verdict", "INCONCLUSIVE")).strip()
        if verdict not in {"APPLICANT_CORRECT", "EXAMINER_CORRECT", "INCONCLUSIVE"}:
            verdict = "INCONCLUSIVE"

        try:
            confidence = float(assessment.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))  # 确保在 0-1 之间
        except Exception:
            confidence = 0.5

        reasoning = str(assessment.get("reasoning", "")).strip()
        if "examiner_rejection_rationale" not in assessment:
            raise ValueError("topup_search_verification 输出缺少 assessment.examiner_rejection_rationale")
        rejection_rationale = str(assessment.get("examiner_rejection_rationale", "")).strip()
        if verdict == "APPLICANT_CORRECT" and not rejection_rationale:
            raise ValueError(
                "topup_search_verification 输出非法: verdict=APPLICANT_CORRECT 时 examiner_rejection_rationale 不能为空"
            )
        if verdict != "APPLICANT_CORRECT":
            rejection_rationale = ""

        evidence_items = []
        used_doc_ids: List[str] =[]
        evidence_raw = output.get("evidence", [])
        if not isinstance(evidence_raw, list):
            evidence_raw =[]

        for item in evidence_raw:
            evidence = self._to_dict(item)
            doc_id = str(evidence.get("doc_id", "")).strip()
            if not doc_id or (doc_id not in allowed_doc_ids and doc_id != "MODEL"):
                continue  # 忽略无效证据

            source_item = evidence_map.get(doc_id, {})
            if doc_id not in used_doc_ids:
                used_doc_ids.append(doc_id)

            evidence_items.append({
                "doc_id": doc_id,
                "quote": str(evidence.get("quote", "")).strip(),
                "location": str(evidence.get("location", "")).strip(),
                "analysis": str(evidence.get("analysis", "")).strip(),
                "source_url": str(evidence.get("source_url") or source_item.get("source_url") or "").strip() or None,
                "source_title": str(evidence.get("source_title") or source_item.get("source_title") or "").strip() or None,
                "source_type": str(evidence.get("source_type") or source_item.get("source_type") or "").strip() or None,
            })

        return {
            "examiner_opinion": {
                "type": examiner_type,
                "supporting_docs": supporting_docs,
                "reasoning": str(examiner_opinion.get("reasoning", "")).strip(),
            },
            "applicant_opinion": {
                "type": applicant_type,
                "reasoning": str(applicant_opinion.get("reasoning", "")).strip(),
                "core_conflict": str(applicant_opinion.get("core_conflict", "")).strip(),
            },
            "assessment": {
                "verdict": verdict,
                "reasoning": reasoning,
                "confidence": confidence,
                "examiner_rejection_rationale": rejection_rationale,
            },
            "evidence": evidence_items,
            "used_doc_ids": used_doc_ids,
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

    def _search_local_evidence(
        self,
        feature_text: str,
        claim_text: str,
        comparison_docs: Dict[str, Dict[str, Any]],
        local_retriever: LocalEvidenceRetriever | None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        doc_filters = [doc_id for doc_id in comparison_docs.keys()]
        queries = normalize_query_list(
            [
                feature_text,
                f"{feature_text} {claim_text[:120]}",
            ],
            limit=2,
        )

        if not local_retriever:
            return [], {
                "enabled": False,
                "fallback": "no_local_retriever",
                "queries": queries,
                "doc_filters": doc_filters,
                "hit_chunks": [],
                "selected_cards": [],
            }

        candidates: List[Dict[str, Any]] = []
        for query in queries:
            hits = local_retriever.search(
                query=query,
                intent="fact_verification",
                doc_filters=doc_filters,
                top_k=settings.LOCAL_RETRIEVAL_CANDIDATE_K,
            )
            candidates.extend(hits)

        deduped: Dict[str, Dict[str, Any]] = {}
        for item in candidates:
            chunk_id = str(item.get("chunk_id", "")).strip()
            if not chunk_id:
                continue
            existing = deduped.get(chunk_id)
            if not existing or float(item.get("score", 0.0)) > float(existing.get("score", 0.0)):
                deduped[chunk_id] = item

        reranked = sorted(
            deduped.values(),
            key=lambda x: float(x.get("score", 0.0)),
            reverse=True,
        )[: settings.LOCAL_RETRIEVAL_RERANK_K]

        card_bundle = local_retriever.build_evidence_cards(
            candidates=reranked,
            context_k=settings.LOCAL_RETRIEVAL_CONTEXT_K,
            max_context_chars=settings.LOCAL_RETRIEVAL_MAX_CONTEXT_CHARS,
            max_quote_chars=settings.LOCAL_RETRIEVAL_MAX_QUOTE_CHARS,
            read_window=1,
        )
        cards = card_bundle.get("cards", [])
        evidence_items: List[Dict[str, Any]] = []
        for card in cards:
            evidence_items.append(
                {
                    "doc_id": str(card.get("doc_id", "")).strip(),
                    "quote": str(card.get("quote", "")).strip(),
                    "location": str(card.get("location", "")).strip(),
                    "analysis": str(card.get("analysis", "")).strip(),
                    "source_url": str(card.get("source_url", "")).strip() or None,
                    "source_title": str(card.get("source_title", "")).strip() or None,
                    "source_type": "comparison_document",
                }
            )

        if not evidence_items:
            return [], {
                "enabled": True,
                "fallback": "no_local_hits",
                "queries": queries,
                "doc_filters": doc_filters,
                "hit_chunks": [item.get("chunk_id") for item in reranked if item.get("chunk_id")],
                "selected_cards": [],
            }

        trace = {
            "enabled": True,
            "fallback": "",
            "queries": queries,
            "doc_filters": doc_filters,
            "hit_chunks": [item.get("chunk_id") for item in reranked if item.get("chunk_id")],
            "selected_cards": card_bundle.get("trace", {}).get("selected_candidates", []),
            "dropped_cards": card_bundle.get("trace", {}).get("dropped_candidates", []),
            "context_chars": card_bundle.get("trace", {}).get("context_chars", 0),
        }
        return evidence_items, trace

    def _to_external_evidence_items(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        evidence: List[Dict[str, Any]] =[]
        for item in candidates:
            item_dict = self._to_dict(item)
            doc_id = str(item_dict.get("doc_id", "")).strip()
            if not doc_id:
                continue
            source_type = str(item_dict.get("source_type", "")).strip()
            published = str(item_dict.get("published", "")).strip()
            location = source_type
            if published:
                location = f"{source_type} {published}".strip()

            # 清洗空白符和可能引起干扰的控制字符
            raw_snippet = str(item_dict.get("snippet", "")).strip()
            clean_snippet = re.sub(r'\s+', ' ', raw_snippet)
            clean_snippet = re.sub(r'[\x00-\x1f\x7f]', '', clean_snippet)

            evidence.append({
                "doc_id": doc_id,
                "quote": clean_snippet[:300],  # 放宽至300字符保留更多技术上下文
                "location": location,
                "analysis": "",
                "source_url": str(item_dict.get("url", "")).strip() or None,
                "source_title": str(item_dict.get("title", "")).strip() or None,
                "source_type": source_type or None,
            })
        return evidence

    def _build_comparison_docs(self, prepared_materials: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        docs: Dict[str, Dict[str, Any]] = {}
        for item in prepared_materials.get("comparison_documents", []) or[]:
            doc = self._to_dict(item)
            doc_id = str(doc.get("document_id", "")).strip()
            if not doc_id:
                continue
            docs[doc_id] = {
                "title": str(doc.get("document_number", "")).strip(),
                "location": f"{doc_id} ({doc.get('document_number', '')})",
                "content": self._extract_doc_content(doc),
            }
        return docs

    def _extract_doc_content(self, doc: Dict[str, Any]) -> str:
        is_patent = bool(doc.get("is_patent", False))
        data = doc.get("data")
        if is_patent:
            data_dict = self._to_dict(data)
            desc = self._to_dict(data_dict.get("description", {}))
            return str(desc.get("detailed_description", "")).strip()
        if isinstance(data, str):
            return data.strip()
        if isinstance(data, dict):
            return json.dumps(data, ensure_ascii=False)
        return ""

    def _normalize_claims(self, claims_structured: List[Any]) -> List[Dict[str, Any]]:
        return [self._to_dict(claim) for claim in (claims_structured or []) if self._to_dict(claim)]

    def _extract_priority_date(self, prepared_materials: Dict[str, Any]) -> str:
        original_patent = self._to_dict(prepared_materials.get("original_patent", {}))
        patent_data = self._to_dict(original_patent.get("data", {}))
        bibliographic_data = self._to_dict(patent_data.get("bibliographic_data", {}))

        candidates =[
            patent_data.get("priority_date"),
            patent_data.get("application_date"),
            bibliographic_data.get("priority_date"),
            bibliographic_data.get("application_date"),
        ]
        for date_str in candidates:
            normalized = self._normalize_date(date_str)
            if normalized:
                return normalized
        return ""

    def _normalize_date(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""

        patterns =[
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
            try:
                datetime.strptime(f"{year}-{month}-{day}", "%Y-%m-%d")
                return f"{year}-{month}-{day}"
            except Exception:
                continue
        return ""

    def _get_claim_text(self, claim_ids: List[str], claims: List[Dict[str, Any]]) -> str:
        texts: List[str] = []
        for claim_id in claim_ids:
            try:
                idx = int(claim_id) - 1
            except Exception:
                continue
            if 0 <= idx < len(claims):
                text = str(claims[idx].get("claim_text", "")).strip()
                if text:
                    texts.append(f"权利要求{claim_id}: {text}")
        return "\n".join(texts)

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
