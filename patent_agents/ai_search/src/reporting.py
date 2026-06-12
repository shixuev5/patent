"""Report rendering helpers for AI Search artifacts."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any


def latest_report_text(messages: list[dict[str, Any]]) -> str:
    for item in reversed(messages):
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        if (
            str(item.get("role") or "").strip() == "assistant"
            and str(metadata.get("message_variant") or "").strip() == "search_report"
            and str(item.get("content") or "").strip()
        ):
            return str(item.get("content") or "").strip()
    for item in reversed(messages):
        if str(item.get("role") or "").strip() == "assistant" and str(item.get("content") or "").strip():
            return str(item.get("content") or "").strip()
    return ""


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _truncate(value: Any, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", _safe_text(value))
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


def _table_text(value: Any) -> str:
    return _safe_text(value).replace("\n", "<br>").replace("|", "\\|") or "-"


def document_identifier(doc: dict[str, Any]) -> str:
    return (
        _safe_text(doc.get("pn"))
        or _safe_text(doc.get("doi"))
        or _safe_text(doc.get("external_id"))
        or _safe_text(doc.get("canonical_id"))
        or "-"
    )


def document_source_label(doc: dict[str, Any]) -> str:
    source_type = _safe_text(doc.get("source_type"))
    detail_source = _safe_text(doc.get("detail_source"))
    if source_type == "user_pdf":
        return "用户补充PDF"
    if detail_source.startswith("user_") or doc.get("user_pinned"):
        return "用户补充文献"
    if source_type == "patent" or _safe_text(doc.get("pn")):
        return "专利文献"
    if _safe_text(doc.get("doi")):
        return "论文文献"
    return source_type or "文献"


def document_date(doc: dict[str, Any]) -> str:
    return _safe_text(doc.get("publication_date") or doc.get("application_date")) or "-"


def document_title(doc: dict[str, Any], index: int = 0) -> str:
    fallback = f"文献 {index}" if index > 0 else "未命名文献"
    return _safe_text(doc.get("title") or doc.get("pn") or doc.get("doi") or doc.get("external_id") or fallback)


def document_evidence_text(doc: dict[str, Any], limit: int = 700) -> str:
    candidates = [
        doc.get("evidence_summary"),
        doc.get("close_read_reason"),
        doc.get("agent_reason"),
        doc.get("coarse_reason"),
        doc.get("abstract"),
    ]
    for value in candidates:
        text = _safe_text(value)
        if text:
            return _truncate(text, limit)
    return "暂无命中说明。"


def document_key_passages(doc: dict[str, Any], *, limit: int = 4) -> list[str]:
    passages = doc.get("key_passages_json")
    if not isinstance(passages, list):
        return []
    outputs: list[str] = []
    for item in passages:
        if isinstance(item, dict):
            text = _safe_text(
                item.get("text")
                or item.get("passage")
                or item.get("content")
                or item.get("quote")
            )
        else:
            text = _safe_text(item)
        if text:
            outputs.append(_truncate(text, 260))
        if len(outputs) >= limit:
            break
    return outputs


def render_markdown_report(payload: dict[str, Any]) -> str:
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    source = payload.get("sourceContext") if isinstance(payload.get("sourceContext"), dict) else {}
    stop_policy = payload.get("stopPolicy") if isinstance(payload.get("stopPolicy"), dict) else {}
    selected = payload.get("selectedDocuments") if isinstance(payload.get("selectedDocuments"), list) else []
    candidates = payload.get("candidateDocuments") if isinstance(payload.get("candidateDocuments"), list) else []
    trace_summary = payload.get("traceSummary") if isinstance(payload.get("traceSummary"), list) else []
    report_text = _safe_text(payload.get("report")) or "暂无阶段性结论。"

    lines = [
        f"# {_safe_text(payload.get('title')) or 'AI 检索报告'}",
        "",
        "## 检索目标",
        f"- 目标专利：{_safe_text(source.get('source_pn')) or '-'}",
        f"- 专利名称：{_safe_text(source.get('source_title')) or '-'}",
        f"- 覆盖问题：{_safe_text(stop_policy.get('target_coverage')) or '-'}",
        f"- 停止条件：{_safe_text(stop_policy.get('stop_when')) or '-'}",
        "",
        "## 统计概览",
        f"- 检索轮次：{stats.get('searchRounds', 0)}",
        f"- 检索式数量：{stats.get('queryCount', 0)}",
        f"- 候选文献：{stats.get('candidateCount', 0)}",
        f"- 已选文献：{stats.get('selectedCount', 0)}",
        "",
        "## 检索结论与缺口",
        report_text,
        "",
        "## 已选证据",
    ]

    if not selected:
        lines.append("- 暂无已选文献。")
    for index, doc in enumerate(selected, start=1):
        title = document_title(doc, index)
        identifier = document_identifier(doc)
        passages = document_key_passages(doc)
        lines.extend(
            [
                f"### {index}. {title}",
                f"- 标识：{identifier}",
                f"- 来源：{document_source_label(doc)}",
                f"- 日期：{document_date(doc)}",
                f"- 链接：{_safe_text(doc.get('url')) or '-'}",
                "",
                document_evidence_text(doc),
                "",
            ]
        )
        if passages:
            lines.append("关键片段：")
            for passage in passages:
                lines.append(f"- {passage}")
            lines.append("")

    lines.extend(["## 候选概览", ""])
    if not candidates:
        lines.append("- 暂无候选文献。")
    else:
        lines.append("| 序号 | 文献 | 标识 | 来源 | 日期 | 命中说明 |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
        for index, doc in enumerate(candidates[:20], start=1):
            lines.append(
                f"| {index} | {_table_text(document_title(doc, index))} | {_table_text(document_identifier(doc))} | "
                f"{_table_text(document_source_label(doc))} | {_table_text(document_date(doc))} | "
                f"{_table_text(document_evidence_text(doc, 180))} |"
            )
        if len(candidates) > 20:
            lines.append("")
            lines.append(f"另有 {len(candidates) - 20} 篇候选文献未在概览表中展开。")
    lines.append("")

    lines.extend(["## 检索过程摘要", ""])
    if not trace_summary:
        lines.append("- 暂无检索过程摘要。")
    else:
        for item in trace_summary[:30]:
            label = _safe_text(item.get("label") if isinstance(item, dict) else item)
            detail = _safe_text(item.get("detail") if isinstance(item, dict) else "")
            if label and detail:
                lines.append(f"- {label}：{detail}")
            elif label:
                lines.append(f"- {label}")

    return "\n".join(lines).strip() + "\n"


def render_office_action_markdown(payload: dict[str, Any]) -> str:
    title = _safe_text(payload.get("title")) or "审查意见通知书"
    bibliographic = payload.get("bibliographic") if isinstance(payload.get("bibliographic"), dict) else {}
    comparison_documents = payload.get("comparison_documents") if isinstance(payload.get("comparison_documents"), list) else []
    body_sections = payload.get("office_action_body") if isinstance(payload.get("office_action_body"), list) else []
    manual_review_items = payload.get("manual_review_items") if isinstance(payload.get("manual_review_items"), list) else []
    target_files = _safe_text(payload.get("target_files")) or "本通知书针对申请人于【提交日】提交的权利要求书、说明书及其摘要。"
    conclusion = _safe_text(payload.get("conclusion")) or "申请人应当在指定期限内对上述审查意见作出答复。"
    response_deadline = _safe_text(payload.get("response_deadline")) or "答复期限：【指定期限】。"

    lines = [
        f"# {title}",
        "",
        "## 著录项目",
        f"- 申请号：{_safe_text(bibliographic.get('application_number')) or '【申请号】'}",
        f"- 发明名称：{_safe_text(bibliographic.get('application_title')) or '【发明名称】'}",
        f"- 申请人：{_safe_text(bibliographic.get('applicant')) or '【申请人】'}",
        "",
        "## 一、通知书所针对的申请文件",
        target_files,
        "",
        "## 二、对比文件",
    ]

    if not comparison_documents:
        lines.append("本通知书未列明可直接用于评价权利要求的新对比文件。")
    else:
        for index, doc in enumerate(comparison_documents, start=1):
            doc_id = _safe_text(doc.get("doc_id")) or f"D{index}"
            identifier = _safe_text(doc.get("identifier")) or "-"
            doc_title = _safe_text(doc.get("title")) or "-"
            date = _safe_text(doc.get("publication_date") or doc.get("application_date")) or "-"
            lines.append(f"{doc_id}：{doc_title}，{identifier}，公开日/申请日：{date}。")
    lines.append("")

    lines.extend(["## 三、审查意见", ""])
    if not body_sections:
        lines.append("经审查，现有材料尚不足以形成针对具体权利要求的审查意见。")
    else:
        for index, section in enumerate(body_sections, start=1):
            claim_ids = section.get("claim_ids") if isinstance(section, dict) else []
            claim_text = "、".join(str(item).strip() for item in claim_ids if str(item).strip()) if isinstance(claim_ids, list) else _safe_text(claim_ids)
            heading = f"### （{index}）关于权利要求{claim_text or '【权利要求编号】'}"
            legal_basis = _safe_text(section.get("legal_basis") if isinstance(section, dict) else "")
            defect_type = _safe_text(section.get("defect_type") if isinstance(section, dict) else "")
            text = _safe_text(section.get("text") if isinstance(section, dict) else section)
            lines.append(heading)
            if legal_basis or defect_type:
                lines.append(f"审查基础：{legal_basis or '【法条】'}；缺陷类型：{defect_type or '【缺陷类型】'}。")
                lines.append("")
            lines.append(text or "【待补充具体审查意见】")
            lines.append("")

    lines.extend(
        [
            "## 四、结论及答复期限",
            conclusion,
            "",
            response_deadline,
        ]
    )
    if manual_review_items:
        lines.extend(["", "## 五、需人工补充审查事项"])
        for item in manual_review_items:
            lines.append(f"- {_safe_text(item) or '-'}")

    return "\n".join(lines).strip() + "\n"


def write_selected_documents_csv(path: Path, selected: list[dict[str, Any]]) -> None:
    fields = [
        "title",
        "identifier",
        "source_label",
        "publication_date",
        "url",
        "agent_reason",
        "evidence_summary",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for doc in selected:
            writer.writerow(
                {
                    "title": document_title(doc),
                    "identifier": document_identifier(doc),
                    "source_label": document_source_label(doc),
                    "publication_date": document_date(doc),
                    "url": _safe_text(doc.get("url")),
                    "agent_reason": _safe_text(doc.get("agent_reason")),
                    "evidence_summary": _safe_text(doc.get("evidence_summary")),
                }
            )
