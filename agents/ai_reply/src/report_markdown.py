"""
最终报告 Markdown 组装（纯函数，无外部依赖副作用）。
"""

from difflib import SequenceMatcher
import html
import re
from typing import Any, Dict, List, Tuple


def build_final_report_markdown(report: Dict[str, Any]) -> str:
    summary = _item_get(report, "summary", {}) or {}
    amendment_section = _item_get(report, "amendment_section", {}) or {}
    response_dispute_section = _item_get(report, "response_dispute_section", {}) or {}
    response_reply_section = _item_get(report, "response_reply_section", {}) or {}
    claim_review_section = _item_get(report, "claim_review_section", {}) or {}

    disputes = _item_get(response_dispute_section, "items", []) or []
    reply_items = _item_get(response_reply_section, "items", []) or []
    claim_reviews = _item_get(claim_review_section, "items", []) or []
    change_items = _item_get(amendment_section, "change_items", []) or []

    total_disputes = _as_int(_item_get(summary, "total_disputes", 0))
    assessed_disputes = _as_int(_item_get(summary, "assessed_disputes", 0))
    unassessed_disputes = _as_int(_item_get(summary, "unassessed_disputes", 0))
    response_reply_points = _as_int(_item_get(summary, "response_reply_points", 0))

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
    avg_confidence = sum(confidence_values) / len(confidence_values) if confidence_values else -1.0

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
            f"待核查 {unassessed_disputes} 项；申请人答复要点 {response_reply_points} 项",
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
            _bool_label(_item_get(amendment_section, "has_claim_amendment", False)),
        )
    )
    lines.append(
        _risk_card(
            "是否存在新增超范围风险",
            _bool_label(_item_get(amendment_section, "added_matter_risk", False)),
        )
    )
    lines.append(
        _risk_card(
            "可提前驳回原因",
            _cell(_item_get(amendment_section, "early_rejection_reason", "") or "无"),
            wide=True,
        )
    )
    lines.append("</div>")
    lines.append("")

    lines.append("## 3. 权利要求变更表")
    lines.append("")
    lines.append(_render_change_items_table(change_items))
    lines.append("")

    lines.append("## 4. 当前生效权利要求逐条评述")
    lines.append("")
    lines.append(_render_claim_review_blocks(claim_reviews))
    lines.append("")

    lines.append("## 5. 争论点总表与AI判断")
    lines.append("")
    lines.append(_render_dispute_overview_table(disputes))
    lines.append("")

    lines.append("## 6. 针对申请人意见陈述的答复")
    lines.append("")
    lines.append(_render_response_reply_blocks(disputes, reply_items))
    lines.append("")

    return "\n".join(lines)


