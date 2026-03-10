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
                "extract_disputes_v5",
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
            result = self.llm_service.invoke_text_json(
                messages=messages,
                task_kind="oar_dispute_extraction",
                temperature=0.05,
            )
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
        return """你是一位顶级的专利审查意见分析专家。你的任务是精准分析【审查意见通知书】与【申请人意见陈述书】，提取出双方的核心“争点（Disputes）”。

### 核心处理步骤（请在内部严格按此逻辑思考）
1. **穷尽提取申请人论点**：通读 <applicant_response>，寻找所有的“区别技术特征”论述、编号序列（如1. 2. 3.）、以及“综上所述...”之前的具体反驳段落。绝不能遗漏任何一个技术点！
2. **精准回溯审查员观点**：针对申请人的每个论点，在 <office_action> 中寻找对应的段落。提取审查员是基于哪些对比文件（或公知常识）对该特征做出了评述。
3. **聚合与对齐（争点中心 1:n 原则）**：
   - 争点必须细化到【单一技术特征 (feature_text)】。
   - 每条争点可以关联多个权利要求，用 `claim_ids` 表示（例如 `["1","2","3"]`）。
   - 如果申请人既反驳了“事实认定（对比文件未公开特征X）”，又反驳了“逻辑结合（无动机结合对比文件1和2）”，请合并在 `applicant_opinion` 中清晰表述，或视情况拆分为独立争点。

### 数据字典与约束规则（强制执行）

#### 1. examiner_opinion (审查员观点)
- `type`: 仅限枚举["document_based" (基于对比文件), "common_knowledge_based" (基于公知常识/惯用手段), "mixed_basis" (文件+公知常识)]
- `supporting_docs`: 列表。如果是 common_knowledge_based 必须为空[]。如果是其他两者，必须包含对应的文档引用。
  - `doc_id`: 必须标准化为 "D1", "D2" 等格式（参考上下文提供的对比文件清单）。
  - `cited_text`: **必须从审查意见原文中逐字摘抄**。寻找类似“对比文件1公开了：...”之后的文字。禁止篡改、缩写或总结！如果原文没有明确引用文字，填 ""。

#### 2. applicant_opinion (申请人观点)
- `type`: 仅限枚举["fact_dispute" (事实争议：如认为D1未公开某特征), "logic_dispute" (逻辑争议：如认为无结合启示、有技术障碍)]。如果两者都有，优先使用 "fact_dispute"。
- `core_conflict`: 提炼一句话核心冲突，例如：“D1是否公开了A与B的联动控制关系” 或 “D1和D2是否存在结合启示”。

#### 3. claim_ids
- **必须是纯数字字符串数组**（例如 `["1", "2"]`）。
- 每个元素不能出现 "1-3"、"权利要求1" 这类文本。
- 如果找不到确切的权利要求编号，该争点判定为无效，不要输出。

### ⚠️ 常见错误避坑指南（Negative Constraints）
- **禁止遗漏**：申请人答复中列出的 1/2/3 条理，必须 100% 体现在输出的 JSON 中。
- **禁止 claim_ids 非法格式**：不要输出 `"1,2"` 这种单字符串，必须输出数组 `["1","2"]`。
- **禁止捏造引用**：cited_text 如果在审查意见中找不到原话，宁可留空，绝不自己编造。

### 输出格式：严格返回如下 JSON
{
  "disputes":[
    {
      "claim_ids": ["1", "2"],
      "dispute_id": "DSP_1_2_a1b2c3d4",  // 可留空或自定义，后续系统会自动覆盖
      "feature_text": "此处填写具体的技术特征内容",
      "examiner_opinion": {
        "type": "document_based",
        "supporting_docs":[
          {
            "doc_id": "D1",
            "cited_text": "面板定位架100，用于固定待检测的柜内机出风面板"
          }
        ],
        "reasoning": "审查员认为该特征由D1公开"
      },
      "applicant_opinion": {
        "type": "fact_dispute",
        "reasoning": "申请人主张D1公开的是支撑架，并未公开可移动的定位架，两者结构与作用完全不同。",
        "core_conflict": "D1是否公开了定位架特征"
      }
    }
  ]
}
只输出 JSON 对象，不要输出任何 Markdown 代码块外的多余解释。"""

    def _build_user_prompt(self, prepared_materials: Dict[str, Any]) -> str:
        """构建动态用户提示词（仅包含上下文数据）。"""
        office_action = self._to_dict(prepared_materials.get("office_action", {}))
        response = self._to_dict(prepared_materials.get("response", {}))
        comparison_documents = prepared_materials.get("comparison_documents", [])

        # 整理对比文件
        comparison_context =[]
        for item in comparison_documents:
            if isinstance(item, dict):
                comparison_context.append({
                    "id_for_json": item.get("document_id", ""),
                    "title_or_num": item.get("document_number", "")
                })

        return f"""请根据以下输入材料提取争辩焦点：

<comparison_docs>
（在 supporting_docs.doc_id 中请优先使用以下 id_for_json）
{json.dumps(comparison_context, ensure_ascii=False, indent=2) if comparison_context else "未提供对比文件列表"}
</comparison_docs>

<office_action>
{json.dumps(office_action.get("paragraphs",[]), ensure_ascii=False, indent=2)}
</office_action>

<applicant_response>
{response.get("content", "未提供答复文本")}
</applicant_response>

请深呼吸，仔细对照 <applicant_response> 和 <office_action>，严格遵守 JSON 格式输出所有的争辩焦点。确保不遗漏任何一个 claim 的反驳！"""

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
                if "claim_ids" not in dispute or "feature_text" not in dispute:
                    logger.warning(f"第{i+1}个争辩焦点缺少必需字段")
                    continue

                claim_ids = self._normalize_claim_ids(dispute.get("claim_ids"))
                if not claim_ids:
                    logger.warning(f"第{i+1}个争辩焦点 claim_ids 非法或为空")
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
                        claim_ids,
                        str(dispute.get("feature_text", "")).strip(),
                    ),
                    "claim_ids": claim_ids,
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

    def _build_dispute_id(self, raw_dispute_id: Any, claim_ids: List[str], feature_text: str) -> str:
        """生成稳定的 dispute_id。"""
        provided = str(raw_dispute_id or "").strip()
        if provided:
            return provided

        digest = hashlib.md5(feature_text.encode("utf-8")).hexdigest()[:8]
        claim_part = "_".join(claim_ids[:4]) if claim_ids else "UNKNOWN"
        return f"DSP_{claim_part}_{digest}"

    def _normalize_claim_ids(self, value: Any) -> List[str]:
        claim_ids: List[str] = []
        candidates = value if isinstance(value, list) else [value]
        for raw in candidates:
            text = str(raw or "").strip()
            if not text:
                continue
            for piece in re.split(r"[，,、\s]+", text):
                piece = piece.strip()
                if not piece or not piece.isdigit():
                    continue
                if piece not in claim_ids:
                    claim_ids.append(piece)
        return claim_ids

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
