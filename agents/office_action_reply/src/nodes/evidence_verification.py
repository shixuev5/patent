"""
证据核查节点
基于 prepared_materials 中的原权利要求与对比文件内容，对事实争议进行核查
"""

import json
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from loguru import logger

from agents.common.retrieval import retrieve_segments
from agents.common.utils.llm import get_llm_service
from agents.office_action_reply.src.state import EvidenceAssessment
from agents.office_action_reply.src.utils import get_node_cache


class EvidenceVerificationNode:
    """证据核查节点（新结构）"""

    def __init__(self, config=None):
        self.config = config
        self.llm_service = get_llm_service()

    def __call__(self, state):
        logger.info("开始证据核查")
        updates = {}

        try:
            retrieval_session_id = (
                str(self._state_get(state, "retrieval_session_id", "")).strip()
                or str(self._state_get(state, "task_id", "")).strip()
            )

            cache = get_node_cache(self.config, "evidence_verification")
            assessments = cache.run_step(
                "verify_evidence_v4",
                self._verify_evidence,
                self._state_get(state, "disputes", []),
                self._state_get(state, "prepared_materials", {}),
                retrieval_session_id,
            )

            if not assessments:
                logger.info("没有需要进行事实核查的争议项")
                return updates

            updates["evidence_assessments"] = [
                item if isinstance(item, EvidenceAssessment) else EvidenceAssessment(**item)
                for item in assessments
            ]
            if retrieval_session_id:
                updates["retrieval_session_id"] = retrieval_session_id
            logger.info(f"完成 {len(assessments)} 个争议项的事实核查")

        except Exception as e:
            logger.error(f"证据核查节点执行失败: {e}")
            updates["errors"] = [{
                "node_name": "evidence_verification",
                "error_message": str(e),
                "error_type": "evidence_verification_error",
            }]

        return updates

    def _verify_evidence(self, disputes, prepared_materials, retrieval_session_id: str):
        document_disputes = self._get_document_based_disputes(disputes)
        if not document_disputes:
            return []

        prepared = self._to_dict(prepared_materials)
        claims = self._extract_claims(prepared)
        comparison_doc_map = self._build_comparison_doc_map(prepared)
        retrieval_inputs = self._build_retrieval_inputs(comparison_doc_map)
        grouped_disputes = self._group_disputes_by_docs(document_disputes)

        assessments = []
        session_seeded = False

        for doc_group, group_items in grouped_disputes.items():
            missing_doc_ids = self._get_missing_doc_ids(doc_group, comparison_doc_map)
            for dispute in group_items:
                upsert_inputs = retrieval_inputs if (retrieval_session_id and not session_seeded) else []

                assessment = self._verify_single_dispute(
                    dispute=dispute,
                    claims=claims,
                    doc_group=doc_group,
                    missing_doc_ids=missing_doc_ids,
                    retrieval_session_id=retrieval_session_id,
                    retrieval_inputs=upsert_inputs,
                )
                if upsert_inputs:
                    session_ready = bool(
                        self._to_dict(assessment.get("trace", {}).get("retrieval", {}).get("session_retrieval", {}))
                        .get("session_ready", False)
                    )
                    session_seeded = session_ready
                assessments.append(assessment)

        return assessments

    def _get_document_based_disputes(self, disputes: List[Any]) -> List[Dict[str, Any]]:
        document_disputes: List[Dict[str, Any]] = []
        for item in disputes or []:
            dispute = self._to_dict(item)
            examiner_opinion = self._to_dict(dispute.get("examiner_opinion", {}))
            dispute_type = str(examiner_opinion.get("type", "")).strip()
            if dispute_type in {"document_based", "mixed_basis"}:
                document_disputes.append(dispute)
        return document_disputes

    def _extract_claims(self, prepared_materials: Dict[str, Any]) -> List[Dict[str, Any]]:
        original_patent = self._to_dict(prepared_materials.get("original_patent", {}))
        patent_data = self._to_dict(original_patent.get("data", {}))
        claims = patent_data.get("claims", [])
        if not isinstance(claims, list):
            return []
        return [self._to_dict(claim) for claim in claims]

    def _build_comparison_doc_map(self, prepared_materials: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        comparison_doc_map: Dict[str, Dict[str, Any]] = {}
        for item in prepared_materials.get("comparison_documents", []) or []:
            doc = self._to_dict(item)
            doc_id = str(doc.get("document_id", "")).strip()
            if doc_id:
                comparison_doc_map[doc_id] = doc
        return comparison_doc_map

    def _build_retrieval_inputs(self, comparison_doc_map: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        inputs: List[Dict[str, Any]] = []
        for doc_id, doc in comparison_doc_map.items():
            content = self._extract_doc_content(doc)
            if not content:
                continue
            source_type = "patent" if bool(doc.get("is_patent", False)) else "non_patent"
            inputs.append({
                "doc_id": doc_id,
                "source_type": source_type,
                "title": str(doc.get("document_number", "")).strip(),
                "markdown": content,
            })
        return inputs

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
            grouped[tuple(normalized_ids)].append(dispute)
        return grouped

    def _get_missing_doc_ids(
        self,
        doc_group: Tuple[str, ...],
        comparison_doc_map: Dict[str, Dict[str, Any]],
    ) -> List[str]:
        missing_doc_ids: List[str] = []
        for doc_id in doc_group:
            doc = comparison_doc_map.get(doc_id)
            if not doc:
                missing_doc_ids.append(doc_id)
                continue
            content = self._extract_doc_content(doc)
            if not content:
                missing_doc_ids.append(doc_id)
        return missing_doc_ids

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

    def _build_prefix_messages(self, retrieved_hits: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": self._build_system_prompt()},
        ]

        if not retrieved_hits:
            messages.append({
                "role": "user",
                "content": "当前争议项未检索到可用对比文件证据片段。请在输出中给出 INCONCLUSIVE。",
            })
            return messages

        for hit in retrieved_hits:
            location = str(hit.get("location", "")).strip()
            messages.append({
                "role": "user",
                "content": (
                    f"证据片段：{hit['doc_id']}\n"
                    f"标题: {hit.get('title', '')}\n"
                    f"位置: {location}\n"
                    f"片段: {hit.get('excerpt', '')}"
                ),
            })

        return messages

    def _build_system_prompt(self) -> str:
        return """你是专利事实核查专家。你的任务是判断“申请人的事实主张是否属实”。

判定标准：
1. 仅基于给定的对比文件内容，不得使用外部知识。
2. 若对比文件明确公开争议特征，应倾向 EXAMINER_CORRECT。
3. 若对比文件未公开或无法支持审查员断言，应倾向 APPLICANT_CORRECT。
4. 若证据不足或文档缺失，应输出 INCONCLUSIVE。

输出要求：
1. 只输出 JSON 对象，不得输出额外文本。
2. 使用以下格式：
{
  "assessment": {
    "verdict": "APPLICANT_CORRECT",
    "reasoning": "判断理由",
    "confidence": 0.82,
    "examiner_rejection_reason": "当裁决偏向申请人时，仍可支持审查员维持驳回的说理理由"
  },
  "evidence": [
    {
      "doc_id": "D1",
      "quote": "证据原文",
      "location": "位置描述",
      "analysis": "该证据如何支持结论"
    }
  ]
}

字段约束：
- verdict 只能是 APPLICANT_CORRECT / EXAMINER_CORRECT / INCONCLUSIVE。
- confidence 必须是 0~1 之间数字。
- 若 verdict=APPLICANT_CORRECT，examiner_rejection_reason 必须给出具体且有说服力的驳回说理；否则留空字符串。
- evidence.doc_id 必须是当前争议项 supporting_docs 中出现的 doc_id 之一。

examiner_rejection_reason 口吻与内容约束（强制）：
- 该字段将直接拼接进 second_office_action_notice.text，必须写成“审查意见通知书正文口吻”，面向申请人。
- 必须使用确定性陈述，不得写策略建议或元话术。
- 禁止使用：审查员可主张、可认为、可以、建议、应补充、如需、若…则…。
- 建议用法：以“经审查认为…”“本局认为…”开头，随后写明证据链与驳回结论。

示例：
- 合格示例：经审查认为，对比文件D1已公开……，对比文件D2进一步公开……，本领域技术人员据此能够得到该区别技术特征，故权利要求1相对于D1结合D2不具备显著进步。
- 不合格示例：审查员可主张D1和D2可以结合，建议补充证据后维持驳回。"""

    def _verify_single_dispute(
        self,
        dispute: Dict[str, Any],
        claims: List[Dict[str, Any]],
        doc_group: Tuple[str, ...],
        missing_doc_ids: List[str],
        retrieval_session_id: str,
        retrieval_inputs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        claim_text = self._get_claim_text(dispute, claims)
        examiner_opinion = self._to_dict(dispute.get("examiner_opinion", {}))
        applicant_opinion = self._to_dict(dispute.get("applicant_opinion", {}))

        query_text = self._build_retrieval_query(dispute, claim_text, examiner_opinion, applicant_opinion)
        retrieval_filters: Dict[str, Any] = {"sources": ["patent", "non_patent"]}
        if doc_group:
            retrieval_filters["doc_ids"] = list(doc_group)

        retrieved_hits: List[Dict[str, Any]] = []
        session_ready = False
        if retrieval_session_id and query_text:
            try:
                raw_hits = retrieve_segments(
                    query_text=query_text,
                    inputs=retrieval_inputs,
                    mode="session",
                    session_id=retrieval_session_id,
                    top_n=24,
                    top_k=8,
                    filters=retrieval_filters,
                )
                retrieved_hits = self._normalize_retrieval_hits(raw_hits)
                session_ready = True
            except Exception as ex:
                logger.warning(f"事实核查检索失败，将回退到无证据片段模式: {ex}")

        prefix_messages = self._build_prefix_messages(retrieved_hits)
        dispute_prompt = f"""请核查以下争议项：
original_claim_id: {dispute.get("original_claim_id", "")}
claim_text: {claim_text}
feature_text: {dispute.get("feature_text", "")}
examiner_opinion: {json.dumps(examiner_opinion, ensure_ascii=False)}
applicant_opinion: {json.dumps(applicant_opinion, ensure_ascii=False)}
supporting_docs_doc_ids: {json.dumps(list(doc_group), ensure_ascii=False)}
missing_doc_ids: {json.dumps(missing_doc_ids, ensure_ascii=False)}
retrieval_query: {query_text}
"""
        messages = prefix_messages + [{"role": "user", "content": dispute_prompt}]

        response = self.llm_service.chat_completion_json(
            messages,
            temperature=0.05,
            thinking=True,
        )
        parsed = self._normalize_llm_output(response, set(doc_group))

        original_claim_id = str(dispute.get("original_claim_id", "")).strip()
        feature_text = str(dispute.get("feature_text", "")).strip()
        used_doc_ids = []
        for hit in retrieved_hits:
            doc_id = str(hit.get("doc_id", "")).strip()
            if doc_id and doc_id not in used_doc_ids:
                used_doc_ids.append(doc_id)
        if not used_doc_ids:
            used_doc_ids = list(doc_group)

        return {
            "dispute_id": str(dispute.get("dispute_id", f"{original_claim_id}_{feature_text[:30]}")),
            "original_claim_id": original_claim_id,
            "claim_text": claim_text,
            "feature_text": feature_text,
            "examiner_opinion": examiner_opinion,
            "applicant_opinion": applicant_opinion,
            "assessment": parsed["assessment"],
            "evidence": parsed["evidence"],
            "trace": {
                "used_doc_ids": used_doc_ids,
                "missing_doc_ids": list(missing_doc_ids),
                "retrieval": {
                    "session_retrieval": {
                        "queries": [query_text] if query_text else [],
                        "filters": retrieval_filters,
                        "result_count": len(retrieved_hits),
                        "session_ready": session_ready,
                        "results": self._build_retrieval_trace_results(retrieved_hits),
                    }
                },
            },
        }

    def _build_retrieval_query(
        self,
        dispute: Dict[str, Any],
        claim_text: str,
        examiner_opinion: Dict[str, Any],
        applicant_opinion: Dict[str, Any],
    ) -> str:
        parts = [
            str(dispute.get("feature_text", "")).strip(),
            claim_text[:200],
            str(examiner_opinion.get("reasoning", "")).strip()[:200],
            str(applicant_opinion.get("core_conflict", "")).strip()[:120],
        ]
        return " ".join(part for part in parts if part).strip()

    def _normalize_retrieval_hits(self, raw_hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        hits: List[Dict[str, Any]] = []
        for item in raw_hits or []:
            hit = self._to_dict(item)
            doc_id = str(hit.get("doc_id", "")).strip()
            if not doc_id:
                continue
            heading_path = str(hit.get("heading_path", "")).strip()
            para_id = str(hit.get("para_id", "")).strip()
            page = hit.get("page")
            location_parts = [heading_path, para_id]
            if page is not None:
                location_parts.append(f"page={page}")
            location = " / ".join(part for part in location_parts if part)

            hits.append({
                "doc_id": doc_id,
                "excerpt": str(hit.get("excerpt", "")).strip()[:650],
                "title": str(hit.get("title", "")).strip(),
                "source_type": str(hit.get("source_type", "")).strip(),
                "location": location,
                "score": float(hit.get("score", 0.0) or 0.0),
            })
        return hits

    def _build_retrieval_trace_results(self, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for hit in hits:
            results.append({
                "doc_id": hit.get("doc_id"),
                "source_type": hit.get("source_type", ""),
                "title": hit.get("title", ""),
                "url": None,
                "published": None,
                "similarity_score": hit.get("score"),
            })
        return results

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
        if "examiner_rejection_reason" not in assessment:
            raise ValueError("evidence_verification 输出缺少 assessment.examiner_rejection_reason")
        rejection_reason = str(assessment.get("examiner_rejection_reason", "")).strip()
        if verdict == "APPLICANT_CORRECT" and not rejection_reason:
            raise ValueError("evidence_verification 输出非法: verdict=APPLICANT_CORRECT 时 examiner_rejection_reason 不能为空")
        if verdict != "APPLICANT_CORRECT":
            rejection_reason = ""

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
                "examiner_rejection_reason": rejection_reason,
            },
            "evidence": evidence_items,
        }

    def _get_claim_text(self, dispute: Dict[str, Any], claims: List[Dict[str, Any]]) -> str:
        claim_id = str(dispute.get("original_claim_id", "")).strip()
        try:
            index = int(claim_id) - 1
            if 0 <= index < len(claims):
                return str(claims[index].get("claim_text", "")).strip()
        except Exception:
            pass
        return ""

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
