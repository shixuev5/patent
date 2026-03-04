"""
公知常识核查节点
基于外部检索（优先）与模型知识（次级）对逻辑争议进行核查
"""

import json
import os
import re
from typing import Any, Dict, List, Optional, Set

import requests
from loguru import logger

from agents.common.utils.llm import get_llm_service
from agents.office_action_reply.src.state import EvidenceAssessment
from agents.office_action_reply.src.utils import get_node_cache


class CommonKnowledgeVerificationNode:
    """公知常识核查节点（输出结构与 EvidenceVerificationNode 对齐）"""

    def __init__(self, config=None):
        self.config = config
        self.llm_service = get_llm_service()
        self.serpapi_key = os.getenv("SERPAPI_API_KEY")
        self.base_url = os.getenv("SERPAPI_BASE_URL", "https://serpapi.com/search")

    def __call__(self, state):
        logger.info("开始公知常识核查")
        updates = {}

        try:
            cache = get_node_cache(self.config, "common_knowledge_verification")
            assessments = cache.run_step(
                "verify_common_knowledge",
                self._verify_common_knowledge,
                self._state_get(state, "disputes", []),
                self._state_get(state, "prepared_materials", {}),
                self.serpapi_key,
                self.base_url,
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
        serpapi_key: Optional[str],
        base_url: str,
    ) -> List[Dict[str, Any]]:
        logic_disputes = self._get_logic_disputes(disputes)
        if not logic_disputes:
            return []

        prepared = self._to_dict(prepared_materials)
        claims = self._extract_claims(prepared)
        priority_date = self._extract_priority_date(prepared)

        assessments: List[Dict[str, Any]] = []
        for dispute in logic_disputes:
            claim_text = self._get_claim_text(dispute, claims)
            queries = self._build_search_queries(dispute, claim_text)

            external_evidence: List[Dict[str, Any]] = []
            if serpapi_key:
                external_evidence = self._search_external_sources(
                    queries=queries,
                    priority_date=priority_date,
                    serpapi_key=serpapi_key,
                    base_url=base_url,
                )
            else:
                logger.warning("SERPAPI_API_KEY 未配置，将仅基于模型知识进行低置信度判断")

            assessment = self._verify_single_dispute(
                dispute=dispute,
                claim_text=claim_text,
                queries=queries,
                priority_date=priority_date,
                external_evidence=external_evidence,
            )
            assessments.append(assessment)

        return assessments

    def _get_logic_disputes(self, disputes: List[Any]) -> List[Dict[str, Any]]:
        logic_disputes: List[Dict[str, Any]] = []
        for item in disputes or []:
            dispute = self._to_dict(item)
            applicant_opinion = self._to_dict(dispute.get("applicant_opinion", {}))
            if applicant_opinion.get("type") == "logic_dispute":
                logic_disputes.append(dispute)
        return logic_disputes

    def _extract_claims(self, prepared_materials: Dict[str, Any]) -> List[Dict[str, Any]]:
        original_patent = self._to_dict(prepared_materials.get("original_patent", {}))
        patent_data = self._to_dict(original_patent.get("data", {}))
        claims = patent_data.get("claims", [])
        if not isinstance(claims, list):
            return []
        return [self._to_dict(claim) for claim in claims]

    def _get_claim_text(self, dispute: Dict[str, Any], claims: List[Dict[str, Any]]) -> str:
        claim_id = str(dispute.get("original_claim_id", "")).strip()
        try:
            index = int(claim_id) - 1
            if 0 <= index < len(claims):
                return str(claims[index].get("claim_text", "")).strip()
        except Exception:
            pass
        return ""

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

    def _build_search_queries(self, dispute: Dict[str, Any], claim_text: str) -> List[str]:
        feature_text = str(dispute.get("feature_text", "")).strip()
        examiner_opinion = self._to_dict(dispute.get("examiner_opinion", {}))
        applicant_opinion = self._to_dict(dispute.get("applicant_opinion", {}))

        queries = [
            f"{feature_text} 本领域公知常识",
            f"{feature_text} 技术手段 常见实现",
            f"{feature_text} {examiner_opinion.get('reasoning', '')}".strip(),
            f"{feature_text} {applicant_opinion.get('core_conflict', '')}".strip(),
            f"{feature_text} {claim_text[:120]}".strip(),
        ]

        deduped: List[str] = []
        for query in queries:
            normalized = " ".join(str(query).split())
            if normalized and normalized not in deduped:
                deduped.append(normalized)
        return deduped[:4]

    def _search_external_sources(
        self,
        queries: List[str],
        priority_date: Optional[str],
        serpapi_key: str,
        base_url: str,
    ) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        for query in queries:
            candidates.extend(self._search_serpapi("google_scholar", query, priority_date, serpapi_key, base_url))
            candidates.extend(self._search_serpapi("google_patents", query, priority_date, serpapi_key, base_url))
            candidates.extend(self._search_serpapi("google", query, priority_date, serpapi_key, base_url))

        merged = self._dedupe_external_results(candidates)
        for idx, item in enumerate(merged[:8], start=1):
            item["doc_id"] = f"EXT{idx}"
        return merged[:8]

    def _search_serpapi(
        self,
        engine: str,
        query: str,
        priority_date: Optional[str],
        serpapi_key: str,
        base_url: str,
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "engine": engine,
            "q": query,
            "api_key": serpapi_key,
            "num": 5,
        }

        if engine == "google_scholar" and priority_date:
            params["as_ylo"] = "1900"
            params["as_yhi"] = priority_date[:4]
        elif engine == "google_patents" and priority_date:
            params["q"] = f"{query} before:{priority_date}"
        elif engine == "google" and priority_date:
            params["tbs"] = f"cdr:1,cd_min:1/1/1900,cd_max:{priority_date[5:7]}/{priority_date[8:10]}/{priority_date[:4]}"

        try:
            response = requests.get(base_url, params=params, timeout=25)
            response.raise_for_status()
            data = response.json()
            return self._parse_serpapi_results(engine, data)
        except Exception as e:
            logger.warning(f"SERPAPI {engine} 检索失败，query={query[:100]} error={e}")
            return []

    def _parse_serpapi_results(self, engine: str, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        source_type_map = {
            "google_scholar": "google_scholar",
            "google_patents": "google_patents",
            "google": "google_web",
        }
        source_type = source_type_map.get(engine, "external")

        parsed: List[Dict[str, Any]] = []
        for item in data.get("organic_results", []) or []:
            title = str(item.get("title", "")).strip()
            url = str(item.get("link", "")).strip()
            snippet = str(item.get("snippet", "")).strip()
            published = ""

            if engine == "google_scholar":
                publication_info = self._to_dict(item.get("publication_info", {}))
                published = str(publication_info.get("year", "")).strip()
            elif engine == "google_patents":
                published = str(item.get("priority_date") or item.get("publication_date") or "").strip()
            else:
                published = str(item.get("date", "")).strip()

            if not any([title, url, snippet]):
                continue

            parsed.append({
                "source_type": source_type,
                "title": title,
                "url": url,
                "snippet": snippet,
                "published": published,
            })

        return parsed

    def _dedupe_external_results(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen: Set[str] = set()
        merged: List[Dict[str, Any]] = []

        for item in results:
            url = str(item.get("url", "")).strip()
            title = str(item.get("title", "")).strip()
            snippet = str(item.get("snippet", "")).strip()
            key = url or f"{title}::{snippet[:120]}"
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(item)

        return merged

    def _build_system_prompt(self) -> str:
        return """你是专利公知常识核查专家。你要判断“申请人的逻辑反驳是否成立”。

判定优先级：
1. 外部检索证据优先级最高：若外部证据足够明确，必须以外部证据为主结论。
2. 仅当外部证据不足时，才允许使用模型通用知识补充判断。
3. 外部证据不足且模型知识也无法形成稳定结论时，输出 INCONCLUSIVE。

结论映射：
- 若“该技术特征在申请日前属于本领域公知常识”更成立 -> EXAMINER_CORRECT
- 若“该技术特征并非公知常识”更成立 -> APPLICANT_CORRECT
- 证据不足 -> INCONCLUSIVE

输出要求：
1. 只输出 JSON 对象，不要输出额外文本。
2. 输出结构必须为：
{
  "assessment": {
    "verdict": "APPLICANT_CORRECT",
    "reasoning": "判断理由",
    "confidence": 0.78,
    "examiner_rejection_reason": "当裁决偏向申请人时，仍可支持审查员维持驳回的说理理由"
  },
  "evidence": [
    {
      "doc_id": "EXT1",
      "quote": "证据原文片段",
      "location": "来源类型+时间+位置",
      "analysis": "证据与结论关系",
      "source_url": "https://...",
      "source_title": "文献标题",
      "source_type": "google_scholar"
    }
  ]
}

字段约束：
- verdict 只能是 APPLICANT_CORRECT / EXAMINER_CORRECT / INCONCLUSIVE
- confidence 必须为 0~1
- 若 verdict=APPLICANT_CORRECT，examiner_rejection_reason 必须给出具体且有说服力的驳回说理；否则留空字符串
- 优先引用 EXT* 证据；若无外部证据可用，可给出一条 doc_id=MODEL 的模型知识证据（source_type=model_knowledge）"""

    def _build_prefix_messages(
        self,
        external_evidence: List[Dict[str, Any]],
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

        if not external_evidence:
            messages.append({
                "role": "user",
                "content": "当前未检索到有效外部证据。可使用模型通用知识进行低置信度分析；若仍不确定输出 INCONCLUSIVE。",
            })
            return messages

        for item in external_evidence:
            messages.append({
                "role": "user",
                "content": (
                    f"外部证据 {item['doc_id']} ({item.get('source_type', 'external')})\n"
                    f"标题: {item.get('title', '')}\n"
                    f"链接: {item.get('url', '')}\n"
                    f"时间: {item.get('published', '')}\n"
                    f"摘要: {item.get('snippet', '')[:700]}"
                ),
            })

        return messages

    def _verify_single_dispute(
        self,
        dispute: Dict[str, Any],
        claim_text: str,
        queries: List[str],
        priority_date: Optional[str],
        external_evidence: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        examiner_opinion = self._to_dict(dispute.get("examiner_opinion", {}))
        applicant_opinion = self._to_dict(dispute.get("applicant_opinion", {}))

        prefix_messages = self._build_prefix_messages(external_evidence, priority_date)
        dispute_prompt = f"""请核查以下逻辑争议项：
dispute_id: {dispute.get("dispute_id", "")}
original_claim_id: {dispute.get("original_claim_id", "")}
claim_text: {claim_text}
feature_text: {dispute.get("feature_text", "")}
examiner_opinion: {json.dumps(examiner_opinion, ensure_ascii=False)}
applicant_opinion: {json.dumps(applicant_opinion, ensure_ascii=False)}
retrieval_queries: {json.dumps(queries, ensure_ascii=False)}
"""
        messages = prefix_messages + [{"role": "user", "content": dispute_prompt}]

        external_doc_ids = {str(item.get("doc_id", "")).strip() for item in external_evidence if item.get("doc_id")}
        allowed_doc_ids = set(external_doc_ids)
        allowed_doc_ids.add("MODEL")
        external_doc_map = {
            str(item.get("doc_id", "")).strip(): item
            for item in external_evidence
            if item.get("doc_id")
        }

        response = self.llm_service.chat_completion_json(
            messages,
            temperature=0.05,
            thinking=True,
        )
        parsed = self._normalize_llm_output(response, allowed_doc_ids, external_doc_map)

        original_claim_id = str(dispute.get("original_claim_id", "")).strip()
        feature_text = str(dispute.get("feature_text", "")).strip()
        used_doc_ids: List[str] = []
        for evidence in parsed.get("evidence", []):
            doc_id = str(evidence.get("doc_id", "")).strip()
            if doc_id and doc_id not in used_doc_ids:
                used_doc_ids.append(doc_id)

        return {
            "dispute_id": str(dispute.get("dispute_id", f"DSP_{original_claim_id}_{feature_text[:8]}")),
            "original_claim_id": original_claim_id,
            "claim_text": claim_text,
            "feature_text": feature_text,
            "examiner_opinion": examiner_opinion,
            "applicant_opinion": applicant_opinion,
            "assessment": parsed["assessment"],
            "evidence": parsed["evidence"],
            "trace": {
                "used_doc_ids": used_doc_ids,
                "missing_doc_ids": [],
                "retrieval_queries": queries,
                "retrieval_engine": "serpapi",
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
        if "examiner_rejection_reason" not in assessment:
            raise ValueError("common_knowledge_verification 输出缺少 assessment.examiner_rejection_reason")
        rejection_reason = str(assessment.get("examiner_rejection_reason", "")).strip()
        if verdict == "APPLICANT_CORRECT" and not rejection_reason:
            raise ValueError(
                "common_knowledge_verification 输出非法: verdict=APPLICANT_CORRECT 时 examiner_rejection_reason 不能为空"
            )
        if verdict != "APPLICANT_CORRECT":
            rejection_reason = ""

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
                "source_url": str(evidence.get("source_url") or source_item.get("url") or "").strip() or None,
                "source_title": str(evidence.get("source_title") or source_item.get("title") or "").strip() or None,
                "source_type": str(evidence.get("source_type") or source_item.get("source_type") or "").strip() or None,
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
