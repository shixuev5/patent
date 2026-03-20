"""
争辩焦点结构化提取节点
先从申请人意见陈述中抽取 applicant arguments，再结合 OA 对齐生成最终 disputes。
"""

import hashlib
import json
import re
from typing import Any, Dict, List

from loguru import logger

from agents.ai_reply.src.state import Dispute
from agents.ai_reply.src.utils import get_node_cache
from agents.common.utils.llm import get_llm_service


class DisputeExtractionNode:
    """争辩焦点结构化提取节点"""

    def __init__(self, config=None):
        self.config = config
        self.llm_service = get_llm_service()

    def __call__(self, state):
        logger.info("开始提取争辩焦点")

        updates = {
            "current_node": "dispute_extraction",
            "status": "running",
            "progress": 50.0,
        }

        try:
            cache = get_node_cache(self.config, "dispute_extraction")

            prepared_materials = self._to_dict(self._state_get(state, "prepared_materials"))
            office_action = self._to_dict(prepared_materials.get("office_action", {}))
            response = self._to_dict(prepared_materials.get("response", {}))
            paragraphs = office_action.get("paragraphs", [])
            response_content = response.get("content", "")

            if not paragraphs or not response_content:
                logger.warning("缺少必要的输入文件")
                updates["errors"] = [{
                    "node_name": "dispute_extraction",
                    "error_message": "缺少 prepared_materials.office_action.paragraphs 或 prepared_materials.response.content",
                    "error_type": "missing_input",
                }]
                updates["status"] = "failed"
                return updates

            applicant_arguments = cache.run_step(
                "extract_applicant_arguments_v1",
                self._extract_applicant_arguments,
                prepared_materials,
            )
            valid_disputes = cache.run_step(
                "match_disputes_with_oa_v1",
                self._match_and_validate_disputes,
                prepared_materials,
                applicant_arguments,
            )

            updates["disputes"] = [
                item if isinstance(item, Dispute) else Dispute(**item)
                for item in valid_disputes
            ]
            updates["progress"] = 60.0
            updates["status"] = "completed"

            logger.info(
                f"提取到 {len(applicant_arguments or [])} 个申请人论点，生成 {len(valid_disputes)} 个争辩焦点"
            )

        except Exception as exc:
            logger.error(f"争辩焦点提取失败: {exc}")
            updates["errors"] = [{
                "node_name": "dispute_extraction",
                "error_message": str(exc),
                "error_type": "extraction_error",
            }]
            updates["status"] = "failed"

        return updates

    def _match_and_validate_disputes(
        self,
        prepared_materials: Dict[str, Any],
        applicant_arguments: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        prepared_materials_dict = self._to_dict(prepared_materials)
        comparison_documents = prepared_materials_dict.get("comparison_documents", [])
        valid_doc_ids = {
            str(item.get("document_id", "")).strip()
            for item in comparison_documents
            if isinstance(item, dict) and str(item.get("document_id", "")).strip()
        }
        disputes = self._match_disputes_with_oa(prepared_materials_dict, applicant_arguments or [])
        return self._validate_disputes(disputes, valid_doc_ids)

    def _extract_applicant_arguments(self, prepared_materials: Dict[str, Any]) -> List[Dict[str, Any]]:
        response = self._to_dict(prepared_materials.get("response", {}))
        response_content = str(response.get("content", "")).strip()
        if not response_content:
            return []

        messages = [
            {"role": "system", "content": self._build_applicant_argument_system_prompt()},
            {"role": "user", "content": self._build_applicant_argument_user_prompt(response_content)},
        ]

        try:
            result = self.llm_service.invoke_text_json(
                messages=messages,
                task_kind="oar_applicant_argument_extraction",
                temperature=0.05,
            )
        except Exception as exc:
            logger.error(f"申请人论点抽取失败: {exc}")
            return []

        if isinstance(result, list):
            raw_arguments = result
        elif isinstance(result, dict) and "arguments" in result:
            raw_arguments = result.get("arguments", [])
        else:
            logger.warning(f"申请人论点抽取返回异常格式: {type(result)}")
            return []

        return self._normalize_applicant_arguments(raw_arguments)

    def _match_disputes_with_oa(
        self,
        prepared_materials: Dict[str, Any],
        applicant_arguments: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not applicant_arguments:
            logger.warning("申请人论点为空，无法结合 OA 生成争辩焦点")
            return []

        messages = [
            {"role": "system", "content": self._build_oa_matching_system_prompt()},
            {"role": "user", "content": self._build_oa_matching_user_prompt(prepared_materials, applicant_arguments)},
        ]

        try:
            result = self.llm_service.invoke_text_json(
                messages=messages,
                task_kind="oar_dispute_oa_matching",
                temperature=0.05,
            )
        except Exception as exc:
            logger.error(f"OA 匹配失败: {exc}")
            return []

        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "disputes" in result:
            disputes = result.get("disputes", [])
            return disputes if isinstance(disputes, list) else []

        logger.warning(f"OA 匹配返回异常格式: {type(result)}")
        return []

    def _build_applicant_argument_system_prompt(self) -> str:
        return """你是一位资深的中国专利代理师和专利审查分析专家。你的唯一任务是阅读【申请人意见陈述书】，穷尽式地、客观准确地提取申请人提出的所有实质性反驳论点（Applicant Arguments）。

【提取原则与强制纪律】
1. 完整无遗漏：必须穷尽式提取申请人的所有反驳防线。如果针对同一个权利要求，申请人既争辩了“特征A未被D1公开”，又争辩了“即使公开，D1和D2也没有结合启示”，必须将其拆分为两个独立的 argument_id。
2. 准确区分原话：申请人经常会在答复中先“复述”审查员的观点（如“审查员认为...”），然后再反驳（如“但是申请人认为...”）。你必须精准剥离复述部分，只提取申请人真正的反驳主张。
3. 客观无偏差：在总结 reasoning 和 core_conflict 时，必须使用第三人称的客观陈述（如“申请人主张...”、“申请人指出...”），不附加任何主观评判。
4. 忽略形式内容：直接跳过寒暄、请求继续审查、联系方式、修改说明（如“将权2并入权1”）等非争辩性内容。
5. 精准原文引用：`source_quote` 必须从原文档中逐字摘抄最核心的反驳原话，严禁改写或脑补。

【字段定义与分类标准】
- argument_type（只能是以下两者之一）：
  - fact_dispute (事实争议)：针对“对比文件是否公开了某特征”、“本专利特征的实际含义（权利要求解释）”、“是否属于公知常识”、“技术问题是否被解决”等客观事实的争议。
  - logic_dispute (逻辑/动机争议)：针对“是否存在结合启示/结合动机”、“技术领域是否相同”、“是否能达到相同技术效果”、“是否有阻碍”等推理逻辑的争议。

【执行检查清单】
- [ ] 是否提取了所有独立的论点？没有遗漏次要的争辩理由？
- [ ] source_quote 是否完全是原文档中的逐字摘抄？
- [ ] reasoning 是否客观准确，没有混入审查员的原始观点？

【输出格式要求】
必须且只能输出纯 JSON 数据，严格遵循以下结构。请先在 `_analysis` 字段中进行思维链分析。不要使用 ```json 标记包裹。

{
  "_analysis": "分析申请人针对哪些权利要求、哪些对比文件提出了几条防线，分别是什么争议类型。",
  "arguments":[
    {
      "argument_id": "ARG_1",
      "claim_ids": ["1", "6"], 
      "doc_ids": ["D1", "D2"], 
      "feature_text": "争议的核心技术特征（需精简，如‘动态密钥生成机制’）",
      "argument_type": "fact_dispute 或者是 logic_dispute",
      "reasoning": "客观复述申请人的具体论证逻辑（如：申请人认为D1仅公开了静态密钥，并未公开...因此无法实现...）",
      "core_conflict": "15字以内的高度浓缩核心冲突（如：D1未公开动态生成机制）",
      "source_quote": "原封不动摘取的关键原文段落"
    }
  ]
}"""

    def _build_applicant_argument_user_prompt(self, response_content: str) -> str:
        return f"""请处理以下《申请人意见陈述书》正文，提取申请人论点：

<applicant_response>
{response_content}
</applicant_response>"""

    def _build_oa_matching_system_prompt(self) -> str:
        return """你是一位顶级的中国专利审查对位分析专家。你的任务是将【申请人的反驳论点 (Applicant Arguments)】与【审查意见通知书结构化段落 (OA Paragraphs)】进行精准匹配，生成完整的、无偏差的【争辩焦点 (Disputes)】。

【对齐与生成原则】
1. 论点溯源基准：每一个 dispute 必须且只能溯源到一个申请人论点 (`source_argument_id`)。绝对不能凭空捏造申请人未主张的焦点。
2. 审查观点还原：根据论点的 claim_ids 和 feature_text，在 OA paragraphs 中寻找审查员针对该特征的原始认定。必须忠实于 OA 原文，准确还原审查员的攻击逻辑。
3. 补全与对齐机制：如果申请人论点中未明确写出 claim_ids，请结合匹配到的 OA 段落推断并补全 claim_ids。
4. 客观对抗呈现：在 dispute 中，examiner_opinion 和 applicant_opinion 必须形成清晰的、截然对立的“对抗态势”，且双方观点描述必须绝对中立客观。

【审查员意见字段 (examiner_opinion) 定义】
- type（只能是以下三者之一）：
  - document_based: 审查员纯粹基于某篇或多篇对比文件（如D1, D2）公开的内容来否定创造性/新颖性。
  - common_knowledge_based: 审查员以“公知常识”或“本领域惯用技术手段”为由进行否定（此时 supporting_docs 数组必须为空[]）。
  - mixed_basis: 审查员同时使用了对比文件和公知常识（如：“D1公开了A，B属于公知常识”）。
- supporting_docs:
  - 必须来源于匹配的 OA 段落中的 cited_doc_ids。
  - `cited_text` 应当尽量从 OA 的 content 中摘录审查员评价该特征的原话，不要自行发挥。

【执行检查清单】
- [ ] 每一个生成的 dispute 是否都有合法的 source_argument_id 对应？
- [ ] 当 examiner_opinion.type 为 common_knowledge_based 时，supporting_docs 是否严格为空数组？
- [ ] 申请人和审查员的观点是否做到了完全无偏差的客观复述？

【输出格式要求】
必须且只能输出纯 JSON 数据，遵循以下结构。请先在 `_mapping_thinking` 中梳理匹配逻辑。不要使用 ```json 标记包裹。

{
  "_mapping_thinking": "逐一分析每个 ARG_X 匹配到了 OA 的哪个段落，审查员认定依据是什么，双方冲突点在哪。",
  "disputes":[
    {
      "dispute_id": "", 
      "source_argument_id": "ARG_1",
      "claim_ids":["1"],
      "feature_text": "争议的核心技术特征或议题",
      "examiner_opinion": {
        "type": "document_based | common_knowledge_based | mixed_basis",
        "supporting_docs":[
          {
            "doc_id": "D1",
            "cited_text": "审查员针对该特征的认定原话（如：对比文件1第3段公开了...）"
          }
        ],
        "reasoning": "客观概括审查员的逻辑（如：审查员认为特征A已被D1公开...）"
      },
      "applicant_opinion": {
        "type": "fact_dispute | logic_dispute",
        "reasoning": "继承并优化申请人的论证逻辑",
        "core_conflict": "继承并优化申请人的核心冲突点"
      }
    }
  ]
}"""

    def _build_oa_matching_user_prompt(
        self,
        prepared_materials: Dict[str, Any],
        applicant_arguments: List[Dict[str, Any]],
    ) -> str:
        office_action = self._to_dict(prepared_materials.get("office_action", {}))

        oa_paragraphs = []
        for item in office_action.get("paragraphs", []) or []:
            paragraph = self._to_dict(item)
            oa_paragraphs.append(
                {
                    "paragraph_id": paragraph.get("paragraph_id", ""),
                    "claim_ids": paragraph.get("claim_ids", []),
                    "legal_basis": paragraph.get("legal_basis", []),
                    "issue_types": paragraph.get("issue_types", []),
                    "cited_doc_ids": paragraph.get("cited_doc_ids", []),
                    "evaluation": str(paragraph.get("evaluation", "unknown")).strip() or "unknown",
                    "content": paragraph.get("content", ""),
                }
            )

        return f"""请对以下数据进行精准对位分析：

<applicant_arguments>
{json.dumps(applicant_arguments, ensure_ascii=False, indent=2)}
</applicant_arguments>

<office_action_paragraphs>
{json.dumps(oa_paragraphs, ensure_ascii=False, indent=2)}
</office_action_paragraphs>"""

    def _normalize_applicant_arguments(self, arguments: List[Any]) -> List[Dict[str, Any]]:
        normalized_arguments: List[Dict[str, Any]] = []

        for index, item in enumerate(arguments or [], 1):
            argument = self._to_dict(item)
            if not argument:
                continue

            feature_text = str(argument.get("feature_text", "")).strip()
            reasoning = str(argument.get("reasoning", "")).strip()
            core_conflict = str(argument.get("core_conflict", "")).strip()
            source_quote = str(argument.get("source_quote", "")).strip()
            if not feature_text:
                logger.warning(f"第{index}个申请人论点缺少 feature_text，跳过")
                continue
            if not reasoning:
                logger.warning(f"第{index}个申请人论点缺少 reasoning，跳过")
                continue
            if not core_conflict:
                logger.warning(f"第{index}个申请人论点缺少 core_conflict，跳过")
                continue
            if not source_quote:
                logger.warning(f"第{index}个申请人论点缺少 source_quote，跳过")
                continue

            claim_ids = self._normalize_claim_ids(argument.get("claim_ids"))
            doc_ids = self._normalize_doc_ids(argument.get("doc_ids"))

            argument_type = str(argument.get("argument_type", "")).strip()
            if argument_type not in {"fact_dispute", "logic_dispute"}:
                logger.warning(f"第{index}个申请人论点 argument_type 非法或缺失，跳过")
                continue

            argument_id = str(argument.get("argument_id", "")).strip() or f"ARG_{index}"
            normalized_arguments.append(
                {
                    "argument_id": argument_id,
                    "claim_ids": claim_ids,
                    "doc_ids": doc_ids,
                    "feature_text": feature_text,
                    "argument_type": argument_type,
                    "reasoning": reasoning,
                    "core_conflict": core_conflict,
                    "source_quote": source_quote,
                }
            )

        return normalized_arguments

    def _validate_disputes(self, disputes: List[Any], valid_doc_ids: set[str]) -> List[Dict[str, Any]]:
        valid_disputes = []

        for index, dispute_item in enumerate(disputes or [], 1):
            try:
                dispute = self._to_dict(dispute_item)
                if not dispute:
                    continue

                claim_ids = self._normalize_claim_ids(dispute.get("claim_ids"))
                if not claim_ids:
                    logger.warning(f"第{index}个争辩焦点 claim_ids 非法或为空")
                    continue

                feature_text = str(dispute.get("feature_text", "")).strip()
                if not feature_text:
                    logger.warning(f"第{index}个争辩焦点缺少 feature_text")
                    continue

                examiner_opinion = self._to_dict(dispute.get("examiner_opinion", {}))
                applicant_opinion = self._to_dict(dispute.get("applicant_opinion", {}))
                if not examiner_opinion or not applicant_opinion:
                    logger.warning(f"第{index}个争辩焦点观点字段格式错误")
                    continue

                examiner_type = str(examiner_opinion.get("type", "")).strip()
                if examiner_type not in {"document_based", "common_knowledge_based", "mixed_basis"}:
                    logger.warning(f"第{index}个争辩焦点 examiner_opinion.type 非法: {examiner_type}")
                    continue

                applicant_type = str(applicant_opinion.get("type", "")).strip()
                if applicant_type not in {"fact_dispute", "logic_dispute"}:
                    logger.warning(f"第{index}个争辩焦点 applicant_opinion.type 非法: {applicant_type}")
                    continue
                applicant_reasoning = str(applicant_opinion.get("reasoning", "")).strip()
                if not applicant_reasoning:
                    logger.warning(f"第{index}个争辩焦点 applicant_opinion.reasoning 为空")
                    continue
                applicant_core_conflict = str(applicant_opinion.get("core_conflict", "")).strip()
                if not applicant_core_conflict:
                    logger.warning(f"第{index}个争辩焦点 applicant_opinion.core_conflict 为空")
                    continue

                supporting_docs_raw = examiner_opinion.get("supporting_docs", [])
                normalized_supporting_docs = self._normalize_supporting_docs(
                    supporting_docs_raw,
                    valid_doc_ids,
                )

                if examiner_type == "common_knowledge_based":
                    normalized_supporting_docs = []
                elif not normalized_supporting_docs:
                    logger.warning(f"第{index}个争辩焦点缺少 supporting_docs")
                    continue

                valid_disputes.append(
                    {
                        "dispute_id": self._build_dispute_id(
                            dispute.get("dispute_id", ""),
                            claim_ids,
                            feature_text,
                        ),
                        "source_argument_id": str(dispute.get("source_argument_id", "")).strip(),
                        "claim_ids": claim_ids,
                        "feature_text": feature_text,
                        "examiner_opinion": {
                            "type": examiner_type,
                            "supporting_docs": normalized_supporting_docs,
                            "reasoning": str(examiner_opinion.get("reasoning", "")).strip(),
                        },
                        "applicant_opinion": {
                            "type": applicant_type,
                            "reasoning": applicant_reasoning,
                            "core_conflict": applicant_core_conflict,
                        },
                    }
                )
            except Exception as exc:
                logger.warning(f"验证第{index}个争辩焦点时出错: {exc}")

        return valid_disputes

    def _normalize_supporting_docs(
        self,
        supporting_docs_raw: Any,
        valid_doc_ids: set[str],
    ) -> List[Dict[str, str]]:
        if not isinstance(supporting_docs_raw, list):
            supporting_docs_raw = []

        normalized: List[Dict[str, str]] = []
        seen_doc_ids = set()
        for item in supporting_docs_raw:
            doc_item = self._to_dict(item)
            if not doc_item:
                continue
            doc_id = str(doc_item.get("doc_id", "")).strip().upper()
            if not doc_id:
                continue
            if valid_doc_ids and doc_id not in valid_doc_ids:
                continue
            if doc_id in seen_doc_ids:
                continue
            seen_doc_ids.add(doc_id)
            normalized.append(
                {
                    "doc_id": doc_id,
                    "cited_text": str(doc_item.get("cited_text", "")).strip(),
                }
            )
        return normalized

    def _extract_doc_ids_from_text(self, text: str, valid_doc_ids: set[str]) -> List[str]:
        doc_ids: List[str] = []
        for match in re.finditer(r"(?:对比文件\s*|[Dd]\s*)(\d+)", text or "", re.I):
            value = f"D{int(match.group(1))}"
            if valid_doc_ids and value not in valid_doc_ids:
                continue
            if value not in doc_ids:
                doc_ids.append(value)
        return doc_ids

    def _normalize_doc_ids(self, value: Any) -> List[str]:
        doc_ids: List[str] = []
        candidates = value if isinstance(value, list) else [value]
        for raw in candidates:
            for doc_id in self._extract_doc_ids_from_text(str(raw or ""), set()):
                if doc_id not in doc_ids:
                    doc_ids.append(doc_id)
        return doc_ids

    def _build_dispute_id(self, raw_dispute_id: Any, claim_ids: List[str], feature_text: str) -> str:
        provided = str(raw_dispute_id or "").strip()
        if provided:
            return provided
        digest = hashlib.md5(feature_text.encode("utf-8")).hexdigest()[:8]
        claim_part = "_".join(claim_ids[:4]) if claim_ids else "UNKNOWN"
        return f"DSP_{claim_part}_{digest}"

    def _normalize_claim_ids(self, value: Any) -> List[str]:
        claim_ids: List[str] = []
        candidates = value if isinstance(value, list) else [value]
        claim_keyword_pattern = r"权\s*利\s*要\s*求"
        range_pattern = rf"(?:{claim_keyword_pattern}\s*)?(\d+)\s*(?:-|－|—|~|～|至|到)\s*(\d+)"

        for raw in candidates:
            text = str(raw or "").strip()
            if not text:
                continue

            for start_raw, end_raw in re.findall(range_pattern, text):
                start = int(start_raw)
                end = int(end_raw)
                low, high = (start, end) if start <= end else (end, start)
                for value_int in range(low, high + 1):
                    claim_id = str(value_int)
                    if claim_id not in claim_ids:
                        claim_ids.append(claim_id)

            normalized_text = re.sub(claim_keyword_pattern, " ", text)
            for piece in re.findall(r"\d+", normalized_text):
                if piece not in claim_ids:
                    claim_ids.append(piece)

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
