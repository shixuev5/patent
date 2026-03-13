"""
补充检索与评判节点
针对新增特征执行对比文件深扫与外部检索，并由大模型给出最终裁决
"""

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Set, Tuple

from loguru import logger

from agents.common.utils.llm import get_llm_service
from agents.ai_reply.src.external_evidence import ExternalEvidenceAggregator
from agents.ai_reply.src.retrieval_utils import (
    build_trace_retrieval,
    normalize_query_list,
    plan_engine_queries,
)
from agents.ai_reply.src.state import Dispute, EvidenceAssessment
from agents.ai_reply.src.utils import get_node_cache


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
                "verify_topup_v6",
                self._verify_topup,
                topup_tasks,
                self._state_get(state, "prepared_materials", {}),
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

    def _verify_topup(self, topup_tasks, prepared_materials):
        tasks =[self._to_dict(item) for item in (topup_tasks or [])]
        prepared = self._to_dict(prepared_materials)

        claims = self._extract_claims(prepared)
        comparison_docs = self._build_comparison_docs(prepared)
        priority_date = self._extract_priority_date(prepared)

        disputes =[]
        assessments =[]
        for task in tasks:
            dispute, assessment = self._evaluate_task(task, claims, comparison_docs, priority_date)
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
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        task_id = str(task.get("task_id", "")).strip() or "New_FX"
        claim_ids = self._normalize_claim_ids(task.get("claim_ids", [])) or ["1"]
        feature_text = str(task.get("feature_text", "")).strip()
        dispute_id = f"TOPUP_{task_id}"
        claim_text = self._get_claim_text(claim_ids, claims)

        local_evidence = self._scan_comparison_docs(feature_text, comparison_docs)
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
    "examiner_rejection_reason": "【关键】当判决倾向申请人时，审查员为了强行维持驳回可能使用的官方说理（见下方约束）。"
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

【驳回正文话术约束 (examiner_rejection_reason) —— 极度重要❗】
- 此字段的文本将直接插入发送给申请人的《审查意见通知书》正文中。
- **触发条件**：仅当 verdict 为 `APPLICANT_CORRECT` 且你必须模拟审查员强行驳回时填写；若 verdict 不为 APPLICANT_CORRECT，必须留空字符串 `""`。
- **文风要求**：必须是**面向申请人的、确定的、官方的陈述语气**。
- **绝对禁止出现的词汇**：禁止出现“审查员可主张”、“审查员认为”、“建议”、“可以尝试”、“如果...则...”、“为了维持驳回”。
- ✅ 合格示例："经审查，虽然修改后的权利要求增加了XXX特征，但该特征已被对比文件1附件中的YYY内容所公开，且将其应用于本发明解决ZZZ问题属于本领域技术人员的常规技术手段，故该权利要求依然不具备创造性。"
- ❌ 违规示例："审查员可以主张该特征是公知常识，建议指出其不具备创造性以维持驳回。"
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
        if "examiner_rejection_reason" not in assessment:
            raise ValueError("topup_search_verification 输出缺少 assessment.examiner_rejection_reason")
        rejection_reason = str(assessment.get("examiner_rejection_reason", "")).strip()
        if verdict == "APPLICANT_CORRECT" and not rejection_reason:
            raise ValueError(
                "topup_search_verification 输出非法: verdict=APPLICANT_CORRECT 时 examiner_rejection_reason 不能为空"
            )
        if verdict != "APPLICANT_CORRECT":
            rejection_reason = ""

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
                "examiner_rejection_reason": rejection_reason,
            },
            "evidence": evidence_items,
            "used_doc_ids": used_doc_ids,
        }

    def _scan_comparison_docs(self, feature_text: str, comparison_docs: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
        # 专利常用停用词过滤，提取真正具有技术含义的关键词
        stop_words = {"一种", "方法", "系统", "装置", "包括", "用于", "实现", "步骤", "所述", "其特征在于", "连接", "设置", "具有", "能够"}
        
        raw_tokens =[token.strip() for token in re.split(r"[\s,，。；;:：、（）()\[\]{}]+", feature_text)]
        meaningful_tokens =[
            t for t in raw_tokens 
            if len(t) >= 2 and t not in stop_words and not t.isdigit()
        ]
        
        # 优先使用有意义的词，如果都被过滤则降级使用原始拆分词
        tokens = meaningful_tokens[:8] if meaningful_tokens else [t for t in raw_tokens if len(t) >= 2][:8]
        
        evidence_items: List[Dict[str, Any]] =[]

        for doc_id, doc in comparison_docs.items():
            content = str(doc.get("content", ""))
            if not content:
                continue

            first_hit = ""
            for token in tokens:
                if token and token in content:
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

    def _extract_claims(self, prepared_materials: Dict[str, Any]) -> List[Dict[str, Any]]:
        original_patent = self._to_dict(prepared_materials.get("original_patent", {}))
        patent_data = self._to_dict(original_patent.get("data", {}))
        claims = patent_data.get("claims",[])
        if not isinstance(claims, list):
            return []
        return[self._to_dict(claim) for claim in claims]

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
