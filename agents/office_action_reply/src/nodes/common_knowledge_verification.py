"""
公知常识核查节点
基于外部检索（优先）与模型知识（次级）对逻辑争议进行核查
"""

import json
import re
from typing import Any, Dict, List, Optional, Set

from loguru import logger

from agents.common.utils.llm import get_llm_service
from agents.office_action_reply.src.external_evidence import ExternalEvidenceAggregator
from agents.office_action_reply.src.retrieval_utils import (
    build_trace_retrieval,
    normalize_query_list,
    plan_engine_queries,
)
from agents.office_action_reply.src.state import EvidenceAssessment
from agents.office_action_reply.src.utils import get_node_cache


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
                "verify_common_knowledge_v5",
                self._verify_common_knowledge,
                self._state_get(state, "disputes", []),
                self._state_get(state, "prepared_materials", {}),
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
    ) -> List[Dict[str, Any]]:
        common_knowledge_disputes = self._get_common_knowledge_disputes(disputes)
        if not common_knowledge_disputes:
            return []

        prepared = self._to_dict(prepared_materials)
        claims = self._extract_claims(prepared)
        priority_date = self._extract_priority_date(prepared)

        assessments: List[Dict[str, Any]] = []
        for dispute in common_knowledge_disputes:
            claim_text = self._get_claim_text(dispute, claims)
            queries_by_engine = self._build_engine_queries(dispute, claim_text, priority_date)
            external_evidence, retrieval_engines, retrieval_meta = self.external_evidence_aggregator.search_evidence(
                queries=queries_by_engine,
                priority_date=priority_date,
                limit=8,
            )
            if not external_evidence:
                logger.warning("外部证据为空，将仅基于模型知识进行低置信度判断")

            assessment = self._verify_single_dispute(
                dispute=dispute,
                claim_text=claim_text,
                queries_by_engine=queries_by_engine,
                priority_date=priority_date,
                external_evidence=external_evidence,
                retrieval_engines=retrieval_engines,
                retrieval_meta=retrieval_meta,
            )
            assessments.append(assessment)

        return assessments

    def _get_common_knowledge_disputes(self, disputes: List[Any]) -> List[Dict[str, Any]]:
        common_knowledge_disputes: List[Dict[str, Any]] = []
        for item in disputes or []:
            dispute = self._to_dict(item)
            examiner_opinion = self._to_dict(dispute.get("examiner_opinion", {}))
            dispute_type = str(examiner_opinion.get("type", "")).strip()
            if dispute_type in {"common_knowledge_based", "mixed_basis"}:
                common_knowledge_disputes.append(dispute)
        return common_knowledge_disputes

    def _extract_claims(self, prepared_materials: Dict[str, Any]) -> List[Dict[str, Any]]:
        original_patent = self._to_dict(prepared_materials.get("original_patent", {}))
        patent_data = self._to_dict(original_patent.get("data", {}))
        claims = patent_data.get("claims", [])
        if not isinstance(claims, list):
            return []
        return [self._to_dict(claim) for claim in claims]

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
    ) -> Dict[str, List[str]]:
        feature_text = str(dispute.get("feature_text", "")).strip()
        examiner_opinion = self._to_dict(dispute.get("examiner_opinion", {}))
        applicant_opinion = self._to_dict(dispute.get("applicant_opinion", {}))
        fallback_queries = {
            "openalex": normalize_query_list([
                f"{feature_text} common general knowledge prior to filing date",
                f"{feature_text} conventional implementation patent",
                f"{feature_text} {examiner_opinion.get('reasoning', '')}",
            ], limit=2),
            "zhihuiya": normalize_query_list([
                f"{feature_text} 本领域公知常识",
                f"{feature_text} 技术手段 常见实现",
                f"{feature_text} {applicant_opinion.get('core_conflict', '')}",
            ], limit=2),
            "tavily": normalize_query_list([
                f"{feature_text} 本领域公知 常见做法",
                f"{feature_text} 技术原理 公开资料",
                f"{feature_text} {claim_text[:120]}",
            ], limit=2),
        }
        user_context = {
            "priority_date": priority_date or "",
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
- examiner_rejection_reason：
  *[强制规则 1] 仅当 verdict 为 "APPLICANT_CORRECT" 时，本字段【必须】填写内容。其业务逻辑是：虽然申请人成功反驳了该特征是“公知常识”，但为了维持审查意见的对抗性，你必须代表审查员撰写一段“替代性的驳回说理”（例如：指出该特征虽非公知常识，但结合具体应用场景属于容易想到的常规手段变形，或利用其他逻辑维持不具备创造性的结论）。
  *[强制规则 2] 当 verdict 为 "EXAMINER_CORRECT" 或 "INCONCLUSIVE" 时，本字段【必须】为空字符串 ""。
  *[语气约束] 必须使用“审查意见通知书正文口吻”，面向申请人，必须是确定性陈述。绝对禁止使用“审查员可主张、建议、应补充”等第三人称或元话术。

【输出格式要求】
1. 必须且只能输出合法的 JSON 对象，不要包含 ```json 等任何 Markdown 标记，不要输出额外说明文本。
2. JSON 结构必须严格如下：
{
  "assessment": {
    "verdict": "APPLICANT_CORRECT | EXAMINER_CORRECT | INCONCLUSIVE",
    "reasoning": "详细的判定理由，包含对技术特征、证据内容以及公知常识属性的分析。",
    "confidence": 0.85,
    "examiner_rejection_reason": "遵守上述强制规则。若需填写，示例：经核查，虽然现有证据未将...直接定义为公知常识，但其工作原理属于本领域常规设计手段的直接推演，本领域技术人员在D1基础上引入该手段不需要创造性劳动，故相关权利要求仍不具备创造性。"
  },
  "evidence":[
    {
      "doc_id": "EXT1",
      "quote": "原文核心证据片段摘录",
      "location": "如：文献摘要/第X段/摘要",
      "analysis": "该证据如何支持或反驳公知常识的认定",
      "source_url": "https://...",
      "source_title": "文献标题",
      "source_type": "openalex 或 zhihuiya 或 model_knowledge"
    }
  ]
}

【证据引用说明】
- 优先引用提供的外部证据（doc_id 必须对应提供的 EXT 编号）。
- 若完全没有外部证据，允许生成一条基于模型自身知识的证据，此时 doc_id 必须固定为 "MODEL"，source_type 固定为 "model_knowledge"。"""

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
        queries_by_engine: Dict[str, List[str]],
        priority_date: Optional[str],
        external_evidence: List[Dict[str, Any]],
        retrieval_engines: List[str],
        retrieval_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        examiner_opinion = self._to_dict(dispute.get("examiner_opinion", {}))
        applicant_opinion = self._to_dict(dispute.get("applicant_opinion", {}))

        prefix_messages = self._build_prefix_messages(external_evidence, priority_date)
        flat_queries = []
        for engine_queries in queries_by_engine.values():
            for query in engine_queries:
                if query not in flat_queries:
                    flat_queries.append(query)
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
