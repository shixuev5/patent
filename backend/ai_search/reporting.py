"""AI search report building and terminal artifact generation."""

from __future__ import annotations

import csv
import html
import json
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from agents.common.rendering.report_render import render_markdown_to_pdf, write_markdown


AI_SEARCH_REPORT_CSS = """
@page { size: A4; margin: 1.6cm 1.2cm; }
body {
  font-family: "Arial", "SimHei", "Microsoft YaHei", sans-serif !important;
  color: #111827;
  font-size: 12px;
  line-height: 1.5;
}
h1 {
  margin: 0 0 12px 0;
  border-bottom: 1px solid #111827;
  color: #111827;
  font-size: 20px;
  padding-bottom: 8px;
}
h2 {
  margin: 18px 0 8px 0;
  border-bottom: 0;
  color: #111827;
  font-size: 15px;
  text-align: center;
  letter-spacing: 0.25em;
}
.ai-search-meta,
.ai-search-records,
.ai-search-doc-table,
.ai-search-empty-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  margin-bottom: 12px;
}
.ai-search-meta td,
.ai-search-records td,
.ai-search-doc-table th,
.ai-search-doc-table td,
.ai-search-empty-table th,
.ai-search-empty-table td {
  border: 1px solid #111827;
  padding: 6px 8px;
  vertical-align: top;
  word-break: break-word;
  overflow-wrap: break-word;
}
.ai-search-doc-table tr,
.ai-search-doc-table td,
.ai-search-doc-table th,
.ai-search-empty-table tr,
.ai-search-empty-table td,
.ai-search-empty-table th {
  page-break-inside: avoid;
  break-inside: avoid;
}
.ai-search-meta td.label {
  width: 18%;
  font-weight: 700;
}
.ai-search-doc-table th,
.ai-search-empty-table th {
  background: #f3f4f6;
  text-align: center;
  font-weight: 700;
}
.ai-search-doc-table td.center,
.ai-search-empty-table td.center {
  text-align: center;
}
.ai-search-records td {
  min-height: 26px;
}
.muted {
  color: #4b5563;
}
ul.ai-search-notes {
  margin: 8px 0 0 18px;
  padding: 0;
}
ul.ai-search-notes li {
  margin: 0 0 4px 0;
}
""".strip()


def _escape(value: Any) -> str:
    return html.escape(str(value or "").strip())


def _normalize_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    return text.replace(".", "-").replace("/", "-")