def _item_get(item: Any, key: str, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


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


def _text_or_default(value: Any, default: str = "-") -> str:
    text = str(value or "").strip()
    return text or default


def _html_text(value: Any, default: str = "-") -> str:
    return _escape_text(_text_or_default(value, default)).replace("\n", "<br>")


def _format_claim_ids(value: Any) -> str:
    claim_ids: List[str] = []
    candidates = value if isinstance(value, list) else [value]
    for raw in candidates:
        text = str(raw or "").strip()
        if not text:
            continue
        for piece in re.split(r"[，,\s]+", text):
            part = piece.strip()
            if part and part.isdigit() and part not in claim_ids:
                claim_ids.append(part)
    return ",".join(claim_ids)


def _max_label_value(candidates: List[tuple[str, int]]) -> tuple[str, int]:
    valid = list(candidates) or [("无", 0)]
    valid.sort(key=lambda item: (item[1], item[0]), reverse=True)
    return valid[0]


def _pct_text(value: int, total: int) -> str:
    if total <= 0:
        return "0.0%"
    return f"{(value / total) * 100:.1f}%"


def _confidence_pct_text(value: float) -> str:
    if value < 0:
        return "-"
    return f"{value * 100:.1f}%"


def _conclusion_card(title: str, primary: str, secondary: str) -> str:
    return (
        '<div class="oar-conclusion-card">'
        f'<div class="oar-conclusion-title">{_html_text(title, default="")}</div>'
        f'<div class="oar-conclusion-primary">{_html_text(primary, default="-")}</div>'
        f'<div class="oar-conclusion-secondary">{_html_text(secondary, default="-")}</div>'
        "</div>"
    )


def _risk_card(label: str, value_html: str, wide: bool = False) -> str:
    class_name = "oar-risk-card"
    if wide:
        class_name += " oar-risk-card-wide"
    return (
        f'<div class="{class_name}">'
        f'<div class="oar-risk-label">{_html_text(label, default="")}</div>'
        f'<div class="oar-risk-value">{value_html}</div>'
        "</div>"
    )


def _cell(value: Any) -> str:
    return _html_text(value, default="-")


def _change_source_tag_label(source_type: str) -> str:
    source_type = str(source_type).strip()
    if source_type == "claim":
        return "权项上提"
    if source_type == "spec":
        return "说明书补入"
    return "未知来源"


def _change_claims_html(target_claim_ids: Any, source_type: str, source_claim_ids: List[str]) -> str:
    claim_label = _format_claim_ids(target_claim_ids) or "-"
    source_type = str(source_type).strip()
    if source_type == "claim":
        source_label = _format_claim_ids(source_claim_ids) or "-"
        main_text = f"{claim_label}（来源权利要求 {source_label}）"
    else:
        main_text = claim_label
    tag_label = _change_source_tag_label(source_type)
    tag_class = "oar-change-source-tag"
    if source_type == "claim":
        tag_class += " oar-change-source-tag-claim"
    elif source_type == "spec":
        tag_class += " oar-change-source-tag-spec"
    else:
        tag_class += " oar-change-source-tag-unknown"
    return (
        '<div class="oar-change-claims">'
        f'<div class="oar-change-claims-main">{_html_text(main_text)}</div>'
        f'<div><span class="{tag_class}">{_html_text(tag_label, default="未知来源")}</span></div>'
        "</div>"
    )


def _verdict_label(verdict: str) -> str:
    mapping = {
        "APPLICANT_CORRECT": "申请人正确",
        "EXAMINER_CORRECT": "审查员正确",
        "INCONCLUSIVE": "结论不确定",
    }
    return mapping.get(str(verdict).strip(), "未核查")


def _verdict_badge_html(label: str, verdict: str) -> str:
    verdict_key = str(verdict).strip()
    if verdict_key == "APPLICANT_CORRECT":
        class_name = "oar-verdict-badge oar-verdict-badge-applicant"
    elif verdict_key == "EXAMINER_CORRECT":
        class_name = "oar-verdict-badge oar-verdict-badge-examiner"
    elif verdict_key == "INCONCLUSIVE":
        class_name = "oar-verdict-badge oar-verdict-badge-inconclusive"
    else:
        class_name = "oar-verdict-badge oar-verdict-badge-unassessed"
    return f'<span class="{class_name}">{_html_text(label, default="未核查")}</span>'


def _detail_text_html(label: str, value: Any) -> str:
    return _detail_block_html(label, _html_text(value))


def _detail_block_html(label: str, body_html: str, extra_class: str = "") -> str:
    class_attr = "oar-detail-block"
    if extra_class:
        class_attr += f" {extra_class}"
    return (
        f'<div class="{class_attr}">'
        f'<div class="oar-detail-label">{_escape_text(label)}</div>'
        f'<div class="oar-detail-body">{body_html}</div>'
        "</div>"
    )


def _layered_summary_html(grid_class: str, summary_cells: List[str], detail_blocks: List[str]) -> str:
    parts = [f'<div class="oar-layered-grid {grid_class}">']
    for index, cell in enumerate(summary_cells, start=1):
        cell_class = "oar-grid-summary-cell"
        if index == len(summary_cells):
            cell_class += " oar-grid-summary-cell-verdict"
        parts.append(f'<div class="{cell_class}">{cell}</div>')
    if detail_blocks:
        parts.append('<div class="oar-grid-detail">')
        parts.extend(detail_blocks)
        parts.append("</div>")
    parts.append("</div>")
    return "".join(parts)


def _render_change_items_table(change_items: List[Any]) -> str:
    lines = [
        '<table class="oar-layered-table oar-layered-table-overview">',
        "<colgroup>",
        '<col style="width: 40px;">',
        '<col style="width: 176px;">',
        "<col>",
        '<col style="width: 132px;">',
        "</colgroup>",
        "<thead>",
        "<tr>",
        '<th class="oar-col-index">序号</th>',
        '<th class="oar-col-claims">目标权利要求</th>',
        '<th class="oar-col-feature">变更特征</th>',
        '<th class="oar-col-verdict">AI判断</th>',
        "</tr>",
        "</thead>",
    ]
    if not change_items:
        lines.extend(
            [
                "<tbody>",
                "<tr>",
                '<td class="oar-index-cell">1</td>',
                '<td class="oar-layered-cell" colspan="3">',
                _layered_summary_html(
                    "oar-layered-grid-change",
                    [_html_text("-"), _html_text("无权利要求变更"), ""],
                    [],
                ),
                "</td>",
                "</tr>",
                "</tbody>",
                "</table>",
            ]
        )
        return "\n".join(lines)

    for index, item in enumerate(change_items, start=1):
        feature_text = _text_or_default(_item_get(item, "feature_text", ""), default="-")
        feature_before_text = str(_item_get(item, "feature_before_text", "")).strip()
        feature_after_text = str(_item_get(item, "feature_after_text", "")).strip() or feature_text
        source_claim_ids = _item_get(item, "source_claim_ids", []) or []
        source_type = str(_item_get(item, "source_type", "")).strip()
        claims_html = _change_claims_html(_item_get(item, "target_claim_ids", []), source_type, source_claim_ids)
        feature_html, inferred_contains_added_text = _change_feature_diff_html(
            feature_before_text,
            feature_after_text,
            feature_text,
        )
        contains_added_text_raw = _item_get(item, "contains_added_text", None)
        contains_added_text = (
            inferred_contains_added_text
            if contains_added_text_raw is None
            else bool(contains_added_text_raw)
        )
        assessment = _item_get(item, "assessment", {}) or {}
        verdict = str(_item_get(assessment, "verdict", "")).strip()
        show_ai = contains_added_text and verdict in {"APPLICANT_CORRECT", "EXAMINER_CORRECT", "INCONCLUSIVE"}
        detail_blocks: List[str] = []
        if show_ai:
            ai_reason = _text_or_default(_item_get(assessment, "reasoning", ""), default="-")
            final_review_reason = _text_or_default(_item_get(item, "final_review_reason", ""), default="-")
            evidence_html = _render_ai_basis_html({"evidence": _item_get(item, "evidence", []) or []})
            detail_blocks = [
                _detail_text_html("AI理由：", ai_reason),
                _detail_block_html("AI依据：", evidence_html, extra_class="oar-detail-block-evidence"),
                _detail_text_html("最终审查结论：", final_review_reason),
            ]

        lines.extend(
            [
                '<tbody class="oar-layered-group">',
                "<tr>",
                f'<td class="oar-index-cell">{index}</td>',
                '<td class="oar-layered-cell" colspan="3">',
                _layered_summary_html(
                    "oar-layered-grid-change",
                    [
                        claims_html,
                        feature_html,
                        _verdict_badge_html(_verdict_label(verdict), verdict) if show_ai else "",
                    ],
                    detail_blocks,
                ),
                "</td>",
                "</tr>",
                "</tbody>",
            ]
        )
    lines.append("</table>")
    return "\n".join(lines)


def _change_feature_diff_html(before_text: str, after_text: str, fallback_text: str) -> Tuple[str, bool]:
    before_tokens = _tokenize_change_text(before_text)
    after_tokens = _tokenize_change_text(after_text)
    if not before_tokens and not after_tokens:
        text = _escape_text(_text_or_default(fallback_text, default="-"))
        return f'<div class="oar-change-diff">{text}</div>', False

    parts: List[str] = []
    contains_added_text = False
    matcher = SequenceMatcher(a=before_tokens, b=after_tokens)
    for tag, start_before, end_before, start_after, end_after in matcher.get_opcodes():
        if tag == "equal":
            parts.append(_render_diff_tokens(before_tokens[start_before:end_before]))
        elif tag == "delete":
            parts.append(f'<span class="oar-change-del">{_render_diff_tokens(before_tokens[start_before:end_before])}</span>')
        elif tag == "insert":
            added_chunk = _render_diff_tokens(after_tokens[start_after:end_after])
            if added_chunk:
                contains_added_text = True
                parts.append(f'<span class="oar-change-add">{added_chunk}</span>')
        elif tag == "replace":
            deleted_chunk = _render_diff_tokens(before_tokens[start_before:end_before])
            added_chunk = _render_diff_tokens(after_tokens[start_after:end_after])
            if deleted_chunk:
                parts.append(f'<span class="oar-change-del">{deleted_chunk}</span>')
            if added_chunk:
                contains_added_text = True
                parts.append(f'<span class="oar-change-add">{added_chunk}</span>')

    if not parts:
        text = _escape_text(_text_or_default(after_text or fallback_text, default="-"))
        return f'<div class="oar-change-diff">{text}</div>', False

    return f'<div class="oar-change-diff">{"".join(parts)}</div>', contains_added_text


def _render_diff_tokens(tokens: List[str]) -> str:
    return "".join(_escape_text(token) for token in tokens)


def _tokenize_change_text(text: Any) -> List[str]:
    value = str(text or "")
    if not value:
        return []
    return re.findall(r"\s+|[A-Za-z0-9_]+|[\u4e00-\u9fff]|[^\sA-Za-z0-9_\u4e00-\u9fff]", value)


def _review_mode_label(review_mode: str) -> str:
    mapping = {
        "reused_oa": "复用原评述",
        "response_based": "结合申请人意见答复",
        "amendment_based": "结合权利要求修改评判",
        "mixed": "修改评判 + 申请人意见答复",
    }
    return mapping.get(str(review_mode).strip(), "逐条评述")


def _render_claim_review_blocks(claim_reviews: List[Any]) -> str:
    if not claim_reviews:
        return '<div class="oar-opinion-empty">当前无可展示的权利要求逐条评述。</div>'

    blocks: List[str] = []
    for item in claim_reviews:
        claim_id = str(_item_get(item, "claim_id", "")).strip() or "-"
        claim_text = _text_or_default(_item_get(item, "claim_text", ""), default="未提取到权利要求文本。")
        review_mode = _review_mode_label(str(_item_get(item, "review_mode", "")).strip())
        review_text = _text_or_default(_item_get(item, "review_text", ""), default="当前未提取到可复用的权利要求评述。")
        blocks.extend(
            [
                '<div class="oar-opinion-block">',
                f'<div class="oar-opinion-title">{_html_text(f"权利要求 {claim_id}｜{review_mode}", default="")}</div>',
                _argument_paragraph_html("权利要求文本：", claim_text),
                _argument_paragraph_html("审查评述：", review_text),
                "</div>",
            ]
        )
    return "\n".join(blocks)


def _render_dispute_overview_table(disputes: List[Any]) -> str:
    lines = [
        '<table class="oar-layered-table oar-layered-table-overview">',
        "<colgroup>",
        '<col style="width: 40px;">',
        '<col style="width: 96px;">',
        "<col>",
        '<col style="width: 132px;">',
        '<col style="width: 132px;">',
        "</colgroup>",
        "<thead>",
        "<tr>",
        '<th class="oar-col-index">序号</th>',
        '<th class="oar-col-claims">权利要求</th>',
        '<th class="oar-col-feature">争议特征</th>',
        '<th class="oar-col-type">审查员依据类型</th>',
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
                '<td class="oar-layered-cell" colspan="4">',
                _layered_summary_html(
                    "oar-layered-grid-overview",
                    ["-", "无争议点", "-", _verdict_badge_html("未核查", "UNASSESSED")],
                    [
                        _detail_text_html("审查员理由：", "-"),
                        _detail_text_html("申请人理由：", "-"),
                        _detail_text_html("AI理由：", "-"),
                        _detail_text_html("AI依据：", "-"),
                    ],
                ),
                "</td>",
                "</tr>",
                "</tbody>",
                "</table>",
            ]
        )
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
        evidence_assessment = _item_get(dispute, "evidence_assessment", None)
        assessment = _item_get(evidence_assessment, "assessment", {}) if evidence_assessment else {}
        verdict = str(_item_get(assessment, "verdict", "")).strip()
        if verdict in {"APPLICANT_CORRECT", "EXAMINER_CORRECT", "INCONCLUSIVE"}:
            ai_reason = _text_or_default(_item_get(assessment, "reasoning", ""), default="-")
            ai_basis_html = _render_ai_basis_html(evidence_assessment)
        else:
            ai_reason = "该争议点尚未完成核查。"
            ai_basis_html = _html_text("-")
        lines.extend(
            [
                '<tbody class="oar-layered-group">',
                "<tr>",
                f'<td class="oar-index-cell">{index}</td>',
                '<td class="oar-layered-cell" colspan="4">',
                _layered_summary_html(
                    "oar-layered-grid-overview",
                    [claim_label, feature_text, examiner_type, _verdict_badge_html(_verdict_label(verdict), verdict)],
                    [
                        _detail_text_html("审查员理由：", examiner_reasoning),
                        _detail_text_html("申请人理由：", applicant_reasoning),
                        _detail_text_html("AI理由：", ai_reason),
                        _detail_block_html("AI依据：", ai_basis_html, extra_class="oar-detail-block-evidence"),
                    ],
                ),
                "</td>",
                "</tr>",
                "</tbody>",
            ]
        )
    lines.append("</table>")
    return "\n".join(lines)


