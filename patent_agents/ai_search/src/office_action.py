"""Office action drafting helpers for AI Search."""

from __future__ import annotations

import json
from typing import Any

from patent_agents.common.utils.llm import get_llm_service

from .reporting import (
    document_date,
    document_evidence_text,
    document_identifier,
    document_key_passages,
    document_source_label,
    document_title,
)

FORBIDDEN_OUTPUT_PHRASES = ("AI认为", "AI 认为", "建议", "可能可以", "草稿")


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_claim_ids(value: Any) -> list[str]:
    candidates = value if isinstance(value, list) else [value]
    outputs: list[str] = []
    for raw in candidates:
        text = _safe_text(raw)
        if not text:
            continue
        for piece in text.replace("，", ",").replace("、", ",").split(","):
            item = piece.strip()
            if item and item not in outputs:
                outputs.append(item)
    return outputs


def comparison_documents_from_selected(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for index, doc in enumerate(selected, start=1):
        documents.append(
            {
                "doc_id": f"D{index}",
                "title": document_title(doc, index),
                "identifier": document_identifier(doc),
                "source_label": document_source_label(doc),
                "publication_date": document_date(doc),
                "url": _safe_text(doc.get("url")),
                "evidence_summary": document_evidence_text(doc, 700),
                "key_passages": document_key_passages(doc),
            }
        )
    return documents


def build_office_action_input(
    *,
    session_id: str,
    title: str,
    source_context: dict[str, Any],
    selected_documents: list[dict[str, Any]],
    report_text: str,
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "session_title": title,
        "target": {
            "application_number": _safe_text(source_context.get("source_pn")),
            "title": _safe_text(source_context.get("source_title")) or title,
            "source_task_id": _safe_text(source_context.get("source_task_id")),
        },
        "selected_documents": comparison_documents_from_selected(selected_documents),
        "search_conclusion": _safe_text(report_text),
    }


def _assert_formal_output_text(text: str) -> None:
    normalized = _safe_text(text)
    for phrase in FORBIDDEN_OUTPUT_PHRASES:
        if phrase in normalized:
            raise ValueError(f"审查意见通知书正文包含禁用表述：{phrase}")


def _normalize_comparison_documents(raw: Any, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_items = raw if isinstance(raw, list) else []
    outputs: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        outputs.append(
            {
                "doc_id": _safe_text(item.get("doc_id")) or f"D{index}",
                "title": _safe_text(item.get("title")) or "-",
                "identifier": _safe_text(item.get("identifier")) or "-",
                "publication_date": _safe_text(item.get("publication_date") or item.get("application_date")) or "-",
                "url": _safe_text(item.get("url")),
            }
        )
    return outputs or [
        {
            "doc_id": _safe_text(item.get("doc_id")) or f"D{index}",
            "title": _safe_text(item.get("title")) or "-",
            "identifier": _safe_text(item.get("identifier")) or "-",
            "publication_date": _safe_text(item.get("publication_date")) or "-",
            "url": _safe_text(item.get("url")),
        }
        for index, item in enumerate(fallback, start=1)
    ]


def _normalize_body_sections(raw: Any) -> list[dict[str, Any]]:
    raw_items = raw if isinstance(raw, list) else []
    outputs: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        text = _safe_text(item.get("text"))
        if not text:
            continue
        _assert_formal_output_text(text)
        outputs.append(
            {
                "claim_ids": _normalize_claim_ids(item.get("claim_ids")),
                "legal_basis": _safe_text(item.get("legal_basis")) or "专利法第二十二条",
                "defect_type": _safe_text(item.get("defect_type")) or "新颖性/创造性",
                "text": text,
                "evidence_refs": [
                    _safe_text(ref)
                    for ref in (item.get("evidence_refs") if isinstance(item.get("evidence_refs"), list) else [])
                    if _safe_text(ref)
                ],
            }
        )
    return outputs


def normalize_office_action_payload(raw: dict[str, Any], input_payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    target = input_payload.get("target") if isinstance(input_payload.get("target"), dict) else {}
    selected_documents = input_payload.get("selected_documents") if isinstance(input_payload.get("selected_documents"), list) else []
    bibliographic = raw.get("bibliographic") if isinstance(raw.get("bibliographic"), dict) else {}
    body_sections = _normalize_body_sections(raw.get("office_action_body") or raw.get("body_sections"))
    manual_review_items = [
        _safe_text(item)
        for item in (raw.get("manual_review_items") if isinstance(raw.get("manual_review_items"), list) else [])
        if _safe_text(item)
    ]
    for item in manual_review_items:
        _assert_formal_output_text(item)

    if not body_sections:
        manual_review_items.append("现有已选文献尚不足以形成针对具体权利要求的完整审查意见，需人工补充权利要求对比。")

    target_files = _safe_text(raw.get("target_files")) or "本通知书针对申请人于【提交日】提交的权利要求书、说明书及其摘要。"
    conclusion = _safe_text(raw.get("conclusion")) or "经审查，上述权利要求存在不符合专利法及其实施细则有关规定的缺陷。申请人应当在指定期限内对上述审查意见作出答复。"
    response_deadline = _safe_text(raw.get("response_deadline")) or "答复期限：【指定期限】。期满未答复的，本申请将依照专利法及其实施细则的有关规定处理。"
    _assert_formal_output_text(target_files)
    _assert_formal_output_text(conclusion)
    _assert_formal_output_text(response_deadline)

    return {
        "schema_version": "ai_search.office_action.v1",
        "title": _safe_text(raw.get("title")) or "审查意见通知书",
        "bibliographic": {
            "application_number": _safe_text(bibliographic.get("application_number")) or _safe_text(target.get("application_number")) or "【申请号】",
            "application_title": _safe_text(bibliographic.get("application_title")) or _safe_text(target.get("title")) or "【发明名称】",
            "applicant": _safe_text(bibliographic.get("applicant")) or "【申请人】",
        },
        "target_files": target_files,
        "comparison_documents": _normalize_comparison_documents(raw.get("comparison_documents"), selected_documents),
        "office_action_body": body_sections,
        "conclusion": conclusion,
        "response_deadline": response_deadline,
        "manual_review_items": list(dict.fromkeys(manual_review_items)),
        "source": {
            "session_id": _safe_text(input_payload.get("session_id")),
            "source_task_id": _safe_text(target.get("source_task_id")),
        },
    }


def build_office_action_messages(input_payload: dict[str, Any]) -> list[dict[str, str]]:
    system_prompt = """你是一位资深的中国国家知识产权局专利实质审查员。你的任务是基于 AI 检索会话中的目标专利、已选对比文件和检索结论，生成可直接进入《审查意见通知书》的正式正文结构。

必须遵守：
1. 采用中国专利审查意见通知书的客观、确定、规范语气。
2. 正文必须包含：通知书针对的申请文件、具体缺陷、对应法条、事实与证据分析、倾向性审查意见、答复期限。
3. 优先生成新颖性或创造性意见；证据不足的权利要求不得强写驳回理由，应写入 manual_review_items。
4. 只能使用输入中的目标专利信息、对比文件编号、证据摘要、关键片段和检索结论，不得捏造文献公开内容。
5. 禁止在输出中出现“AI认为”“建议”“可能可以”“草稿”等内部或商榷性表述。
6. 必须使用正式句式，例如“权利要求1不具备专利法第二十二条第三款规定的创造性”“对比文件D1公开了……，相当于本申请权利要求中的……”“申请人应当在指定期限内对上述审查意见作出答复”。

你必须且只能输出合法 JSON，不要输出 Markdown 或解释。"""
    user_prompt = """请基于以下 JSON 生成审查意见通知书结构化数据。

输出 JSON 格式：
{
  "title": "审查意见通知书",
  "bibliographic": {
    "application_number": "...",
    "application_title": "...",
    "applicant": "【申请人】"
  },
  "target_files": "本通知书针对申请人于【提交日】提交的权利要求书、说明书及其摘要。",
  "comparison_documents": [
    {"doc_id": "D1", "title": "...", "identifier": "...", "publication_date": "...", "url": "..."}
  ],
  "office_action_body": [
    {
      "claim_ids": ["1"],
      "legal_basis": "专利法第二十二条第三款",
      "defect_type": "创造性",
      "text": "权利要求1不具备专利法第二十二条第三款规定的创造性。对比文件D1公开了……，相当于本申请权利要求1中的……。区别技术特征在于……。基于……，本领域技术人员容易想到……。因此，权利要求1不具备突出的实质性特点和显著的进步。",
      "evidence_refs": ["D1"]
    }
  ],
  "conclusion": "经审查，上述权利要求存在不符合专利法及其实施细则有关规定的缺陷。申请人应当在指定期限内对上述审查意见作出答复。",
  "response_deadline": "答复期限：【指定期限】。期满未答复的，本申请将依照专利法及其实施细则的有关规定处理。",
  "manual_review_items": []
}

输入 JSON：
""" + json.dumps(input_payload, ensure_ascii=False, indent=2)
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def generate_office_action_payload(input_payload: dict[str, Any], *, llm_service: Any = None) -> dict[str, Any]:
    service = llm_service or get_llm_service()
    raw = service.invoke_text_json(
        messages=build_office_action_messages(input_payload),
        task_kind="ai_search_office_action_drafting",
        temperature=0.1,
    )
    return normalize_office_action_payload(raw, input_payload)