def _format_location(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("paragraph_"):
        raw_number = text.split("_", 1)[1]
        try:
            return f"说明书第{int(raw_number):02d}段"
        except Exception:
            return text
    return text


def _unique_strings(values: Iterable[Any]) -> List[str]:
    items: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            items.append(text)
    return items


def _compress_claim_ids(values: Iterable[Any]) -> str:
    raw_items = _unique_strings(values)
    numeric = sorted({int(item) for item in raw_items if item.isdigit()})
    textual = sorted(item for item in raw_items if not item.isdigit())
    ranges: List[str] = []
    if numeric:
        start = numeric[0]
        end = numeric[0]
        for value in numeric[1:]:
            if value == end + 1:
                end = value
                continue
            ranges.append(str(start) if start == end else f"{start}-{end}")
            start = value
            end = value
        ranges.append(str(start) if start == end else f"{start}-{end}")
    return ",".join(ranges + textual)


def _type_rank(value: str) -> int:
    mapping = {"X": 0, "Y": 1, "A": 2}
    return mapping.get(str(value or "").strip().upper(), 9)


def _sort_key(document: Dict[str, Any]) -> tuple[Any, ...]:
    publication_date = _normalize_date(document.get("publication_date"))
    return (
        _type_rank(str(document.get("document_type") or "").strip().upper()),
        -(int(publication_date.replace("-", "")) if publication_date.replace("-", "").isdigit() else 0),
        str(document.get("pn") or ""),
    )


def _extract_document_role_map(feature_compare_result: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    role_map: Dict[str, Dict[str, Any]] = {}
    for item in feature_compare_result.get("document_roles") or []:
        if not isinstance(item, dict):
            continue
        document_id = str(item.get("document_id") or "").strip()
        if document_id:
            role_map[document_id] = item
    return role_map


def _solo_support_doc_ids(close_read_result: Dict[str, Any]) -> set[str]:
    outputs: set[str] = set()
    for item in close_read_result.get("limitation_coverage") or []:
        if not isinstance(item, dict):
            continue
        supporting_ids = item.get("supporting_document_ids") if isinstance(item.get("supporting_document_ids"), list) else []
        if len(supporting_ids) != 1:
            continue
        status = str(item.get("status") or "").strip().lower()
        if status in {"missing", "gap", "none"}:
            continue
        doc_id = str(supporting_ids[0] or "").strip()
        if doc_id:
            outputs.add(doc_id)
    return outputs


def classify_report_documents(
    documents: List[Dict[str, Any]],
    close_read_result: Optional[Dict[str, Any]] = None,
    feature_compare_result: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    close_read = close_read_result if isinstance(close_read_result, dict) else {}
    feature_compare = feature_compare_result if isinstance(feature_compare_result, dict) else {}
    role_map = _extract_document_role_map(feature_compare)
    solo_support_ids = _solo_support_doc_ids(close_read)
    outputs: List[Dict[str, Any]] = []
    for document in documents:
        item = dict(document)
        existing_type = str(item.get("document_type") or "").strip().upper()
        if existing_type not in {"X", "Y", "A"}:
            role = role_map.get(str(item.get("document_id") or "").strip(), {})
            role_name = str(role.get("document_type_hint") or role.get("role") or "").strip().lower()
            claim_ids = item.get("claim_ids_json") if isinstance(item.get("claim_ids_json"), list) else []
            locations = item.get("evidence_locations_json") if isinstance(item.get("evidence_locations_json"), list) else []
            evidence_score = len(_unique_strings(claim_ids)) + len(_unique_strings(locations))
            if role_name in {"x", "primary", "standalone", "single_reference", "core"}:
                existing_type = "X"
            elif role_name in {"y", "combination", "supporting", "secondary"}:
                existing_type = "Y"
            elif role_name in {"a", "background"}:
                existing_type = "A"
            elif str(item.get("document_id") or "").strip() in solo_support_ids and evidence_score > 0:
                existing_type = "X"
            elif len(documents) == 1 and evidence_score > 0:
                existing_type = "X"
            elif evidence_score > 0:
                existing_type = "Y"
            else:
                existing_type = "A"
        item["document_type"] = existing_type
        outputs.append(item)
    outputs.sort(key=_sort_key)
    for index, item in enumerate(outputs, start=1):
        item["report_row_order"] = index
    return outputs


def _to_html_table(headers: List[str], rows: List[List[str]], *, table_class: str) -> str:
    thead = "".join(f"<th>{_escape(item)}</th>" for item in headers)
    tbody_rows = []
    for row in rows:
        cells = "".join(f"<td>{cell}</td>" for cell in row)
        tbody_rows.append(f"<tr>{cells}</tr>")
    tbody = "".join(tbody_rows)
    return f'<table class="{table_class}"><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>'


def _build_doc_rows(documents: List[Dict[str, Any]]) -> List[List[str]]:
    rows: List[List[str]] = []
    for item in documents:
        rows.append(
            [
                f'<div class="center">{_escape(item.get("document_type") or "")}</div>',
                f'<div class="center">{_escape(item.get("pn") or "")}</div>',
                f'<div class="center">{_escape(_normalize_date(item.get("publication_date")) or "-")}</div>',
                f'<div class="center">{_escape(item.get("primary_ipc") or "-")}</div>',
                _escape(item.get("evidence_summary") or "-"),
                f'<div class="center">{_escape(_compress_claim_ids(item.get("claim_ids_json") or []) or "-")}</div>',
            ]
        )
    return rows


def _build_search_record_lines(documents: List[Dict[str, Any]]) -> List[str]:
    lines: List[str] = []
    for item in documents:
        pn = str(item.get("pn") or "").strip()
        lanes = ",".join(_unique_strings(item.get("source_lanes_json") or []))
        batches = ",".join(_unique_strings(item.get("source_batches_json") or []))
        terms = []
        if lanes:
            terms.append(f"来源={lanes}")
        if batches:
            terms.append(f"批次={batches}")
        summary = "；".join(terms) if terms else "已入选对比文献"
        if pn:
            lines.append(f"{pn}: {summary}")
    return lines or ["当前轮未记录结构化检索轨迹摘要。"]


def build_ai_search_report_markdown(
    *,
    task: Any,
    current_plan: Optional[Dict[str, Any]],
    documents: List[Dict[str, Any]],
    feature_comparison: Optional[Dict[str, Any]],
    close_read_result: Optional[Dict[str, Any]],
    feature_compare_result: Optional[Dict[str, Any]],
    source_patent_data: Optional[Dict[str, Any]] = None,
    termination_reason: str = "",
) -> str:
    patent_data = source_patent_data if isinstance(source_patent_data, dict) else {}
    biblio = patent_data.get("bibliographic_data") if isinstance(patent_data.get("bibliographic_data"), dict) else {}
    applicants = biblio.get("applicants") if isinstance(biblio.get("applicants"), list) else []
    claims = patent_data.get("claims") if isinstance(patent_data.get("claims"), list) else []
    claim_count = len(claims)
    paragraph_count = len(patent_data.get("description_paragraphs")) if isinstance(patent_data.get("description_paragraphs"), list) else 0
    selected_documents = [item for item in documents if str(item.get("stage") or "").strip() == "selected"]
    search_record_lines = _build_search_record_lines(selected_documents)
    plan_scope = current_plan.get("executionSpec", {}).get("search_scope", {}) if isinstance(current_plan, dict) else {}
    objective = str(plan_scope.get("objective") or "").strip()
    notes = []
    readiness_rationale = str((feature_compare_result or {}).get("readiness_rationale") or "").strip()
    if readiness_rationale:
        notes.append(readiness_rationale)
    if termination_reason:
        notes.append(termination_reason)
    if objective:
        notes.append(f"检索目标：{objective}")

    meta_table = """
<table class="ai-search-meta">
  <tbody>
    <tr>
      <td class="label">申请号</td>
      <td>{application_number}</td>
      <td class="label">申请日</td>
      <td>{filing_date}</td>
      <td rowspan="3" class="center">首次检索</td>
    </tr>
    <tr>
      <td class="label">申请人</td>
      <td>{applicant}</td>
      <td class="label">最早的优先权日</td>
      <td>{priority_date}</td>
    </tr>
    <tr>
      <td class="label">权利要求项数</td>
      <td>{claim_count}</td>
      <td class="label">说明书段数</td>
      <td>{paragraph_count}</td>
    </tr>
  </tbody>
</table>
""".format(
        application_number=_escape(biblio.get("application_number") or biblio.get("publication_number") or getattr(task, "pn", None) or ""),
        filing_date=_escape(_normalize_date(biblio.get("filing_date")) or "-"),
        applicant=_escape("；".join(str(item or "").strip() for item in applicants if str(item or "").strip()) or "-"),
        priority_date=_escape(_normalize_date(biblio.get("priority_date")) or "-"),
        claim_count=_escape(claim_count or "-"),
        paragraph_count=_escape(paragraph_count or "-"),
    )
    records_table = _to_html_table(
        ["检索记录信息"],
        [[_escape(search_record_lines[0])], *[[_escape(item)] for item in search_record_lines[1:]]],
        table_class="ai-search-records",
    )
    patent_table = _to_html_table(
        ["类型", "国别以及代码[11]给出的文献号", "代码[43]或[45]给出的日期", "IPC分类号", "相关的段落和/或图号", "涉及的权利要求"],
        _build_doc_rows(selected_documents) or [[
            '<div class="center">-</div>',
            '<div class="center">-</div>',
            '<div class="center">-</div>',
            '<div class="center">-</div>',
            '-',
            '<div class="center">-</div>',
        ]],
        table_class="ai-search-doc-table",
    )
    non_patent_table = _to_html_table(
        ["类型", "书名（包括版本号和卷号）", "出版日期", "作者姓名和出版者名称", "相关页数", "涉及的权利要求"],
        [["", "", "", "", "", ""]],
        table_class="ai-search-empty-table",
    )
    note_block = ""
    if notes:
        note_items = "".join(f"<li>{_escape(item)}</li>" for item in notes if str(item or "").strip())
        note_block = f"\n<ul class=\"ai-search-notes\">{note_items}</ul>\n"
    return "\n".join(
        [
            "# 检索报告",
            "",
            meta_table,
            records_table,
            "",
            "## 相关专利文献",
            "",
            patent_table,
            "",
            "## 相关非专利文献",
            "",
            non_patent_table,
            "",
            "## 表格填写说明事项",
            "",
            "1. 文献类型仅输出 `X / Y / A` 三类。",
            "2. 相关的段落和/或图号优先使用精读阶段定位到的证据位置。",
            "3. 涉及的权利要求来自精读阶段聚合的 claim 对齐结果。",
            note_block,
            "",
        ]
    ).strip()


def write_feature_comparison_csv(feature_comparison: Optional[Dict[str, Any]], output_path: Path) -> Optional[Path]:
    table_rows = feature_comparison.get("table_json") if isinstance(feature_comparison, dict) else []
    if not isinstance(table_rows, list) or not table_rows:
        return None
    headers: List[str] = []
    for row in table_rows:
        if not isinstance(row, dict):
            continue
        for key in row.keys():
            if key not in headers:
                headers.append(str(key))
    if not headers:
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in table_rows:
            if isinstance(row, dict):
                writer.writerow({key: row.get(key, "") for key in headers})
    return output_path


def _download_selected_document_pdf(pn: str, output_path: Path) -> Optional[Path]:
    if not str(pn or "").strip():
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and output_path.stat().st_size > 0:
        return output_path
    try:
        from agents.common.search_clients.factory import SearchClientFactory

        client = SearchClientFactory.get_client("zhihuiya")
        if not hasattr(client, "download_patent_document"):
            return None
        success = bool(client.download_patent_document(str(pn or "").strip().upper(), str(output_path)))
        return output_path if success and output_path.exists() else None
    except Exception:
        return None


def build_ai_search_bundle(
    *,
    output_path: Path,
    report_pdf_path: Path,
    feature_comparison_csv_path: Optional[Path],
    selected_documents: List[Dict[str, Any]],
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    downloaded_files: List[Path] = []
    docs_dir = output_path.parent / "comparison_docs"
    for item in selected_documents:
        pn = str(item.get("pn") or "").strip().upper()
        if not pn:
            continue
        pdf_path = _download_selected_document_pdf(pn, docs_dir / f"{pn}.pdf")
        if pdf_path is not None:
            downloaded_files.append(pdf_path)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(report_pdf_path, arcname=report_pdf_path.name)
        if feature_comparison_csv_path is not None and feature_comparison_csv_path.exists():
            archive.write(feature_comparison_csv_path, arcname=feature_comparison_csv_path.name)
        for item in downloaded_files:
            archive.write(item, arcname=f"comparison_docs/{item.name}")
    return output_path


def build_ai_search_terminal_artifacts(
    *,
    task: Any,
    current_plan: Optional[Dict[str, Any]],
    documents: List[Dict[str, Any]],
    feature_comparison: Optional[Dict[str, Any]],
    close_read_result: Optional[Dict[str, Any]],
    feature_compare_result: Optional[Dict[str, Any]],
    source_patent_data: Optional[Dict[str, Any]] = None,
    termination_reason: str = "",
) -> Dict[str, Any]:
    classified_documents = classify_report_documents(documents, close_read_result, feature_compare_result)
    output_dir = Path(str(getattr(task, "output_dir", "") or "")).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / "ai_search_report.md"
    pdf_path = output_dir / "ai_search_report.pdf"
    feature_comparison_csv_path = output_dir / "feature_comparison.csv"
    bundle_zip_path = output_dir / "ai_search_result_bundle.zip"
    markdown_text = build_ai_search_report_markdown(
        task=task,
        current_plan=current_plan,
        documents=classified_documents,
        feature_comparison=feature_comparison,
        close_read_result=close_read_result,
        feature_compare_result=feature_compare_result,
        source_patent_data=source_patent_data,
        termination_reason=termination_reason,
    )
    write_markdown(markdown_text, markdown_path)
    render_markdown_to_pdf(
        md_text=markdown_text,
        output_path=pdf_path,
        title="AI Search Report",
        css_text=AI_SEARCH_REPORT_CSS,
        enable_mathjax=False,
    )
    feature_csv = write_feature_comparison_csv(feature_comparison, feature_comparison_csv_path)
    build_ai_search_bundle(
        output_path=bundle_zip_path,
        report_pdf_path=pdf_path,
        feature_comparison_csv_path=feature_csv,
        selected_documents=[item for item in classified_documents if str(item.get("stage") or "").strip() == "selected"],
    )
    return {
        "pdf": str(pdf_path),
        "bundle_zip": str(bundle_zip_path),
        "feature_comparison_csv": str(feature_csv) if feature_csv is not None else None,
        "classified_documents": classified_documents,
    }
