"""
最终报告 Markdown 组装（纯函数，无外部依赖副作用）。
"""

import html
import re
from typing import Any, Dict, List


def build_final_report_markdown(report: Dict[str, Any]) -> str:
    summary = _item_get(report, "summary", {}) or {}
    amendment_review = _item_get(report, "amendment_review", {}) or {}
    disputes = _item_get(report, "disputes", []) or []
    second_notice = _item_get(report, "second_office_action_notice", {}) or {}

    total_disputes = _as_int(_item_get(summary, "total_disputes", 0))
    assessed_disputes = _as_int(_item_get(summary, "assessed_disputes", 0))
    unassessed_disputes = _as_int(_item_get(summary, "unassessed_disputes", 0))
    second_points = _as_int(_item_get(summary, "second_office_action_points", 0))

    rebuttal_distribution = _item_get(summary, "rebuttal_type_distribution", {}) or {}
    verdict_distribution = _item_get(summary, "verdict_distribution", {}) or {}
    app_correct = _as_int(_item_get(verdict_distribution, "applicant_correct", 0))
    exm_correct = _as_int(_item_get(verdict_distribution, "examiner_correct", 0))
    inconclusive = _as_int(_item_get(verdict_distribution, "inconclusive", 0))
    fact_dispute = _as_int(_item_get(rebuttal_distribution, "fact_dispute", 0))
    logic_dispute = _as_int(_item_get(rebuttal_distribution, "logic_dispute", 0))
    unknown_dispute = _as_int(_item_get(rebuttal_distribution, "unknown", 0))

    confidence_high = 0
    confidence_mid = 0
    confidence_low = 0
    confidence_unknown = 0
    confidence_values: List[float] = []
    for dispute in disputes:
        evidence_assessment = _item_get(dispute, "evidence_assessment", None)
        assessment = _item_get(evidence_assessment, "assessment", {}) if evidence_assessment else {}
        verdict = str(_item_get(assessment, "verdict", "")).strip()
        if verdict not in {"APPLICANT_CORRECT", "EXAMINER_CORRECT", "INCONCLUSIVE"}:
            continue

        confidence = _as_float(_item_get(assessment, "confidence", None), default=-1.0)
        if confidence < 0:
            confidence_unknown += 1
            continue
        confidence = max(0.0, min(1.0, confidence))
        confidence_values.append(confidence)
        if confidence >= 0.75:
            confidence_high += 1
        elif confidence >= 0.5:
            confidence_mid += 1
        else:
            confidence_low += 1

    confidence_known_total = confidence_high + confidence_mid + confidence_low
    avg_confidence = (
        sum(confidence_values) / max(len(confidence_values), 1)
        if confidence_values
        else -1.0
    )

    dominant_verdict_name, dominant_verdict_value = _max_label_value(
        [
            ("申请人正确", app_correct),
            ("审查员正确", exm_correct),
            ("结论不确定", inconclusive),
        ]
    )
    dominant_type_name, dominant_type_value = _max_label_value(
        [
            ("事实争议", fact_dispute),
            ("逻辑争议", logic_dispute),
            ("未分类争议", unknown_dispute),
        ]
    )
    dominant_conf_name, dominant_conf_value = _max_label_value(
        [
            ("高置信（>=0.75）", confidence_high),
            ("中置信（0.50-0.74）", confidence_mid),
            ("低置信（<0.50）", confidence_low),
            ("未给出置信度", confidence_unknown),
        ]
    )

    lines: List[str] = []
    lines.append("# AI 答复最终报告")
    lines.append("")

    lines.append("## 1. 核心结论卡片")
    lines.append('<div class="oar-conclusion-grid">')
    lines.append(
        _conclusion_card(
            "核查进度",
            f"{assessed_disputes}/{total_disputes} 项已核查",
            f"待核查 {unassessed_disputes} 项；二通可复用要点 {second_points} 项",
        )
    )
    lines.append(
        _conclusion_card(
            "主导裁决",
            f"{dominant_verdict_name}（{dominant_verdict_value} 项）",
            f"占已核查争议点 {_pct_text(dominant_verdict_value, assessed_disputes)}",
        )
    )
    lines.append(
        _conclusion_card(
            "主导争议类型",
            f"{dominant_type_name}（{dominant_type_value} 项）",
            f"占总争议点 {_pct_text(dominant_type_value, total_disputes)}",
        )
    )
    lines.append(
        _conclusion_card(
            "主导置信分层",
            f"{dominant_conf_name}（{dominant_conf_value} 项）",
            f"均值置信度 {_confidence_pct_text(avg_confidence)}（有效 {confidence_known_total} 项）",
        )
    )
    lines.append("</div>")
    lines.append("")

    lines.append("## 2. 修改与风险概览")
    lines.append("")
    lines.append('<div class="oar-risk-grid">')
    lines.append(
        _risk_card(
            "是否存在权利要求修改",
            _bool_label(_item_get(amendment_review, "has_claim_amendment", False)),
        )
    )
    lines.append(
        _risk_card(
            "是否存在新增超范围风险",
            _bool_label(_item_get(amendment_review, "added_matter_risk", False)),
        )
    )
    lines.append(
        _risk_card(
            "可提前驳回原因",
            _cell(_item_get(amendment_review, "early_rejection_reason", "") or "无"),
            wide=True,
        )
    )
    lines.append("</div>")
    lines.append("")

    second_notice_items = _item_get(second_notice, "items", []) or []

    lines.append("## 3. 争论点数据总表")
    lines.append("")
    lines.append(_render_dispute_data_table(disputes))
    lines.append("")

    lines.append("## 4. 争论点 AI 判断总表")
    lines.append("")
    lines.append(_render_ai_assessment_table(disputes))
    lines.append("")

    lines.append("## 5. 二次审查意见通知书要点")
    lines.append("")
    lines.append(_render_second_notice_argument_blocks(disputes, second_notice_items))
    lines.append("")

    return "\n".join(lines)


