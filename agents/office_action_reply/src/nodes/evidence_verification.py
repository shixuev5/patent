"""
证据核查节点
基于 prepared_materials 中的原权利要求与对比文件内容，对事实争议进行核查
"""

import json
import re
from collections import defaultdict
from typing import Any, Dict, List, Tuple
from loguru import logger
from agents.common.utils.llm import get_llm_service
from agents.office_action_reply.src.utils import get_node_cache
from agents.office_action_reply.src.state import EvidenceAssessment


class EvidenceVerificationNode:
    """证据核查节点（新结构）"""

    def __init__(self, config=None):
        self.config = config
        self.llm_service = get_llm_service()

    def __call__(self, state):
        logger.info("开始证据核查")
        updates = {}

        try:
            cache = get_node_cache(self.config, "evidence_verification")
            assessments = cache.run_step(
                "verify_evidence_v4",
                self._verify_evidence,
                self._state_get(state, "disputes", []),
                self._state_get(state, "prepared_materials", {}),
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

    def _verify_evidence(self, disputes, prepared_materials):
        document_disputes = self._get_document_based_disputes(disputes)
        if not document_disputes:
            return []

        prepared = self._to_dict(prepared_materials)
        claims = self._extract_claims(prepared)
        comparison_doc_map = self._build_comparison_doc_map(prepared)
        grouped_disputes = self._group_disputes_by_docs(document_disputes)

        assessments = []
        for doc_group, group_items in grouped_disputes.items():
            docs_context, missing_doc_ids = self._build_docs_context(doc_group, comparison_doc_map)
            prefix_messages = self._build_prefix_messages(docs_context)

            for dispute in group_items:
                assessment = self._verify_single_dispute(
                    dispute=dispute,
                    claims=claims,
                    doc_group=doc_group,
                    missing_doc_ids=missing_doc_ids,
                    prefix_messages=prefix_messages,
                )
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

    def _build_docs_context(
        self,
        doc_group: Tuple[str, ...],
        comparison_doc_map: Dict[str, Dict[str, Any]],
    ) -> Tuple[List[Dict[str, str]], List[str]]:
        docs_context: List[Dict[str, str]] = []
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

            docs_context.append({
                "doc_id": doc_id,
                "document_number": str(doc.get("document_number", "")),
                "content": content[:16000],
            })

        return docs_context, missing_doc_ids

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

    def _build_prefix_messages(self, docs_context: List[Dict[str, str]]) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": self._build_system_prompt()},
        ]

        if not docs_context:
            messages.append({
                "role": "user",
                "content": "当前争议项未提供任何可用对比文件全文内容。请在输出中给出 INCONCLUSIVE。"
            })
            return messages

        for doc in docs_context:
            messages.append({
                "role": "user",
                "content": (
                    f"对比文件上下文：{doc['doc_id']} ({doc['document_number']})\n"
                    f"全文片段如下：\n{doc['content']}"
                )
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

### 特殊字段约束：examiner_rejection_reason（极度重要）
在专利审查实务的自动化流程中，当审查员最初的某项认定被指出错误时，系统需尝试寻找新的驳回理由。
- **当 verdict = "EXAMINER_CORRECT" 或 "INCONCLUSIVE" 时**：该字段必须为空字符串 `""`。
- **当 verdict = "APPLICANT_CORRECT" 时**：你必须代入审查员的角色，**基于核查后发现的真实对比文件内容，重新构建一段无懈可击的驳回说理**，用于后续下发给申请人。
  - **口吻要求（强制）**：必须是“审查意见通知书正文口吻”，绝对确定，面向申请人论述。
  - **禁止词汇**：严禁使用“审查员可主张”、“可认为”、“可以”、“建议”、“应补充”、“如需”、“若…则…”等商榷性、策略性或元描述词汇。
  - **标准话术示例**：“经审查认为，虽然对比文件D1未直接公开[原争议特征]，但D1中记载了[真实存在的相关特征]......本领域技术人员在此基础上容易想到......，因此权利要求1仍不具备创造性。”

### JSON 输出格式与字段定义
你必须输出且仅输出一个合法的 JSON 对象，不要包含任何 Markdown 格式标记（如 ```json），不要有任何前言或后语。JSON 结构如下：

{
  "assessment": {
    "verdict": "必须是 APPLICANT_CORRECT, EXAMINER_CORRECT, INCONCLUSIVE 之一",
    "reasoning": "你的核查分析过程。指出审查员和申请人谁对谁错，以及为什么。逻辑需严密。",
    "confidence": 0.95, // 0.0到1.0之间的浮点数，表示你对该判定的信心
    "examiner_rejection_reason": "严格遵循上述【特殊字段约束】的要求填写"
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
    ) -> Dict[str, Any]:
        claim_text = self._get_claim_text(dispute, claims)
        examiner_opinion = self._to_dict(dispute.get("examiner_opinion", {}))
        applicant_opinion = self._to_dict(dispute.get("applicant_opinion", {}))

        dispute_prompt = f"""请核查以下争议项：
claim_ids: {json.dumps(self._normalize_claim_ids(dispute.get("claim_ids", [])), ensure_ascii=False)}
claim_text: {claim_text}
feature_text: {dispute.get("feature_text", "")}
examiner_opinion: {json.dumps(examiner_opinion, ensure_ascii=False)}
applicant_opinion: {json.dumps(applicant_opinion, ensure_ascii=False)}
supporting_docs_doc_ids: {json.dumps(list(doc_group), ensure_ascii=False)}
missing_doc_ids: {json.dumps(missing_doc_ids, ensure_ascii=False)}
"""
        messages = prefix_messages + [{"role": "user", "content": dispute_prompt}]

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
