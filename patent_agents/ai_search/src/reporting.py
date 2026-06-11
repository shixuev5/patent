"""Report rendering helpers for AI Search artifacts."""

from __future__ import annotations

import csv
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


def render_markdown_report(payload: dict[str, Any]) -> str:
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    lines = [
        f"# {payload.get('title') or 'AI 检索报告'}",
        "",
        "## 摘要",
        str(payload.get("report") or "暂无报告内容。").strip() or "暂无报告内容。",
        "",
        "## 统计",
        f"- 检索轮次：{stats.get('searchRounds', 0)}",
        f"- 检索式数量：{stats.get('queryCount', 0)}",
        f"- 候选文献：{stats.get('candidateCount', 0)}",
        f"- 已选文献：{stats.get('selectedCount', 0)}",
        "",
        "## 已选文献",
    ]
    selected = payload.get("selectedDocuments") if isinstance(payload.get("selectedDocuments"), list) else []
    if not selected:
        lines.append("- 暂无已选文献。")
    for index, doc in enumerate(selected, start=1):
        title = str(doc.get("title") or doc.get("pn") or doc.get("doi") or doc.get("external_id") or f"文献 {index}").strip()
        identifier = str(doc.get("pn") or doc.get("doi") or doc.get("external_id") or "").strip()
        reason = str(doc.get("evidence_summary") or doc.get("agent_reason") or doc.get("abstract") or "").strip()
        lines.extend(
            [
                f"### {index}. {title}",
                f"- 标识：{identifier or '-'}",
                f"- 来源：{str(doc.get('source_type') or '-').strip() or '-'}",
                f"- 日期：{str(doc.get('publication_date') or doc.get('application_date') or '-').strip() or '-'}",
                f"- 链接：{str(doc.get('url') or '-').strip() or '-'}",
                "",
                reason or "暂无命中说明。",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def write_selected_documents_csv(path: Path, selected: list[dict[str, Any]]) -> None:
    fields = ["title", "pn", "doi", "external_id", "source_type", "publication_date", "url", "agent_reason", "evidence_summary"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for doc in selected:
            writer.writerow({field: str(doc.get(field) or "") for field in fields})