def _item_get(item: Any, key: str, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _examiner_type_label(raw_type: str) -> str:
    mapping = {
        "document_based": "文献对比",
        "common_knowledge_based": "公知常识",
        "mixed_basis": "混合依据",
    }
    return mapping.get(str(raw_type).strip(), "未知")


def _verdict_label(verdict: str) -> str:
    mapping = {
        "APPLICANT_CORRECT": "申请人正确",
        "EXAMINER_CORRECT": "审查员正确",
        "INCONCLUSIVE": "结论不确定",
    }
    return mapping.get(str(verdict).strip(), "未核查")


def _format_claim_ids(value: Any) -> str:
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
    return ",".join(claim_ids)


def _render_dispute_data_table(disputes: List[Any]) -> str:
    lines = [
        '<table class="oar-layered-table oar-layered-table-data">',
        "<colgroup>",
        '<col style="width: 40px;">',
        '<col style="width: 96px;">',
        "<col>",
        '<col style="width: 132px;">',
        "</colgroup>",
        "<thead>",
        "<tr>",
        '<th class="oar-col-index">序号</th>',
        '<th class="oar-col-claims">权利要求</th>',
        '<th class="oar-col-feature">争议特征</th>',
        '<th class="oar-col-type">审查员依据类型</th>',
        "</tr>",
        "</thead>",
    ]
    if not disputes:
        lines.extend(
            [
                "<tbody>",
                "<tr>",
                '<td class="oar-index-cell">1</td>',
                '<td class="oar-layered-cell" colspan="3">',
                _layered_summary_html(
                    "oar-layered-grid-data",
                    ["-", "无争议点", "-"],
                    [
                        _detail_text_html("审查员理由：", "-"),
                        _detail_text_html("申请人理由：", "-"),
                    ],
                ),
                "</td>",
                "</tr>",
                "</tbody>",
            ]
        )
        lines.append("</table>")
        return "\n".join(lines)

    for index, dispute in enumerate(disputes, start=1):
        claim_label = _format_claim_ids(_item_get(dispute, "claim_ids", [])) or "-"
        feature_text = _text_or_default(_item_get(dispute, "feature_text", ""), default="-")
        examiner_type = _examiner_type_label(
            _item_get(_item_get(dispute, "examiner_opinion", {}) or {}, "type", "")
        )
        examiner_reasoning = _text_or_default(
            _item_get(_item_get(dispute, "examiner_opinion", {}) or {}, "reasoning", ""),
            default="-",
        )
        applicant_reasoning = _text_or_default(
            _item_get(_item_get(dispute, "applicant_opinion", {}) or {}, "reasoning", ""),
            default="-",
        )
        lines.extend(
            [
                '<tbody class="oar-layered-group">',
                "<tr>",
                f'<td class="oar-index-cell">{index}</td>',
                '<td class="oar-layered-cell" colspan="3">',
                _layered_summary_html(
                    "oar-layered-grid-data",
                    [claim_label, feature_text, examiner_type],
                    [
                        _detail_text_html("审查员理由：", examiner_reasoning),
                        _detail_text_html("申请人理由：", applicant_reasoning),
                    ],
                ),
                "</td>",
                "</tr>",
                "</tbody>",
            ]
        )
    lines.append("</table>")
    return "\n".join(lines)


def _render_ai_assessment_table(disputes: List[Any]) -> str:
    lines = [
        '<table class="oar-layered-table oar-layered-table-ai">',
        "<colgroup>",
        '<col style="width: 40px;">',
        '<col style="width: 96px;">',
        "<col>",
        '<col style="width: 132px;">',
        "</colgroup>",
        "<thead>",
        "<tr>",
        '<th class="oar-col-index">序号</th>',
        '<th class="oar-col-claims">权利要求</th>',
        '<th class="oar-col-feature">争议特征</th>',
        '<th class="oar-col-verdict">AI判断</th>',
        "</tr>",
        "</thead>",
    ]
    if not disputes:
        lines.extend(
            [
                "<tbody>",
                "<tr>",
                '<td class="oar-index-cell">1</td>',
                '<td class="oar-layered-cell" colspan="3">',
                _layered_summary_html(
                    "oar-layered-grid-ai",
                    ["-", "无争议点", "-"],
                    [
                        _detail_text_html("AI理由：", "-"),
                        _detail_text_html("AI依据：", "-"),
                    ],
                ),
                "</td>",
                "</tr>",
                "</tbody>",
            ]
        )
        lines.append("</table>")
        return "\n".join(lines)

    for index, dispute in enumerate(disputes, start=1):
        claim_label = _format_claim_ids(_item_get(dispute, "claim_ids", [])) or "-"
        feature_text = _text_or_default(_item_get(dispute, "feature_text", ""), default="-")
        evidence_assessment = _item_get(dispute, "evidence_assessment", None)
        assessment = _item_get(evidence_assessment, "assessment", {}) if evidence_assessment else {}
        verdict = str(_item_get(assessment, "verdict", "")).strip()

        if verdict in {"APPLICANT_CORRECT", "EXAMINER_CORRECT", "INCONCLUSIVE"}:
            ai_verdict = _verdict_label(verdict)
            ai_reason = _text_or_default(_item_get(assessment, "reasoning", ""), default="-")
            ai_basis_html = _render_ai_basis_html(evidence_assessment)
        else:
            ai_verdict = "未核查"
            ai_reason = "该争议点尚未完成核查。"
            ai_basis_html = _html_text("-")

        lines.extend(
            [
                '<tbody class="oar-layered-group">',
                "<tr>",
                f'<td class="oar-index-cell">{index}</td>',
                '<td class="oar-layered-cell" colspan="3">',
                _layered_summary_html(
                    "oar-layered-grid-ai",
                    [claim_label, feature_text, ai_verdict],
                    [
                        _detail_text_html("AI理由：", ai_reason),
                        _detail_block_html("AI依据：", ai_basis_html),
                    ],
                ),
                "</td>",
                "</tr>",
                "</tbody>",
            ]
        )
    lines.append("</table>")
    return "\n".join(lines)


def _render_second_notice_argument_blocks(disputes: List[Any], second_notice_items: List[Any]) -> str:
    if not disputes:
        return '<div class="oar-opinion-empty">当前无可展示的申请人意见陈述。</div>'

    rejection_reason_by_dispute: Dict[str, str] = {}
    for item in second_notice_items or []:
        dispute_id = str(_item_get(item, "dispute_id", "")).strip()
        rejection_reason = str(_item_get(item, "examiner_rejection_reason", "")).strip()
        if dispute_id and rejection_reason:
            rejection_reason_by_dispute[dispute_id] = rejection_reason

    blocks: List[str] = []
    for index, dispute in enumerate(disputes, start=1):
        dispute_id = str(_item_get(dispute, "dispute_id", "")).strip()
        claim_label = _format_claim_ids(_item_get(dispute, "claim_ids", [])) or "-"
        feature_text = _text_or_default(_item_get(dispute, "feature_text", ""), default="未提取争议特征")
        applicant_reasoning = _text_or_default(
            _item_get(_item_get(dispute, "applicant_opinion", {}) or {}, "reasoning", ""),
            default="未提取到申请人详细意见陈述。",
        )
        rejection_reason = rejection_reason_by_dispute.get(dispute_id, "")
        title = f"第 {index} 项｜权利要求 {claim_label}｜争议特征：{feature_text}"

        blocks.extend(
            [
                '<div class="oar-opinion-block">',
                f'<div class="oar-opinion-title">{_html_text(title, default="")}</div>',
                _argument_paragraph_html("申请人指出：", applicant_reasoning),
            ]
        )
        if rejection_reason:
            blocks.append(_argument_paragraph_html("审查员认为：", rejection_reason))
        blocks.append("</div>")

    return "\n".join(blocks)


def _render_ai_basis_html(evidence_assessment: Any) -> str:
    evidence_list = _item_get(evidence_assessment, "evidence", []) or []
    if not evidence_list:
        return _html_text("-")

    basis_parts: List[str] = ['<div class="oar-evidence-list">']
    for index, item in enumerate(evidence_list, start=1):
        source_title = _text_or_default(_item_get(item, "source_title", ""), default="") or _text_or_default(
            _item_get(item, "doc_id", ""),
            default="",
        )
        location = _text_or_default(_item_get(item, "location", ""), default="")
        quote = _text_or_default(_item_get(item, "quote", ""), default="")
        analysis = _text_or_default(_item_get(item, "analysis", ""), default="")
        header_parts = [f"证据{index}"]
        if source_title:
            header_parts.append(source_title)
        if location:
            header_parts.append(location)

        basis_parts.append('<div class="oar-evidence-item">')
        basis_parts.append(
            f'<div class="oar-evidence-head">{"｜".join(_escape_text(part) for part in header_parts)}</div>'
        )
        if quote:
            basis_parts.append(_evidence_line_html("引文：", quote))
        if analysis:
            basis_parts.append(_evidence_line_html("分析：", analysis))
        if not quote and not analysis:
            basis_parts.append('<div class="oar-evidence-line">-</div>')
        basis_parts.append("</div>")

    basis_parts.append("</div>")
    return "".join(basis_parts)


def _bool_label(value: Any) -> str:
    return "是" if bool(value) else "否"


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _escape_text(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def _html_text(value: Any, default: str = "-") -> str:
    text = _text_or_default(value, default=default)
    return _escape_text(text).replace("\n", "<br>")


def _text_or_default(value: Any, default: str = "-") -> str:
    text = str(value or "").strip()
    return text or default


def _detail_text_html(label: str, value: Any) -> str:
    return _detail_block_html(label, _html_text(value))


def _detail_block_html(label: str, body_html: str) -> str:
    return (
        '<div class="oar-detail-block">'
        f'<div class="oar-detail-label">{_escape_text(label)}</div>'
        f'<div class="oar-detail-body">{body_html}</div>'
        "</div>"
    )


def _layered_summary_html(grid_class: str, summary_cells: List[str], detail_blocks: List[str]) -> str:
    cell_html = "".join(
        f'<div class="oar-grid-summary-cell">{_html_text(value)}</div>'
        for value in summary_cells
    )
    details_html = "".join(detail_blocks)
    return (
        f'<div class="oar-layered-grid {grid_class}">'
        f"{cell_html}"
        f'<div class="oar-grid-detail">{details_html}</div>'
        "</div>"
    )


def _argument_paragraph_html(label: str, value: Any) -> str:
    return (
        '<div class="oar-opinion-paragraph">'
        f'<span class="oar-opinion-label">{_escape_text(label)}</span>'
        f'<span class="oar-opinion-text">{_html_text(value)}</span>'
        "</div>"
    )


def _evidence_line_html(label: str, value: Any) -> str:
    return (
        '<div class="oar-evidence-line">'
        f'<span class="oar-evidence-line-label">{_escape_text(label)}</span>'
        f'<span class="oar-evidence-line-text">{_html_text(value)}</span>'
        "</div>"
    )


def _cell(value: Any) -> str:
    text = str(value or "-")
    text = text.replace("\n", "<br>").replace("|", "\\|")
    return text


def _pct_text(value: int, total: int) -> str:
    safe_total = max(int(total), 1)
    safe_value = min(max(int(value), 0), safe_total)
    pct = (safe_value / safe_total) * 100.0
    return f"{pct:.1f}%"


def _confidence_pct_text(value: float) -> str:
    if value < 0:
        return "-"
    bounded = max(0.0, min(1.0, value))
    return f"{bounded * 100:.1f}%"


def _max_label_value(items: List[tuple[str, int]]) -> tuple[str, int]:
    if not items:
        return "-", 0
    return max(items, key=lambda pair: pair[1])


def _risk_card(label: str, value: str, wide: bool = False) -> str:
    classes = "oar-risk-card oar-risk-card-wide" if wide else "oar-risk-card"
    return (
        f'<div class="{classes}">'
        f'<div class="oar-risk-label">{_cell(label)}</div>'
        f'<div class="oar-risk-value">{_cell(value)}</div>'
        "</div>"
    )


def _conclusion_card(title: str, primary: str, secondary: str) -> str:
    return (
        '<div class="oar-conclusion-card">'
        f'<div class="oar-conclusion-title">{_cell(title)}</div>'
        f'<div class="oar-conclusion-primary">{_cell(primary)}</div>'
        f'<div class="oar-conclusion-secondary">{_cell(secondary)}</div>'
        "</div>"
    )
