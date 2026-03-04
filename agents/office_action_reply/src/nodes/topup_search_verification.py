"""
补充检索与评判节点
针对新增特征执行对比文件深扫与外部检索，并由大模型给出最终裁决
"""

import json
import os
import re
from typing import Any, Dict, List, Set, Tuple

import requests
from loguru import logger

from agents.common.utils.llm import get_llm_service
from agents.office_action_reply.src.state import Dispute, EvidenceAssessment
from agents.office_action_reply.src.utils import get_node_cache


class TopupSearchVerificationNode:
    """补充检索与评判节点（LLM主判）"""

    def __init__(self, config=None):
        self.config = config
        self.serpapi_key = os.getenv("SERPAPI_API_KEY")
        self.base_url = os.getenv("SERPAPI_BASE_URL", "https://serpapi.com/search")
        self.llm_service = get_llm_service()

    def __call__(self, state):
        logger.info("开始补充检索与评判")
        updates = {}

        try:
            topup_tasks = self._state_get(state, "topup_tasks", []) or []
            if not topup_tasks:
                logger.info("无补充检索任务，跳过")
                return updates

            cache = get_node_cache(self.config, "topup_search_verification")
            result = cache.run_step(
                "verify_topup_v3",
                self._verify_topup,
                topup_tasks,
                self._state_get(state, "prepared_materials", {}),
                self.serpapi_key,
                self.base_url,
            )

            updates["disputes"] = [
                item if isinstance(item, Dispute) else Dispute(**item)
                for item in result.get("disputes", [])
            ]
            updates["evidence_assessments"] = [
                item if isinstance(item, EvidenceAssessment) else EvidenceAssessment(**item)
                for item in result.get("evidence_assessments", [])
            ]
            logger.info(
                f"补充检索完成，新增争议项: {len(updates.get('disputes', []))}，核查结果: {len(updates.get('evidence_assessments', []))}"
            )
        except Exception as e:
            logger.error(f"补充检索与评判失败: {e}")
            updates["errors"] = [{
                "node_name": "topup_search_verification",
                "error_message": str(e),
                "error_type": "topup_search_verification_error",
            }]

        return updates

    def _verify_topup(self, topup_tasks, prepared_materials, serpapi_key: str, base_url: str):
        tasks = [self._to_dict(item) for item in (topup_tasks or [])]
        prepared = self._to_dict(prepared_materials)

        claims = self._extract_claims(prepared)
        comparison_docs = self._build_comparison_docs(prepared)

        disputes = []
        assessments = []
        for task in tasks:
            dispute, assessment = self._evaluate_task(task, claims, comparison_docs, serpapi_key, base_url)
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
        serpapi_key: str,
        base_url: str,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        task_id = str(task.get("task_id", "")).strip() or "New_FX"
        claim_id = str(task.get("original_claim_id", "")).strip() or "1"
        feature_text = str(task.get("feature_text", "")).strip()
        dispute_id = f"TOPUP_{task_id}"
        claim_text = self._get_claim_text(claim_id, claims)

        local_evidence = self._scan_comparison_docs(feature_text, comparison_docs)
        external_queries = [f"{feature_text} 专利 技术公开", f"{feature_text} 本领域公知"]
        external_evidence = self._search_external(feature_text, external_queries, serpapi_key, base_url) if serpapi_key else []

        evidence_context = local_evidence + external_evidence
        allowed_doc_ids: Set[str] = {str(item.get("doc_id", "")).strip() for item in evidence_context if item.get("doc_id")}
        allowed_doc_ids.add("MODEL")
        evidence_map = {
            str(item.get("doc_id", "")).strip(): item
            for item in evidence_context
            if item.get("doc_id")
        }

        messages = [
            {"role": "system", "content": self._build_system_prompt()},
            {
                "role": "user",
                "content": self._build_user_prompt(
                    task=task,
                    claim_id=claim_id,
                    claim_text=claim_text,
                    feature_text=feature_text,
                    local_evidence=local_evidence,
                    external_evidence=external_evidence,
                    external_queries=external_queries,
                ),
            },
        ]
        response = self.llm_service.chat_completion_json(
            messages,
            temperature=0.05,
            thinking=True,
        )
        parsed = self._normalize_llm_output(response, allowed_doc_ids, evidence_map)

        examiner_opinion = parsed["examiner_opinion"]
        applicant_opinion = parsed["applicant_opinion"]
        dispute = {
            "dispute_id": dispute_id,
            "original_claim_id": claim_id,
            "feature_text": feature_text,
            "examiner_opinion": examiner_opinion,
            "applicant_opinion": applicant_opinion,
        }
        assessment = {
            "dispute_id": dispute_id,
            "original_claim_id": claim_id,
            "claim_text": claim_text,
            "feature_text": feature_text,
            "examiner_opinion": examiner_opinion,
            "applicant_opinion": applicant_opinion,
            "assessment": parsed["assessment"],
            "evidence": parsed["evidence"],
            "trace": {
                "used_doc_ids": parsed["used_doc_ids"],
                "missing_doc_ids": [],
                "retrieval_queries": external_queries,
                "retrieval_engine": "serpapi" if serpapi_key else "none",
            },
        }
        return dispute, assessment

    def _build_system_prompt(self) -> str:
        return """你是专利补充检索审查专家。你需要基于给定证据判断：新增特征是否已被公开或是否足以支持维持驳回。

任务：
1. 生成 examiner_opinion（审查员观点）与 applicant_opinion（申请人观点）。
2. 给出 assessment（APPLICANT_CORRECT / EXAMINER_CORRECT / INCONCLUSIVE）。
3. 给出 evidence（引用证据项），证据优先使用给定 doc_id（D* / EXT*）；若无外部证据可用，可使用 MODEL。

输出结构（仅 JSON）：
{
  "examiner_opinion": {
    "type": "document_based",
    "supporting_docs": [
      {
        "doc_id": "D1",
        "cited_text": "证据片段"
      }
    ],
    "reasoning": "审查员观点"
  },
  "applicant_opinion": {
    "type": "fact_dispute",
    "reasoning": "申请人观点",
    "core_conflict": "核心冲突"
  },
  "assessment": {
    "verdict": "INCONCLUSIVE",
    "reasoning": "裁决理由",
    "confidence": 0.62,
    "examiner_rejection_reason": "当裁决偏向申请人时，仍可支持审查员维持驳回的说理理由"
  },
  "evidence": [
    {
      "doc_id": "D1",
      "quote": "证据片段",
      "location": "位置描述",
      "analysis": "证据分析",
      "source_url": "https://...",
      "source_title": "标题",
      "source_type": "comparison_document"
    }
  ]
}

约束：
- examiner_opinion.type 只能是 document_based、common_knowledge_based、mixed_basis。
- applicant_opinion.type 固定为 fact_dispute。
- assessment.verdict 只能是 APPLICANT_CORRECT / EXAMINER_CORRECT / INCONCLUSIVE。
- 若 assessment.verdict=APPLICANT_CORRECT，assessment.examiner_rejection_reason 必须给出具体且有说服力的驳回说理；否则留空字符串。
- 当 examiner_opinion.type 为 document_based/mixed_basis 时，supporting_docs 必须至少包含一个 doc_id。
- 当 examiner_opinion.type 为 common_knowledge_based 时，supporting_docs 必须为空数组。
- evidence.doc_id 必须来自给定证据 doc_id 列表或 MODEL。

examiner_rejection_reason 口吻与内容约束（强制）：
- 该字段将直接拼接进 second_office_action_notice.text，必须写成“审查意见通知书正文口吻”，面向申请人。
- 必须使用确定性陈述，不得写策略建议或元话术。
- 禁止使用：审查员可主张、可认为、可以、建议、应补充、如需、若…则…。

示例：
- 合格示例：经审查认为，现有技术已公开……，并且将……应用于……属于本领域技术人员的常规技术选择，故该新增特征不足以克服现有创造性缺陷。
- 不合格示例：审查员可主张该特征可能显而易见，建议进一步补证后维持驳回。"""

    def _build_user_prompt(
        self,
        task: Dict[str, Any],
        claim_id: str,
        claim_text: str,
        feature_text: str,
        local_evidence: List[Dict[str, Any]],
        external_evidence: List[Dict[str, Any]],
        external_queries: List[str],
    ) -> str:
        return f"""请评判以下补充检索任务：

【任务】
{json.dumps(task, ensure_ascii=False, indent=2)}

【权利要求】
original_claim_id: {claim_id}
claim_text: {claim_text}
feature_text: {feature_text}

【本地对比文件证据（D*）】
{json.dumps(local_evidence, ensure_ascii=False, indent=2)}

【外部检索证据（EXT*）】
{json.dumps(external_evidence, ensure_ascii=False, indent=2)}

【外部检索查询】
{json.dumps(external_queries, ensure_ascii=False)}"""

    def _normalize_llm_output(
        self,
        response: Dict[str, Any],
        allowed_doc_ids: Set[str],
        evidence_map: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        output = self._to_dict(response)

        examiner_opinion = self._to_dict(output.get("examiner_opinion", {}))
        examiner_type = str(examiner_opinion.get("type", "")).strip()
        if examiner_type not in {"document_based", "common_knowledge_based", "mixed_basis"}:
            raise ValueError(f"topup_search_verification 输出非法 examiner_opinion.type: {examiner_type}")

        supporting_docs_raw = examiner_opinion.get("supporting_docs", [])
        if not isinstance(supporting_docs_raw, list):
            raise ValueError("topup_search_verification 输出非法 supporting_docs，必须为列表")
        supporting_docs = []
        for item in supporting_docs_raw:
            item_dict = self._to_dict(item)
            doc_id = str(item_dict.get("doc_id", "")).strip()
            if not doc_id:
                continue
            if doc_id not in allowed_doc_ids or doc_id == "MODEL":
                raise ValueError(f"topup_search_verification 输出非法 supporting_docs.doc_id: {doc_id}")
            supporting_docs.append({
                "doc_id": doc_id,
                "cited_text": str(item_dict.get("cited_text", "")).strip(),
            })

        deduped_supporting_docs = []
        seen_doc_ids = set()
        for item in supporting_docs:
            doc_id = item["doc_id"]
            if doc_id in seen_doc_ids:
                continue
            seen_doc_ids.add(doc_id)
            deduped_supporting_docs.append(item)
        supporting_docs = deduped_supporting_docs

        if examiner_type in {"document_based", "mixed_basis"} and not supporting_docs:
            raise ValueError("topup_search_verification 输出非法: document_based/mixed_basis 时 supporting_docs 不能为空")
        if examiner_type == "common_knowledge_based":
            supporting_docs = []

        applicant_opinion = self._to_dict(output.get("applicant_opinion", {}))
        applicant_type = str(applicant_opinion.get("type", "")).strip()
        if applicant_type not in {"fact_dispute", "logic_dispute"}:
            raise ValueError(f"topup_search_verification 输出非法 applicant_opinion.type: {applicant_type}")

        assessment = self._to_dict(output.get("assessment", {}))
        verdict = str(assessment.get("verdict", "")).strip()
        if verdict not in {"APPLICANT_CORRECT", "EXAMINER_CORRECT", "INCONCLUSIVE"}:
            raise ValueError(f"topup_search_verification 输出非法 verdict: {verdict}")

        confidence = assessment.get("confidence", 0.0)
        try:
            confidence = float(confidence)
        except Exception as e:
            raise ValueError(f"topup_search_verification 输出非法 confidence: {confidence}") from e
        if confidence < 0.0 or confidence > 1.0:
            raise ValueError(f"topup_search_verification 输出非法 confidence 范围: {confidence}")

        reasoning = str(assessment.get("reasoning", "")).strip()
        if "examiner_rejection_reason" not in assessment:
            raise ValueError("topup_search_verification 输出缺少 assessment.examiner_rejection_reason")
        rejection_reason = str(assessment.get("examiner_rejection_reason", "")).strip()
        if verdict == "APPLICANT_CORRECT" and not rejection_reason:
            raise ValueError("topup_search_verification 输出非法: verdict=APPLICANT_CORRECT 时 examiner_rejection_reason 不能为空")
        if verdict != "APPLICANT_CORRECT":
            rejection_reason = ""

        evidence_items = []
        used_doc_ids: List[str] = []
        evidence_raw = output.get("evidence", [])
        if not isinstance(evidence_raw, list):
            raise ValueError("topup_search_verification 输出非法 evidence，必须为列表")
        for item in evidence_raw:
            evidence = self._to_dict(item)
            doc_id = str(evidence.get("doc_id", "")).strip()
            if not doc_id or doc_id not in allowed_doc_ids:
                raise ValueError(f"topup_search_verification 输出非法 evidence.doc_id: {doc_id}")

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
                "examiner_rejection_reason": rejection_reason,
            },
            "evidence": evidence_items,
            "used_doc_ids": used_doc_ids,
        }

    def _scan_comparison_docs(self, feature_text: str, comparison_docs: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        tokens = [token for token in re.split(r"[\s,，。；;:：、（）()\[\]{}]+", feature_text) if len(token) >= 2][:8]
        evidence_items: List[Dict[str, Any]] = []

        for doc_id, doc in comparison_docs.items():
            content = str(doc.get("content", ""))
            if not content:
                continue

            first_hit = ""
            for token in tokens:
                if token in content:
                    first_hit = token
                    break
            if not first_hit:
                continue

            evidence_items.append({
                "doc_id": doc_id,
                "quote": self._extract_snippet(content, first_hit),
                "location": doc.get("location", ""),
                "analysis": "",
                "source_url": None,
                "source_title": doc.get("title", ""),
                "source_type": "comparison_document",
            })
            if len(evidence_items) >= 6:
                break

        return evidence_items

    def _extract_snippet(self, content: str, token: str) -> str:
        index = content.find(token)
        if index < 0:
            return content[:240]
        start = max(index - 90, 0)
        end = min(index + 160, len(content))
        return content[start:end].strip()

    def _search_external(self, feature_text: str, queries: List[str], serpapi_key: str, base_url: str):
        evidence = []
        ext_index = 1
        for query in queries:
            params = {
                "engine": "google",
                "q": query,
                "api_key": serpapi_key,
                "num": 5,
            }
            try:
                response = requests.get(base_url, params=params, timeout=25)
                response.raise_for_status()
                data = response.json()
            except Exception as e:
                logger.warning(f"外部检索失败: {e}")
                continue

            for item in data.get("organic_results", []) or []:
                title = str(item.get("title", "")).strip()
                snippet = str(item.get("snippet", "")).strip()
                url = str(item.get("link", "")).strip()
                if not (title or snippet):
                    continue
                evidence.append({
                    "doc_id": f"EXT{ext_index}",
                    "quote": snippet[:240],
                    "location": "google organic result",
                    "analysis": "",
                    "source_url": url or None,
                    "source_title": title or None,
                    "source_type": "google_web",
                })
                ext_index += 1
                if len(evidence) >= 6:
                    return evidence
        return evidence

    def _build_comparison_docs(self, prepared_materials: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        docs: Dict[str, Dict[str, Any]] = {}
        for item in prepared_materials.get("comparison_documents", []) or []:
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

    def _extract_claims(self, prepared_materials: Dict[str, Any]) -> List[Dict[str, Any]]:
        original_patent = self._to_dict(prepared_materials.get("original_patent", {}))
        patent_data = self._to_dict(original_patent.get("data", {}))
        claims = patent_data.get("claims", [])
        if not isinstance(claims, list):
            return []
        return [self._to_dict(claim) for claim in claims]

    def _get_claim_text(self, claim_id: str, claims: List[Dict[str, Any]]) -> str:
        try:
            idx = int(claim_id) - 1
            if 0 <= idx < len(claims):
                return str(claims[idx].get("claim_text", "")).strip()
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