def _render_response_reply_blocks(disputes: List[Any], reply_items: List[Any]) -> str:
    if not disputes:
        return '<div class="oar-opinion-empty">当前无可展示的申请人意见陈述。</div>'

    reply_map = {
        str(_item_get(item, "dispute_id", "")).strip(): item
        for item in reply_items or []
        if str(_item_get(item, "dispute_id", "")).strip()
    }

    blocks: List[str] = []
    for index, dispute in enumerate(disputes, start=1):
        dispute_id = str(_item_get(dispute, "dispute_id", "")).strip()
        reply_item = reply_map.get(dispute_id, {})
        claim_label = _format_claim_ids(_item_get(dispute, "claim_ids", [])) or "-"
        feature_text = _text_or_default(_item_get(dispute, "feature_text", ""), default="未提取争议特征")
        applicant_reasoning = _text_or_default(
            _item_get(_item_get(dispute, "applicant_opinion", {}) or {}, "reasoning", ""),
            default="未提取到申请人详细意见陈述。",
        )
        final_reason = _text_or_default(_item_get(reply_item, "final_examiner_rejection_reason", ""), default="")
        title = f"第 {index} 项｜权利要求 {claim_label}｜争议特征：{feature_text}"

        blocks.extend(
            [
                '<div class="oar-opinion-block">',
                f'<div class="oar-opinion-title">{_html_text(title, default="")}</div>',
                _argument_paragraph_html("申请人指出：", applicant_reasoning),
            ]
        )
        if final_reason:
            blocks.append(_argument_paragraph_html("审查员答复：", final_reason))
        blocks.append("</div>")

    return "\n".join(blocks)


def _examiner_type_label(raw_type: str) -> str:
    mapping = {
        "document_based": "文献对比",
        "common_knowledge_based": "公知常识",
        "mixed_basis": "混合依据",
    }
    return mapping.get(str(raw_type).strip(), "未知")


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


def _evidence_line_html(label: str, value: str) -> str:
    return (
        '<div class="oar-evidence-line">'
        f'<span class="oar-evidence-line-label">{_escape_text(label)}</span>'
        f'{_html_text(value, default="-")}'
        "</div>"
    )


def _argument_paragraph_html(label: str, value: Any) -> str:
    return (
        '<div class="oar-opinion-paragraph">'
        f'<span class="oar-opinion-label">{_escape_text(label)}</span>'
        f'{_html_text(value, default="-")}'
        "</div>"
    )
