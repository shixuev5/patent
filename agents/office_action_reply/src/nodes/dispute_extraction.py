"""
争辩焦点结构化提取节点
从 prepared_materials 中提取结构化争辩焦点（争点对）
"""

import json
import hashlib
import re
from agents.common.utils.llm import get_llm_service
from loguru import logger
from typing import Dict, List, Any
from agents.office_action_reply.src.utils import get_node_cache
from agents.office_action_reply.src.state import Dispute


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
            "progress": 50.0
        }

        try:
            # 获取节点缓存
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
                    "error_type": "missing_input"
                }]
                updates["status"] = "failed"
                return updates

            # 使用缓存运行争辩焦点提取
            valid_disputes = cache.run_step(
                "extract_disputes_v4",
                self._extract_and_validate_disputes,
                prepared_materials
            )

            # 更新状态 - 平铺字段
            updates["disputes"] = [
                item if isinstance(item, Dispute) else Dispute(**item)
                for item in valid_disputes
            ]
            updates["progress"] = 60.0
            updates["status"] = "completed"

            logger.info(f"提取到 {len(valid_disputes)} 个争辩焦点")

        except Exception as e:
            logger.error(f"争辩焦点提取失败: {e}")
            updates["errors"] = [{
                "node_name": "dispute_extraction",
                "error_message": str(e),
                "error_type": "extraction_error"
            }]
            updates["status"] = "failed"

        return updates

    def _extract_and_validate_disputes(self, prepared_materials):
        """
        实际执行争辩焦点提取和验证的内部方法（可缓存）

        Args:
            prepared_materials: 整理后的关键材料

        Returns:
            验证后的争辩焦点列表
        """
        prepared_materials_dict = self._to_dict(prepared_materials)
        comparison_documents = prepared_materials_dict.get("comparison_documents", [])
        valid_doc_ids = {
            str(item.get("document_id", "")).strip()
            for item in comparison_documents
            if isinstance(item, dict) and str(item.get("document_id", "")).strip()
        }

        # 提取争辩焦点
        disputes = self._extract_disputes(prepared_materials_dict)

        # 验证数据格式
        valid_disputes = self._validate_disputes(disputes, valid_doc_ids)

        return valid_disputes

    def _extract_disputes(self, prepared_materials: Dict[str, Any]) -> List[Dict]:
        """使用LLM提取争辩焦点"""
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(prepared_materials)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        try:
            result = self.llm_service.chat_completion_json(messages, temperature=0.05, thinking=True)
            if isinstance(result, list):
                return result
            elif isinstance(result, dict) and "disputes" in result:
                return result["disputes"]
            else:
                logger.warning(f"意外的响应格式: {type(result)}")
                return []
        except Exception as e:
            logger.error(f"争辩焦点提取失败: {e}")
            return []

    def _build_system_prompt(self) -> str:
        """构建固定系统提示词"""
        return """你是一位专利审查意见分析专家，负责提取审查员与申请人的核心争论点。

你必须在内部按以下步骤进行思考，但不要输出任何思考过程，只输出最终JSON：
1. 先逐条整理申请人陈述：
   - 从 response 中提取申请人按编号列举的每一项区别技术特征（如“1.”“2.”“3.”）。
   - 同时提取“对于区别技术特征X”对应的逐条论证段落。
   - 形成“申请人逐条陈述清单”，不得漏项。
2. 再逐条回溯审查员意见：
   - 对清单中的每一项，在 paragraphs 中定位对应 claim 段落与审查员原始说理。
   - 提取审查员观点，并整理支撑文献关联 supporting_docs（doc_id + cited_text）。
3. 逐条对齐并输出争点：
   - 每个申请人条目至少映射到一个 dispute；若一个条目包含多个独立冲突，可拆为多个 dispute。
   - 对“对比文件结合启示/结合动机/技术障碍”类反驳，必须单列 logic_dispute。
4. 输出前进行覆盖校验（强制）：
   - response 中编号条目全部被 disputes 覆盖；
   - “对于区别技术特征X”段落全部被覆盖；
   - 不得出现“申请人已论证但 disputes 未体现”的遗漏。

输出要求：
1. 只输出 JSON 对象，不要额外说明。
2. 使用以下结构：
{
  "disputes": [
    {
      "original_claim_id": "1",
      "dispute_id": "DSP_1_a1b2c3d4",
      "feature_text": "具体技术特征",
      "examiner_opinion": {
        "type": "document_based",
        "supporting_docs": [
          {
            "doc_id": "D1",
            "cited_text": "面板定位架100，用于固定待检测的柜内机出风面板"
          },
          {
            "doc_id": "D2",
            "cited_text": "提供转速、周期等参数可配置的手动、自动、组合控制方法"
          }
        ],
        "reasoning": "审查员认为该特征由D1公开且可由D2补充得到"
      },
      "applicant_opinion": {
        "type": "fact_dispute",
        "reasoning": "申请人主张D1未公开该特征，D2也不支持组合",
        "core_conflict": "D1是否公开‘A与B的联动控制关系’"
      }
    }
  ]
}

字段约束：
- examiner_opinion.type 只能是 "document_based"、"common_knowledge_based" 或 "mixed_basis"
- applicant_opinion.type 只能是 "fact_dispute" 或 "logic_dispute"
- supporting_docs 的 doc_id 必须优先使用 comparison_documents 的 document_id（如 D1、D2）
- 当 examiner_opinion.type="common_knowledge_based" 时，supporting_docs 必须为 []
- 当 examiner_opinion.type="document_based" 或 "mixed_basis" 时，supporting_docs 应至少包含一个 doc_id
- supporting_docs 每项必须包含 doc_id 和 cited_text
- cited_text 必须严格来自审查意见原文中“对比文件X公开了：”之后的内容，逐字摘录，不得改写、缩写、总结或添加省略号
- 每个争点必须包含 original_claim_id、feature_text、examiner_opinion、applicant_opinion

original_claim_id 取值与来源规则（强约束）：
- original_claim_id 必须是单个权利要求编号（数字字符串，如 "1"、"2"）。
- 必须优先使用 paragraphs[*].claim_ids 作为来源；禁止凭空臆测 claim 编号。
- 若 paragraphs[*].claim_ids 为空，可从对应段落正文中的“权利要求X”表达提取；仍无法确定时，该争点不得输出。

拆分规则（强约束）：
- 争点的语义核心是 feature_text，但输出单元必须是“单 claim + 单 feature_text”。
- 当同一 feature_text 关联多个权利要求（例如 claim_ids=[1,2,3]、或文本表达“权利要求1-3”）时，必须拆分为多条 disputes：
  - original_claim_id="1" + 同一 feature_text
  - original_claim_id="2" + 同一 feature_text
  - original_claim_id="3" + 同一 feature_text
- 不允许在一条 dispute 中放多个 original_claim_id。
- 对同一 (original_claim_id, feature_text) 组合不得重复输出。

一致性规则：
- examiner_opinion 与 applicant_opinion 必须与该 original_claim_id 对应段落保持一致，不得跨段落错配。
- 若同一 feature_text 在多个 claim 上共享同一审查员理由/申请人反驳，可在拆分后复用观点内容。"""

    def _build_user_prompt(self, prepared_materials: Dict[str, Any]) -> str:
        """构建动态用户提示词（仅包含上下文数据）。"""
        office_action = self._to_dict(prepared_materials.get("office_action", {}))
        response = self._to_dict(prepared_materials.get("response", {}))
        comparison_documents = prepared_materials.get("comparison_documents", [])

        paragraphs_json = json.dumps(office_action.get("paragraphs", []), ensure_ascii=False, indent=2)
        response_excerpt = str(response.get("content", ""))
        comparison_context = []
        for item in comparison_documents:
            if isinstance(item, dict):
                comparison_context.append({
                    "document_id": item.get("document_id", ""),
                    "document_number": item.get("document_number", ""),
                    "is_patent": item.get("is_patent", False)
                })
        comparison_context_json = json.dumps(comparison_context, ensure_ascii=False, indent=2)

        return f"""输入信息：
【审查意见段落 paragraphs】
{paragraphs_json if paragraphs_json else "[]"}

【申请人 response】
{response_excerpt if response_excerpt else "未提供"}

【对比文件上下文 comparison_documents（用于关联 supporting_docs.doc_id）】
{comparison_context_json if comparison_context_json else "[]"}

请特别注意：
1. 必须对 response 中编号条目（如 1/2/3）与“对于区别技术特征X”段落逐条回溯，不得遗漏。
2. 每条输出都要能在 paragraphs 中找到对应审查员说理依据。"""

    def _validate_disputes(self, disputes: List, valid_doc_ids: set[str]) -> List[Dict]:
        """验证和修复争辩焦点数据"""
        valid_disputes = []

        for i, dispute in enumerate(disputes):
            try:
                # 验证基本字段
                if not isinstance(dispute, dict):
                    logger.warning(f"第{i+1}个争辩焦点格式错误: 不是字典")
                    continue

                # 检查必需字段
                if "original_claim_id" not in dispute or "feature_text" not in dispute:
                    logger.warning(f"第{i+1}个争辩焦点缺少必需字段")
                    continue

                examiner_opinion = dispute.get("examiner_opinion", {})
                applicant_opinion = dispute.get("applicant_opinion", {})
                if not isinstance(examiner_opinion, dict) or not isinstance(applicant_opinion, dict):
                    logger.warning(f"第{i+1}个争辩焦点观点字段格式错误")
                    continue

                examiner_type = str(examiner_opinion.get("type", "")).strip()
                if examiner_type not in {"document_based", "common_knowledge_based", "mixed_basis"}:
                    logger.warning(f"第{i+1}个争辩焦点 examiner_opinion.type 非法: {examiner_type}")
                    continue

                applicant_type = str(applicant_opinion.get("type", "")).strip()
                if applicant_type not in {"fact_dispute", "logic_dispute"}:
                    logger.warning(f"第{i+1}个争辩焦点 applicant_opinion.type 非法: {applicant_type}")
                    continue

                supporting_docs_raw = examiner_opinion.get("supporting_docs", [])
                if not isinstance(supporting_docs_raw, list):
                    supporting_docs_raw = []

                normalized_supporting_docs = []
                for item in supporting_docs_raw:
                    if not isinstance(item, dict):
                        continue
                    doc_id_value = str(item.get("doc_id", "")).strip()
                    if not doc_id_value:
                        continue
                    if valid_doc_ids and doc_id_value not in valid_doc_ids:
                        continue
                    cited_text_value = str(item.get("cited_text", "")).strip()
                    normalized_supporting_docs.append({
                        "doc_id": doc_id_value,
                        "cited_text": cited_text_value,
                    })

                deduped_supporting_docs = []
                seen_doc_ids = set()
                for item in normalized_supporting_docs:
                    doc_id = str(item.get("doc_id", "")).strip()
                    if not doc_id or doc_id in seen_doc_ids:
                        continue
                    seen_doc_ids.add(doc_id)
                    deduped_supporting_docs.append(item)
                normalized_supporting_docs = deduped_supporting_docs

                if examiner_type == "common_knowledge_based":
                    normalized_supporting_docs = []
                else:
                    if not normalized_supporting_docs:
                        fallback_doc_ids = self._extract_doc_ids_from_text(
                            " ".join(
                                [
                                    str(examiner_opinion.get("reasoning", "")),
                                    str(dispute.get("feature_text", "")),
                                ]
                            ),
                            valid_doc_ids,
                        )
                        normalized_supporting_docs = [
                            {"doc_id": doc_id, "cited_text": ""}
                            for doc_id in fallback_doc_ids
                        ]
                    if not normalized_supporting_docs:
                        logger.warning(f"第{i+1}个争辩焦点 document_based/mixed_basis 但 supporting_docs 为空")

                valid_disputes.append({
                    "dispute_id": self._build_dispute_id(
                        dispute.get("dispute_id", ""),
                        str(dispute.get("original_claim_id", "")).strip(),
                        str(dispute.get("feature_text", "")).strip(),
                    ),
                    "original_claim_id": str(dispute.get("original_claim_id", "")).strip(),
                    "feature_text": str(dispute.get("feature_text", "")).strip(),
                    "examiner_opinion": {
                        "type": examiner_type,
                        "supporting_docs": normalized_supporting_docs,
                        "reasoning": str(examiner_opinion.get("reasoning", "")).strip()
                    },
                    "applicant_opinion": {
                        "type": applicant_type,
                        "reasoning": str(applicant_opinion.get("reasoning", "")).strip(),
                        "core_conflict": str(applicant_opinion.get("core_conflict", "")).strip()
                    }
                })

            except Exception as e:
                logger.warning(f"验证第{i+1}个争辩焦点时出错: {e}")

        return valid_disputes

    def _extract_doc_ids_from_text(self, text: str, valid_doc_ids: set[str]) -> List[str]:
        doc_ids: List[str] = []
        for match in re.finditer(r"(?:对比文件\s*|D\s*)(\d+)", text or "", re.I):
            value = f"D{match.group(1)}"
            if valid_doc_ids and value not in valid_doc_ids:
                continue
            if value not in doc_ids:
                doc_ids.append(value)
        return doc_ids

    def _build_dispute_id(self, raw_dispute_id: Any, original_claim_id: str, feature_text: str) -> str:
        """生成稳定的 dispute_id。"""
        provided = str(raw_dispute_id or "").strip()
        if provided:
            return provided

        digest = hashlib.md5(feature_text.encode("utf-8")).hexdigest()[:8]
        claim_part = original_claim_id or "UNKNOWN"
        return f"DSP_{claim_part}_{digest}"

    def _state_get(self, state, key: str, default=None):
        """兼容 dict 与对象状态读取。"""
        if isinstance(state, dict):
            return state.get(key, default)
        return getattr(state, key, default)

    def _to_dict(self, item: Any) -> Dict[str, Any]:
        """统一转换为 dict。"""
        if isinstance(item, dict):
            return item
        if hasattr(item, "model_dump"):
            return item.model_dump()
        if hasattr(item, "dict"):
            return item.dict()
        return {}
