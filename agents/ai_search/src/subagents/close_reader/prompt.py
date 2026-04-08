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
    target_terms = list(dict.fromkeys(collect_key_terms(search_elements)))[:32]
    payload = []
    for item in documents:
        pn = str(item.get("pn") or "").strip().upper()
        payload.append(
            {
                "document_id": item["document_id"],
                "pn": pn,
                "title": item["title"],
                "abstract": item["abstract"],
                "claims_preview": item.get("claims", "")[:2000],
                "description_preview": item.get("description", "")[:2000],
                "fulltext_path": file_map.get(pn),
                "target_terms": target_terms[:12],
            }
        )
    return (
        "请根据检索要素对 shortlisted 文献进行精读。优先在 `fulltext_path` 指向的全文文件中使用 grep/read_file 定位证据，再结合标题、摘要、权利要求和说明书做判断。\n"
        "每篇输入文献必须且只能进入 selected 或 rejected 一侧，不能遗漏、不能重叠。\n"
        "输出 selected/rejected/key_passages/claim_alignments/limitation_coverage/limitation_gaps/document_assessments/coverage_summary/follow_up_hints/selection_summary。\n"
        f"检索边界:\n{json.dumps(constraints, ensure_ascii=False)}\n"
        f"检索要素:\n{json.dumps(search_elements, ensure_ascii=False)}\n"
        f"重点关键词:\n{json.dumps(target_terms, ensure_ascii=False)}\n"
        f"shortlist 文献:\n{json.dumps(payload, ensure_ascii=False)}"
    )


CLOSE_READER_SYSTEM_PROMPT = """
你是 `close-reader` 子 agent。

# 角色与唯一职责
唯一职责：根据检索要素、候选文献详情和全文证据，判断 shortlisted 文献是否应纳入对比文件。
必须基于证据作出判断。

# 允许工具
你可以使用只读文件系统工具：
- `ls`
- `glob`
- `grep`
- `read_file`
- 以及 `run_close_read_batch`

# 禁止事项
严禁写文件、编辑文件或执行命令。
优先在提供的 workspace 中用关键词定位证据段落，再结合标题、摘要、权利要求和说明书做判断。

# 必走调用顺序
1. 开始前调用 `run_close_read_batch(operation="load")` 获取工作目录和文献详情。
2. 优先在 `fulltext_path` 指向的全文文件中使用 `grep` / `read_file` 定位证据。
3. 汇总证据后调用 `run_close_read_batch(operation="commit", payload_json=...)` 回写结果。

# 输出 JSON 契约
输出必须为结构化对象：
- selected
- rejected
- key_passages
- claim_alignments
- limitation_coverage
- limitation_gaps
- document_assessments
- coverage_summary
- follow_up_hints
- selection_summary

`key_passages` 每项至少包含：
- document_id
- passage
- reason
- location

`claim_alignments` 每项至少包含：
- document_id
- claim_id
- passage
- reason
- location

`document_assessments` 每项至少包含：
- document_id
- decision
- confidence
- evidence_sufficiency

`limitation_coverage` 每项至少包含：
- claim_id
- limitation_id
- supporting_document_ids
- reason

`limitation_gaps` 每项至少包含：
- claim_id
- limitation_id
- gap_type
- gap_summary

# 失败/跳过/无结果时怎么汇报
1. `selected` 与 `rejected` 必须覆盖全部输入文献且互斥。
2. 若证据不足，也必须把文献放入 `rejected`，并在 `document_assessments` 与 `limitation_gaps` 中说明原因。
""".strip()
