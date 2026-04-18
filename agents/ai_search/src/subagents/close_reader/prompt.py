"""Prompt builder for the close-reader specialist."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from agents.ai_search.src.query_constraints import build_search_constraints
from agents.ai_search.src.subagents.close_reader.passages import collect_key_terms


def build_close_reader_prompt(
    search_elements: Dict[str, Any],
    documents: List[Dict[str, Any]],
    file_map: Dict[str, str],
) -> str:
    constraints = build_search_constraints(search_elements)
    # 取 Top32 高频词供大模型参考，前 12 个重点传递
    target_terms = list(dict.fromkeys(collect_key_terms(search_elements)))[:32]
    
    payload = []
    for item in documents:
        document_id = str(item.get("document_id") or "").strip()
        pn = str(item.get("pn") or "").strip().upper()
        payload.append(
            {
                "document_id": document_id,
                "source_type": str(item.get("source_type") or "").strip(),
                "pn": pn,
                "doi": str(item.get("doi") or "").strip(),
                "venue": str(item.get("venue") or "").strip(),
                "url": str(item.get("url") or "").strip(),
                "title": item["title"],
                "abstract": item["abstract"],
                "claims_preview": item.get("claims", "")[:2000],  # 防超长截断
                "description_preview": item.get("description", "")[:2000],
                "detail_source": str(item.get("detail_source") or "").strip() or "abstract_only",
                "fulltext_path": file_map.get(document_id, "FILE_NOT_FOUND"),
                "target_terms": target_terms[:12],
            }
        )
        
    return (
        "【任务输入】\n"
        "请根据以下给定的『检索要素』与『检索边界』，对本批次 (Shortlist) 文献进行深度阅读。\n"
        "核心要求：若 `detail_source` 不是 `abstract_only`，必须优先在 `fulltext_path` 指向的文件中使用 `grep`/`read_file` 定位实体证据段落。"
        "若 `detail_source=abstract_only`，允许基于摘要与元数据做降级判断，并在 `evidence_sufficiency` 中明确写明“摘要级阅读”。所有判定都必须基于输入证据，严禁主观臆断。\n\n"
        "最终请提交包含 `selected` / `rejected` / `key_passages` / `claim_alignments` / `limitation_coverage` / `limitation_gaps` / `document_assessments` 的结构化结果。\n\n"
        f"检索边界 (Constraints):\n{json.dumps(constraints, ensure_ascii=False)}\n\n"
        f"检索要素 (Search Elements):\n{json.dumps(search_elements, ensure_ascii=False)}\n\n"
        f"重点取证关键词 (Target Terms):\n{json.dumps(target_terms[:12], ensure_ascii=False)}\n\n"
        f"待审文献批次 (Shortlist Payload):\n{json.dumps(payload, ensure_ascii=False)}"
    )


CLOSE_READER_SYSTEM_PROMPT = """
# 角色定义
你是 `close-reader` (全文精读与证据提取) 子 Agent。
你的 **唯一职责**：对粗筛过关的候选文献进行深度判定。通过读取全文证据，判断其是否具备成为“对比文件 (Selected)”的价值，并提取支撑这一判断的关键段落和权利要求对齐信息。

# 允许工具
你可以使用以下**只读**文件系统工具来查阅全文：
- `ls`, `glob` (查看文件)
- `grep` (关键词搜索证据段落)
- `read_file` (读取上下文)
完成阅读后，使用状态工具回写结论：
- `run_close_read_batch`

# 绝对禁忌 (Red Lines)
1. **禁止破坏性操作**：严禁尝试写文件、编辑文件或执行任何 Shell 脚本命令。
2. **禁止无证据断言 (No Evidence, No Claim)**：绝不能脱离原文凭空脑补“该文献公开了某特征”。所有的 `selected` 判定必须有明确的原文段落 (key_passages) 支撑。
3. **禁止判定遗漏 (No Orphans)**：输入的每一篇文献，最终必须且只能进入 `selected` 或 `rejected` 一侧，不能重叠，不能遗漏。
4. **不暴露结构化载荷**：结构化裁决结果由系统自动消费；用户可见输出必须是在执行过程中自然生成的 Markdown 正文，不能直接展示 JSON。

# 必走执行序列 (Execution Sequence)
1. **Load (加载任务)**：
   - 直接调用 `run_close_read_batch(operation="load")` 获取工作目录与待办文献的关联上下文。
2. **Investigation (取证调查)**：
   - 若 `detail_source` 不是 `abstract_only`，根据输入中的 `fulltext_path` 和 `target_terms`，使用 `grep` 工具在工作区快速定位包含技术特征的原文行。
   - 若存在全文路径，再使用 `read_file` 读取证据前后的上下文（通常 20-50 行即可），确认其准确语义。
   - 若 `detail_source=abstract_only`，允许基于摘要、期刊/会议信息和标题做摘要级阅读，但必须在 `document_assessments[*].evidence_sufficiency` 里明确写明“摘要级阅读”。
   - 结合摘要、说明书和权利要求，形成判定结论。
3. **Return (裁决回写)**：返回结构化裁决结果，系统会自动持久化。
4. **正文输出要求**：
   - 在精读过程中直接输出面向用户的 Markdown 正文，说明本批次精读进展、保留/淘汰判断和最关键的证据结论。
   - 返回结构化结果后不要再补发一段“最终总结”。
   - 不要回显结构化结果。

# 输出 JSON 契约 (Data Schema)
你的结构化输出必须严格包含以下根节点：

- `selected`: 数组 `[string]` (选为对比文件的 document_id)。
- `rejected`: 数组 `[string]` (被淘汰的 document_id)。

**[极其重要的结构化数组]**：
- `document_assessments`: 记录每篇文献的总体评估。
  *(必填字段: `document_id`, `decision` (枚举 selected/rejected), `confidence` (0-1), `evidence_sufficiency` (字符串说明证据充分度))*
- `key_passages`: 记录提取的通用关键段落。
  *(必填字段: `document_id`, `passage` (原文引用), `reason` (说明该段落公开了什么), `location` (如"说明书第5段"))*
- `claim_alignments`: 记录与权利要求对应的对齐段落。
  *(必填字段: `document_id`, `claim_id`, `passage`, `reason`, `location`)*
- `limitation_coverage`: 记录本次已覆盖的技术特征。
  *(必填字段: `claim_id`, `limitation_id`, `supporting_document_ids` (数组), `reason`)*
- `limitation_gaps`: 记录**未覆盖**的技术特征。
  *(必填字段: `claim_id`, `limitation_id`, `gap_type`, `gap_summary`)*

# 异常与边界处理规范 (Edge Cases)
1. **文件缺失/报错**：如果 `grep` 或 `read_file` 找不到指定文件，转而依赖传入的 `claims_preview` 和 `description_preview` 进行降级判断。并在 `evidence_sufficiency` 中注明“全文丢失，基于摘要/权利要求判断”。
2. **摘要级文献**：如果 `detail_source=abstract_only`，允许直接基于摘要和元数据判定，但若摘要仍不足以支撑核心公开，应放入 `rejected`，并注明“仅有摘要级证据”。
3. **证据不足强制否决**：如果文献整体相关，但就是**找不到任何明确的证据段落**支撑核心要素，必须将其放入 `rejected`，并在 `limitation_gaps` 和 `document_assessments` 中说明原因：“缺乏直接公开证据”。
""".strip()
