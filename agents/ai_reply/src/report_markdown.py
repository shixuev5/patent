"""
最终报告 Markdown 组装（纯函数，无外部依赖副作用）。
"""

from difflib import SequenceMatcher
import html
import re
from typing import Any, Dict, List, Tuple

from agents.ai_reply.src.text_normalization import normalize_for_compare, sanitize_for_display


def build_final_report_markdown(report: Dict[str, Any]) -> str:
    summary = _item_get(report, "summary", {}) or {}
    amendment_section = _item_get(report, "amendment_section", {}) or {}
    response_dispute_section = _item_get(report, "response_dispute_section", {}) or {}
    response_reply_section = _item_get(report, "response_reply_section", {}) or {}
    claim_review_section = _item_get(report, "claim_review_section", {}) or {}
    search_followup_section = _item_get(report, "search_followup_section", {}) or {}

    disputes = _item_get(response_dispute_section, "items", []) or []
    reply_items = _item_get(response_reply_section, "items", []) or []
    review_units = _item_get(claim_review_section, "items", []) or []
    substantive_change_groups = _item_get(amendment_section, "substantive_change_groups", []) or []
    structural_adjustments = _item_get(amendment_section, "structural_adjustments", []) or []
    claim_change_groups = _build_claim_change_groups(
        substantive_change_groups=substantive_change_groups,
        structural_adjustments=structural_adjustments,
    )

    total_disputes = _as_int(_item_get(summary, "total_disputes", 0))
    assessed_disputes = _as_int(_item_get(summary, "assessed_disputes", 0))
    unassessed_disputes = _as_int(_item_get(summary, "unassessed_disputes", 0))
    response_reply_points = _as_int(_item_get(summary, "response_reply_points", 0))

    overall_conclusion = str(_item_get(summary, "overall_conclusion", "")).strip()
    rebuttal_distribution = _item_get(summary, "rebuttal_type_distribution", {}) or {}
    verdict_distribution = _item_get(summary, "verdict_distribution", {}) or {}
    added_matter_risk_summary = str(_item_get(amendment_section, "added_matter_risk_summary", "")).strip()
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

    overall_primary, overall_secondary = _build_overall_judgement_card(
        overall_conclusion=overall_conclusion,
        assessed_disputes=assessed_disputes,
        app_correct=app_correct,
        exm_correct=exm_correct,
        inconclusive=inconclusive,
        added_matter_risk_summary=added_matter_risk_summary,
    )
    risk_primary, risk_secondary = _build_key_risk_card(
        unassessed_disputes=unassessed_disputes,
        inconclusive=inconclusive,
        added_matter_risk_summary=added_matter_risk_summary,
    )
    support_primary, support_secondary = _build_support_strength_card(
        assessed_disputes=assessed_disputes,
        app_correct=app_correct,
        avg_confidence=avg_confidence,
        confidence_known_total=confidence_known_total,
    )

    lines: List[str] = []
    lines.append("# AI 答复最终报告")
    lines.append("")

    lines.append("## 1. 核心结论卡片")
    lines.append('<div class="oar-conclusion-grid">')
    lines.append(
        _conclusion_card(
            "整体判断",
            overall_primary,
            overall_secondary,
            emphasis=True,
        )
    )
    lines.append(
        _conclusion_card(
            "重点风险",
            risk_primary,
            risk_secondary,
        )
    )
    lines.append(
        _conclusion_card(
            "核查进度",
            f"{assessed_disputes}/{total_disputes} 项已核查",
            f"待核查 {unassessed_disputes} 项；已形成答复 {response_reply_points} 项",
        )
    )
    lines.append(
        _conclusion_card(
            "支撑强度",
            support_primary,
            support_secondary,
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
            "超范围风险提示",
            _cell(_item_get(amendment_section, "added_matter_risk_summary", "") or "无"),
            wide=True,
        )
    )
    lines.append("</div>")
    lines.append("")

    lines.append("## 3. 权利要求变更表")
    lines.append("")
    lines.append(_render_claim_change_groups_table(claim_change_groups))
    lines.append("")

    lines.append("## 4. 基于上一轮审查意见的重组评述")
    lines.append("")
    lines.append(_render_review_unit_blocks(review_units, _item_get(amendment_section, "substantive_amendments", []) or []))
    lines.append("")

    lines.append("## 5. 争论点总表与AI判断")
    lines.append("")
    lines.append(_render_dispute_overview_table(disputes))
    lines.append("")

    lines.append("## 6. 针对申请人意见陈述的答复")
    lines.append("")
    lines.append(_render_response_reply_blocks(disputes, reply_items))
    lines.append("")

    if _followup_needed(search_followup_section):
        lines.append("## 7. 补检/检索建议")
        lines.append("")
        lines.extend(_render_search_followup_section(search_followup_section))
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


def _followup_needed(section: Any) -> bool:
    return bool(_item_get(section, "needed", False))


def _build_overall_judgement_card(
    overall_conclusion: str,
    assessed_disputes: int,
    app_correct: int,
    exm_correct: int,
    inconclusive: int,
    added_matter_risk_summary: str,
) -> Tuple[str, str]:
    if added_matter_risk_summary:
        primary = "存在修改超范围风险"
        secondary = added_matter_risk_summary
        return primary, secondary

    if assessed_disputes <= 0:
        return "暂无可用核查结论", "当前尚无已完成核查的争议点"

    normalized = overall_conclusion.strip()
    if normalized == "申请人主要争点更占优" or app_correct > exm_correct:
        primary = "本次答复基本成立"
    elif normalized == "审查员主要争点更占优" or exm_correct > app_correct:
        primary = "本次答复支撑不足"
    elif normalized == "现有争点暂无法形成明确结论" or inconclusive == assessed_disputes:
        primary = "本次答复暂无法形成明确结论"
    else:
        primary = "本次答复结论相持"

    secondary = f"已核查 {assessed_disputes} 项中，{app_correct} 项可支持申请人主张"
    return primary, secondary


def _build_key_risk_card(
    unassessed_disputes: int,
    inconclusive: int,
    added_matter_risk_summary: str,
) -> Tuple[str, str]:
    if added_matter_risk_summary:
        return "存在修改超范围风险", added_matter_risk_summary
    if inconclusive > 0:
        primary = f"{inconclusive} 项仍需重点复核"
        secondary = "现有证据尚不足以形成明确结论"
        return primary, secondary
    if unassessed_disputes > 0:
        primary = f"{unassessed_disputes} 项待继续核查"
        secondary = "仍有争议点未完成有效评估"
        return primary, secondary
    return "未见明显剩余风险", "已核查争议点均形成明确判断"


def _build_support_strength_card(
    assessed_disputes: int,
    app_correct: int,
    avg_confidence: float,
    confidence_known_total: int,
) -> Tuple[str, str]:
    if assessed_disputes <= 0:
        return "暂无法判断", "当前缺少可用于衡量支撑强度的核查结果"

    if avg_confidence < 0:
        primary = f"有效回应 {app_correct} 项"
        secondary = f"占已核查争议点 {_pct_text(app_correct, assessed_disputes)}；暂无有效置信度"
        return primary, secondary

    if avg_confidence >= 0.75:
        band = "高支撑"
    elif avg_confidence >= 0.5:
        band = "中等支撑"
    else:
        band = "低支撑"
    primary = f"{band} {app_correct} 项"
    secondary = (
        f"占已核查争议点 {_pct_text(app_correct, assessed_disputes)}；"
        f"平均置信度 {_confidence_pct_text(avg_confidence)}（有效 {confidence_known_total} 项）"
    )
    return primary, secondary


def _conclusion_card(title: str, primary: str, secondary: str, emphasis: bool = False) -> str:
    class_name = "oar-conclusion-card"
    if emphasis:
        class_name += " oar-conclusion-card-emphasis"
    return (
        f'<div class="{class_name}">'
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


def _change_source_tag_label(amendment_kind: str) -> str:
    amendment_kind = str(amendment_kind).strip()
    if amendment_kind == "claim_feature_merge":
        return "从权特征并入"
    if amendment_kind == "spec_feature_addition":
        return "说明书记载补入"
    return "未知修改"


def _change_source_html(amendment_kind: str, source_claim_ids: List[str]) -> str:
    amendment_kind = str(amendment_kind).strip()
    tag_label = _change_source_tag_label(amendment_kind)
    tag_class = "oar-change-source-tag"
    if amendment_kind == "claim_feature_merge":
        tag_class += " oar-change-source-tag-claim"
    elif amendment_kind == "spec_feature_addition":
        tag_class += " oar-change-source-tag-spec"
    else:
        tag_class += " oar-change-source-tag-unknown"
    return (
        '<div class="oar-change-claims oar-change-claims-compact">'
        f'<div><span class="{tag_class}">{_html_text(tag_label, default="未知来源")}</span></div>'
        "</div>"
    )


def _change_item_title_html(amendment_kind: str, source_claim_ids: List[str]) -> str:
    amendment_kind = str(amendment_kind).strip()
    if amendment_kind == "claim_feature_merge":
        source_label = _format_claim_ids(source_claim_ids) or "-"
        title = f"来源权利要求 {source_label}"
    elif amendment_kind == "spec_feature_addition":
        title = "来源说明书"
    else:
        title = "来源待确认"
    return f'<div class="oar-change-item-title">{_html_text(title)}</div>'


def _verdict_label(verdict: str) -> str:
    mapping = {
        "APPLICANT_CORRECT": "支持申请人",
        "EXAMINER_CORRECT": "支持审查员",
        "INCONCLUSIVE": "暂不确定",
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


def _render_search_followup_section(section: Any) -> List[str]:
    lines: List[str] = []
    trigger_reasons = _item_get(section, "trigger_reasons", []) or []
    objective = _text_or_default(_item_get(section, "objective", ""), default="-")
    search_elements = _item_get(section, "search_elements", []) or []
    gap_summaries = _item_get(section, "gap_summaries", []) or []
    constraints = _item_get(section, "suggested_constraints", {}) or {}
    status = str(_item_get(section, "status", "")).strip()
    missing_items = _item_get(section, "missing_items", []) or []

    lines.append("当前报告识别到仍需补强的争点或新增特征，以下内容可用于后续人工补检与检索接续参考。")
    lines.append("")

    if trigger_reasons:
        lines.append("### 7.1 触发原因")
        for item in trigger_reasons:
            lines.append(f"- {_text_or_default(item)}")
        lines.append("")

    lines.append("### 7.2 本轮补检目标")
    lines.append(f"- {objective}")
    if status == "needs_answer" and missing_items:
        lines.append(f"- 当前仍缺少：{'、'.join(str(item).strip() for item in missing_items if str(item).strip())}")
    lines.append("")

    if gap_summaries:
        lines.append("### 7.3 缺口摘要")
        lines.append("| 权利要求 | 关联特征 | 缺口类型 | 缺口说明 |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for item in gap_summaries:
            claim_ids = ",".join(_item_get(item, "claim_ids", []) or []) or "-"
            lines.append(
                f"| {claim_ids} | {_safe_table_text(_item_get(item, 'feature_text', '-') or '-')} | "
                f"{_safe_table_text(_item_get(item, 'gap_type', '-') or '-')} | "
                f"{_safe_table_text(_item_get(item, 'gap_summary', '-') or '-')} |"
            )
        lines.append("")

    if search_elements:
        lines.append("### 7.4 检索要素表")
        lines.append("| 逻辑块 | 检索要素 | 中文扩展 | 英文扩展 | 备注 |")
        lines.append("| :--- | :--- | :--- | :--- | :--- |")
        for item in search_elements:
            block_id = _format_followup_block_id(_item_get(item, "block_id", ""))
            zh_terms = _format_followup_or_terms(_item_get(item, "keywords_zh", []) or [])
            en_terms = _format_followup_or_terms(_item_get(item, "keywords_en", []) or [])
            lines.append(
                f"| {block_id} | {_safe_table_text(_item_get(item, 'element_name', '-') or '-')} | "
                f"{zh_terms} | {en_terms} | "
                f"{_safe_table_text(_item_get(item, 'notes', '-') or '-')} |"
            )
        lines.append("")

    if constraints:
        lines.append("### 7.5 建议边界")
        applicants = constraints.get("applicants") if isinstance(constraints, dict) else []
        comparison_document_ids = constraints.get("comparison_document_ids") if isinstance(constraints, dict) else []
        filing_date = constraints.get("filing_date") if isinstance(constraints, dict) else None
        priority_date = constraints.get("priority_date") if isinstance(constraints, dict) else None
        notes = constraints.get("notes") if isinstance(constraints, dict) else []
        if applicants:
            lines.append(f"- 申请人：{'、'.join(str(item).strip() for item in applicants if str(item).strip())}")
        if filing_date:
            lines.append(f"- 申请日：{filing_date}")
        if priority_date:
            lines.append(f"- 优先权日：{priority_date}")
        if comparison_document_ids:
            lines.append(f"- 当前已存在对比文件编号：{'、'.join(str(item).strip() for item in comparison_document_ids if str(item).strip())}")
        for note in notes if isinstance(notes, list) else []:
            text = str(note or "").strip()
            if text:
                lines.append(f"- {text}")

    return lines


def _safe_table_text(value: Any) -> str:
    return _text_or_default(value).replace("\n", "<br>").replace("|", "\\|")


def _format_followup_or_terms(values: Any) -> str:
    items = values if isinstance(values, list) else [values]
    cleaned = [_safe_table_text(item) for item in items if str(item or "").strip()]
    if not cleaned:
        return "-"
    return " <small style='color:#ccc;'>OR</small> ".join(cleaned)


def _format_followup_block_id(value: Any) -> str:
    block_id = str(value or "").strip().upper()
    if not block_id:
        return "-"
    return f"Block {block_id}"


def _claim_group_label_html(claim_id: Any, claim_type: Any) -> str:
    claim_label = _format_claim_ids(claim_id) or "-"
    claim_type_text = str(claim_type or "").strip()
    if claim_type_text == "independent":
        label = f"{claim_label}（独权）"
    elif claim_type_text == "dependent":
        label = f"{claim_label}（从权）"
    else:
        label = str(claim_label)
    return f'<div class="oar-change-claims-main">{_html_text(label)}</div>'


def _change_item_html(item: Any) -> str:
    item_type = str(_item_get(item, "item_type", "substantive_amendment")).strip()
    if item_type == "structural_adjustment":
        return _structural_adjustment_item_html(item)
    if item_type == "merged_structural_adjustment":
        return _merged_structural_adjustment_item_html(item)

    feature_text = _text_or_default(sanitize_for_display(_item_get(item, "feature_text", "")), default="-")
    feature_before_text = sanitize_for_display(_item_get(item, "feature_before_text", ""))
    feature_after_text = sanitize_for_display(str(_item_get(item, "feature_after_text", "")).strip() or feature_text)
    source_claim_ids = _item_get(item, "source_claim_ids", []) or []
    amendment_kind = str(_item_get(item, "amendment_kind", "")).strip()
    source_title = _change_item_title_html(amendment_kind, source_claim_ids)
    source_tag = _change_source_html(amendment_kind, source_claim_ids)
    feature_html, _ = _change_feature_diff_html(feature_before_text, feature_after_text, feature_text)
    return (
        '<div class="oar-change-item-card">'
        f'<div class="oar-change-item-head">{source_title}{source_tag}</div>'
        '<div class="oar-change-item-body">'
        '<div class="oar-change-item-label">变更内容</div>'
        f'{feature_html}'
        "</div>"
        "</div>"
    )


def _change_ai_badge_html(item: Any) -> str:
    assessment = _item_get(item, "assessment", {}) or {}
    verdict = str(_item_get(assessment, "verdict", "")).strip()
    return _verdict_badge_html(_verdict_label(verdict), verdict)


def _change_ai_detail_html(item: Any) -> str:
    assessment = _item_get(item, "assessment", {}) or {}
    verdict = str(_item_get(assessment, "verdict", "")).strip()
    ai_reason = _text_or_default(_item_get(assessment, "reasoning", ""), default="-")
    final_review_reason = _text_or_default(_item_get(item, "final_review_reason", ""), default="-")
    evidence_html = _render_ai_basis_html({"evidence": _item_get(item, "evidence", []) or []})
    return (
        '<div class="oar-change-ai-panel">'
        f'<div class="oar-change-ai-verdict">{_change_ai_badge_html(item) if verdict else ""}</div>'
        '<div class="oar-change-ai-detail-stack">'
        f'{_detail_block_html("AI理由：", _html_text(ai_reason), extra_class="oar-change-ai-card")}'
        f'{_detail_block_html("AI依据：", evidence_html, extra_class="oar-detail-block-evidence oar-change-ai-card")}'
        f'{_detail_block_html("最终审查结论：", _html_text(final_review_reason), extra_class="oar-change-ai-card")}'
        "</div>"
        "</div>"
    )


def _change_unassessed_html() -> str:
    return ""


def _build_claim_change_groups(
    substantive_change_groups: List[Any],
    structural_adjustments: List[Any],
) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}

    for group in substantive_change_groups or []:
        claim_id = str(_item_get(group, "claim_id", "")).strip()
        if not claim_id:
            continue
        bucket = grouped.setdefault(
            claim_id,
            {
                "claim_id": claim_id,
                "claim_type": str(_item_get(group, "claim_type", "")).strip() or "unknown",
                "items": [],
            },
        )
        if str(bucket.get("claim_type", "")).strip() == "unknown":
            bucket["claim_type"] = str(_item_get(group, "claim_type", "")).strip() or "unknown"
        for item in _item_get(group, "items", []) or []:
            item_dict = dict(_item_get({"item": item}, "item", {}) if isinstance(item, dict) else item)
            if not item_dict:
                item_dict = _to_dict(item)
            item_dict["item_type"] = "substantive_amendment"
            bucket["items"].append(item_dict)

    struct_grouped: Dict[str, List[Any]] = {}
    for item in structural_adjustments or []:
        claim_id = str(_item_get(item, "claim_id", "")).strip()
        if not claim_id:
            continue
        struct_grouped.setdefault(claim_id, []).append(item)

    for claim_id, adjustments in struct_grouped.items():
        bucket = grouped.setdefault(
            claim_id,
            {
                "claim_id": claim_id,
                "claim_type": "unknown",
                "items": [],
            },
        )
        claim_type = "unknown"
        normalized_adjustments: List[Dict[str, Any]] = []
        for adjustment in adjustments:
            item_dict = dict(adjustment) if isinstance(adjustment, dict) else _to_dict(adjustment)
            normalized_adjustments.append(item_dict)
            item_claim_type = str(item_dict.get("claim_type", "")).strip()
            if item_claim_type and item_claim_type != "unknown" and claim_type == "unknown":
                claim_type = item_claim_type
        if str(bucket.get("claim_type", "")).strip() == "unknown" and claim_type != "unknown":
            bucket["claim_type"] = claim_type

        merged_item = {
            "item_type": "merged_structural_adjustment",
            "claim_id": claim_id,
            "old_claim_id": str(_item_get(normalized_adjustments[0], "old_claim_id", "")).strip(),
            "adjustments": normalized_adjustments,
            "has_ai_assessment": False,
        }
        bucket["items"].append(merged_item)

    result: List[Dict[str, Any]] = []
    for claim_id in sorted(grouped.keys(), key=_claim_sort_key):
        bucket = grouped[claim_id]
        bucket["items"] = sorted(bucket["items"], key=_claim_change_item_sort_key)
        result.append(bucket)
    return result


def _claim_change_item_sort_key(item: Dict[str, Any]) -> Tuple[int, str]:
    item_type = str(item.get("item_type", "substantive_amendment")).strip()
    if item_type in ("structural_adjustment", "merged_structural_adjustment"):
        return (3, "")

    amendment_kind = str(item.get("amendment_kind", "")).strip()
    source_rank = 1 if amendment_kind == "claim_feature_merge" else 2
    return (source_rank, str(item.get("amendment_id", "")).strip())


def _render_claim_change_groups_table(claim_change_groups: List[Any]) -> str:
    lines = [
        '<table class="oar-layered-table oar-layered-table-overview oar-claim-change-table">',
        "<colgroup>",
        '<col style="width: 80px;">',
        "<col>",
        "</colgroup>",
        "<thead>",
        "<tr>",
        '<th class="oar-col-claims">权利要求</th>',
        '<th class="oar-col-feature">变更项</th>',
        "</tr>",
        "</thead>",
    ]
    if not claim_change_groups:
        lines.extend(
            [
                "<tbody>",
                "<tr>",
                '<td class="oar-layered-cell">',
                _html_text("-"),
                "</td>",
                '<td class="oar-layered-cell">',
                _html_text("无权利要求变更"),
                "</td>",
                "</tr>",
                "</tbody>",
                "</table>",
            ]
        )
        return "\n".join(lines)

    for group in claim_change_groups:
        claim_id = _item_get(group, "claim_id", "")
        claim_type = _item_get(group, "claim_type", "unknown")
        items = _item_get(group, "items", []) or []
        feature_blocks = [_change_item_html(item) for item in items]
        ai_items = [item for item in items if bool(_item_get(item, "has_ai_assessment", False))]
        ai_detail_blocks = [_change_ai_detail_html(item) for item in ai_items]
        lines.extend(
            [
                '<tbody class="oar-layered-group">',
                "<tr>",
                f'<td class="oar-layered-cell oar-claim-change-cell-claim" rowspan="{2 if ai_items else 1}">',
                _claim_group_label_html(claim_id, claim_type),
                "</td>",
                '<td class="oar-layered-cell oar-claim-change-cell-feature">',
                "".join(feature_blocks),
                _change_unassessed_html() if not ai_items else "",
                "</td>",
                "</tr>",
            ]
        )
        if ai_items:
            lines.extend(
                [
                    "<tr>",
                    '<td class="oar-layered-cell oar-claim-change-cell-detail">',
                    '<div class="oar-grid-detail">',
                    "".join(ai_detail_blocks),
                    "</div>",
                    "</td>",
                    "</tr>",
                ]
            )
        lines.extend(
            [
                "</tbody>",
            ]
        )
    lines.append("</table>")
    return "\n".join(lines)


def _structural_adjustment_label(adjustment_kind: str) -> str:
    adjustment_kind = str(adjustment_kind).strip()
    if adjustment_kind == "renumbering":
        return "编号顺延"
    if adjustment_kind == "reference_adjustment":
        return "引用关系调整"
    return "结构调整"


def _structural_adjustment_reason(reason: str) -> str:
    reason = str(reason).strip()
    if reason == "upstream_merged":
        return "因上游权项并入触发"
    if reason == "upstream_deleted":
        return "因上游权项删除触发"
    return "触发原因待确认"


def _structural_adjustment_sentence(item: Any) -> str:
    claim_id = str(_item_get(item, "claim_id", "")).strip() or "-"
    old_claim_id = str(_item_get(item, "old_claim_id", "")).strip() or "-"
    adjustment_kind = str(_item_get(item, "adjustment_kind", "")).strip()
    reason = str(_item_get(item, "reason", "")).strip()

    if adjustment_kind == "renumbering":
        if reason == "upstream_merged":
            return f"旧权利要求{old_claim_id}因上游权项并入，顺延为现权利要求{claim_id}。"
        if reason == "upstream_deleted":
            return f"旧权利要求{old_claim_id}因上游权项删除，顺延为现权利要求{claim_id}。"
        return f"旧权利要求{old_claim_id}顺延为现权利要求{claim_id}。"

    if adjustment_kind == "reference_adjustment":
        if reason == "upstream_merged":
            return f"现权利要求{claim_id}对应旧权利要求{old_claim_id}，其引用关系已随上游权项并入同步调整。"
        if reason == "upstream_deleted":
            return f"现权利要求{claim_id}对应旧权利要求{old_claim_id}，其引用关系已随上游权项删除同步调整。"
        return f"现权利要求{claim_id}对应旧权利要求{old_claim_id}，其引用关系已同步调整。"

    return f"现权利要求{claim_id}对应旧权利要求{old_claim_id}，结构关系已调整。"


def _structural_adjustment_item_html(item: Any) -> str:
    old_claim_id = str(_item_get(item, "old_claim_id", "")).strip() or "-"
    adjustment_kind = _structural_adjustment_label(_item_get(item, "adjustment_kind", ""))
    summary = f"对应旧权利要求 {old_claim_id}"
    sentence = _structural_adjustment_sentence(item)
    return (
        '<div class="oar-change-item-card oar-structural-adjustment-item">'
        '<div class="oar-change-item-head">'
        f'<div class="oar-change-item-title">{_html_text(summary)}</div>'
        f'<div class="oar-change-claims oar-change-claims-compact"><div><span class="oar-change-source-tag oar-change-source-tag-unknown">{_html_text(adjustment_kind)}</span></div></div>'
        "</div>"
        '<div class="oar-change-item-body">'
        f'<div class="oar-change-item-label">{_html_text(sentence)}</div>'
        "</div>"
        "</div>"
    )


def _merged_structural_adjustment_item_html(merged_item: Dict[str, Any]) -> str:
    claim_id = str(_item_get(merged_item, "claim_id", "")).strip() or "-"
    old_claim_id = str(_item_get(merged_item, "old_claim_id", "")).strip() or "-"
    adjustments = _item_get(merged_item, "adjustments", []) or []

    has_renumbering = False
    has_ref_adj = False
    reason = "upstream_merged"
    old_ref = ""
    new_ref = ""

    for adjustment in adjustments:
        kind = str(_item_get(adjustment, "adjustment_kind", "")).strip()
        item_reason = str(_item_get(adjustment, "reason", "")).strip()
        if item_reason:
            reason = item_reason

        if kind == "renumbering":
            has_renumbering = True
            continue
        if kind == "reference_adjustment":
            has_ref_adj = True
            before_text = str(_item_get(adjustment, "before_text", "")).strip()
            after_text = str(_item_get(adjustment, "after_text", "")).strip()
            match_before = re.search(r"^\s*(?:根据|如)权利要求(.*?)所述", before_text)
            match_after = re.search(r"^\s*(?:根据|如)权利要求(.*?)所述", after_text)
            if match_before and match_after:
                old_ref = match_before.group(1).strip()
                new_ref = match_after.group(1).strip()

    tags: List[str] = []
    if has_renumbering:
        tags.append("编号顺延")
    if has_ref_adj:
        tags.append("引用关系调整")
    if not tags:
        tags.append("结构调整")
    tags_html = "".join(
        f'<div><span class="oar-change-source-tag oar-change-source-tag-unknown">{_html_text(tag)}</span></div>'
        for tag in tags
    )

    if reason == "upstream_merged":
        reason_prefix = "因上游权项并入"
    elif reason == "upstream_deleted":
        reason_prefix = "因上游权项删除"
    else:
        reason_prefix = "因结构调整"

    if has_renumbering and has_ref_adj:
        if old_ref and new_ref and old_ref != new_ref:
            sentence = (
                f"旧权利要求{old_claim_id}{reason_prefix}，顺延为现权利要求{claim_id}，"
                f"且其引用基础由“权利要求{old_ref}”变更为“权利要求{new_ref}”。"
            )
        else:
            sentence = f"旧权利要求{old_claim_id}{reason_prefix}，顺延为现权利要求{claim_id}，其引用关系已同步调整。"
    elif has_renumbering:
        sentence = f"旧权利要求{old_claim_id}{reason_prefix}，顺延为现权利要求{claim_id}。"
    elif has_ref_adj:
        if old_ref and new_ref and old_ref != new_ref:
            sentence = (
                f"现权利要求{claim_id}对应旧权利要求{old_claim_id}，{reason_prefix}，"
                f"其引用基础由“权利要求{old_ref}”变更为“权利要求{new_ref}”。"
            )
        else:
            sentence = f"现权利要求{claim_id}对应旧权利要求{old_claim_id}，{reason_prefix}，其引用关系已同步调整。"
    else:
        sentence = f"现权利要求{claim_id}对应旧权利要求{old_claim_id}，结构关系已调整。"

    summary = f"对应旧权利要求 {old_claim_id}"
    return (
        '<div class="oar-change-item-card oar-structural-adjustment-item">'
        '<div class="oar-change-item-head">'
        f'<div class="oar-change-item-title">{_html_text(summary)}</div>'
        f'<div class="oar-change-claims oar-change-claims-compact">{tags_html}</div>'
        "</div>"
        '<div class="oar-change-item-body">'
        f'<div class="oar-change-item-label">{_html_text(sentence)}</div>'
        "</div>"
        "</div>"
    )


def _change_feature_diff_html(before_text: str, after_text: str, fallback_text: str) -> Tuple[str, bool]:
    if normalize_for_compare(before_text) == normalize_for_compare(after_text):
        text = _escape_text(_text_or_default(after_text or fallback_text, default="-"))
        return f'<div class="oar-change-diff">{text}</div>', False

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


def _to_dict(item: Any) -> Dict[str, Any]:
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return item.model_dump()
    if hasattr(item, "dict"):
        return item.dict()
    return {}


def _claim_sort_key(value: Any) -> Tuple[int, str]:
    text = str(value or "").strip()
    if text.isdigit():
        return (0, f"{int(text):09d}")
    return (1, text)


def _review_unit_type_label(unit_type: str) -> str:
    mapping = {
        "evidence_restructured": "独权重组",
        "supplemented_new": "补充评述",
        "dependent_group_restructured": "从权组重组",
    }
    return mapping.get(str(unit_type).strip(), "重组评述")


def _claim_snapshot_html(claim_snapshots: List[Any]) -> str:
    snapshots: List[tuple[str, str]] = []
    for item in claim_snapshots or []:
        claim_id = str(_item_get(item, "claim_id", "")).strip()
        claim_before_text = sanitize_for_display(_item_get(item, "claim_before_text", ""))
        claim_text = _text_or_default(sanitize_for_display(_item_get(item, "claim_text", "")), default="")
        if not claim_id:
            continue
        claim_diff_html, _ = _change_feature_diff_html(claim_before_text, claim_text, claim_text)
        snapshots.append((claim_id, claim_diff_html))

    items: List[str] = []
    show_snapshot_head = len(snapshots) > 1
    for claim_id, claim_diff_html in snapshots:
        parts = ['<div class="oar-claim-snapshot-item">']
        if show_snapshot_head:
            parts.append(f'<div class="oar-claim-snapshot-head">{_escape_text(f"权利要求{claim_id}")}</div>')
        parts.append(f'<div class="oar-claim-snapshot-body">{claim_diff_html}</div>')
        parts.append("</div>")
        items.extend(parts)

    if not items:
        items.append('<div class="oar-claim-snapshot-empty">未提取到权利要求文本。</div>')

    return (
        '<div class="oar-opinion-paragraph oar-opinion-paragraph-claims">'
        f'<div class="oar-opinion-label">{_escape_text("权利要求：")}</div>'
        f'<div class="oar-claim-snapshot-list">{"".join(items)}</div>'
        "</div>"
    )


def _normalize_claim_id_list(value: Any) -> List[str]:
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
    return claim_ids


def _review_claim_title(display_claim_ids: List[str], fallback_title: str) -> str:
    return "、".join(f"权利要求{claim_id}" for claim_id in display_claim_ids) or fallback_title


def _build_amendment_map(substantive_amendments: List[Any]) -> Dict[str, Dict[str, Any]]:
    amendment_map: Dict[str, Dict[str, Any]] = {}
    for item in substantive_amendments or []:
        amendment = _to_dict(item)
        amendment_id = str(_item_get(amendment, "amendment_id", "")).strip()
        if amendment_id:
            amendment_map[amendment_id] = amendment
    return amendment_map


def _claim_label_text(claim_ids: List[str]) -> str:
    normalized = [claim_id for claim_id in claim_ids if str(claim_id).strip()]
    if not normalized:
        return "相关权利要求"
    if len(normalized) == 1:
        return f"权利要求{normalized[0]}"
    return "权利要求" + "、".join(normalized)


def _review_scope_summary_text(unit_type: str, display_claim_ids: List[str]) -> str:
    claim_label = _claim_label_text(display_claim_ids)
    if unit_type == "supplemented_new":
        return f"重组范围：围绕{claim_label}补成新的正式评述。"
    if unit_type == "dependent_group_restructured":
        return f"重组范围：围绕{claim_label}重组剩余从权评述。"
    return f"重组范围：围绕{claim_label}重组独权评述。"


def _review_handling_summary_text(unit_type: str, review_before_text: str) -> str:
    if review_before_text:
        return "评述处理：沿用上一轮审查意见骨架后完成补强。"
    if unit_type == "supplemented_new":
        return "评述处理：无可复用原评述，本轮补成正式评述。"
    return "评述处理：缺少可复用原评述，本轮按现有素材重组正式评述。"


def _review_summary_html(
    unit_type: str,
    display_claim_ids: List[str],
    review_before_text: str,
    source_summary: Dict[str, Any],
    amendment_map: Dict[str, Dict[str, Any]],
) -> str:
    summary_lines: List[str] = []
    summary_lines.append(_review_scope_summary_text(unit_type, display_claim_ids))

    merged_source_claim_ids = _normalize_claim_id_list(source_summary.get("merged_source_claim_ids", []))
    if merged_source_claim_ids:
        summary_lines.append(f"并入来源：吸收{_claim_label_text(merged_source_claim_ids)}的旧权限定。")

    amendment_feature_texts: List[str] = []
    for amendment_id in source_summary.get("amendment_ids", []) or []:
        amendment = amendment_map.get(str(amendment_id).strip(), {})
        feature_text = sanitize_for_display(_item_get(amendment, "feature_text", ""))
        if feature_text and feature_text not in amendment_feature_texts:
            amendment_feature_texts.append(feature_text)
        if len(amendment_feature_texts) >= 2:
            break
    if amendment_feature_texts:
        summary_lines.append(f"新增限定：{'；'.join(amendment_feature_texts)}。")

    summary_lines.append(_review_handling_summary_text(unit_type, review_before_text))

    seen: List[str] = []
    for line in summary_lines:
        text = str(line or "").strip()
        if text and text not in seen:
            seen.append(text)
        if len(seen) >= 4:
            break

    if not seen:
        seen.append("评述处理：本轮按现有素材整理正式评述。")

    items = "".join(
        (
            '<div class="oar-review-summary-item">'
            f'<span class="oar-review-summary-bullet">{_escape_text(str(index))}.</span>'
            f'<span class="oar-review-summary-text">{_html_text(text, default="-")}</span>'
            "</div>"
        )
        for index, text in enumerate(seen, start=1)
    )
    return f'<div class="oar-review-summary-list">{items}</div>'


def _render_review_unit_blocks(review_units: List[Any], substantive_amendments: List[Any]) -> str:
    if not review_units:
        return '<div class="oar-opinion-empty">当前无可展示的重组评述。</div>'

    amendment_map = _build_amendment_map(substantive_amendments)
    blocks: List[str] = []
    for item in review_units:
        visible_claim_ids = _normalize_claim_id_list(_item_get(item, "display_claim_ids", []))
        title = _review_claim_title(visible_claim_ids, _text_or_default(_item_get(item, "title", ""), default="重组评述"))
        unit_type = _review_unit_type_label(str(_item_get(item, "unit_type", "")).strip())
        claim_snapshots = [
            snapshot
            for snapshot in (_item_get(item, "claim_snapshots", []) or [])
            if str(_item_get(snapshot, "claim_id", "")).strip() in set(visible_claim_ids)
        ]
        claim_text_html = _claim_snapshot_html(claim_snapshots)
        review_before_text = sanitize_for_display(_item_get(item, "review_before_text", ""))
        review_text = sanitize_for_display(_item_get(item, "review_text", ""))
        review_body_html = _html_text(
            review_text,
            default="当前未提取到可复用的审查评述。",
        )
        review_summary_block_html = _review_summary_html(
            unit_type=str(_item_get(item, "unit_type", "")).strip(),
            display_claim_ids=visible_claim_ids,
            review_before_text=review_before_text,
            source_summary=_to_dict(_item_get(item, "source_summary", {}) or {}),
            amendment_map=amendment_map,
        )
        blocks.extend(
            [
                '<div class="oar-opinion-block">',
                f'<div class="oar-opinion-title">{_html_text(f"{title}｜{unit_type}", default="")}</div>',
                claim_text_html,
                _argument_paragraph_html_with_body(
                    "正式评述：",
                    review_body_html,
                    paragraph_class="oar-opinion-paragraph-formal",
                    body_class="oar-opinion-body-formal",
                ),
                _argument_paragraph_html_with_body(
                    "修改摘要：",
                    review_summary_block_html,
                    paragraph_class="oar-opinion-paragraph-summary",
                    body_class="oar-opinion-body-summary",
                ),
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
        quote_translation = _text_or_default(_item_get(item, "quote_translation", ""), default="")
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
        if quote and quote_translation:
            basis_parts.append(_evidence_line_html("译文：", quote_translation, extra_class="oar-evidence-line-translation"))
        if analysis:
            basis_parts.append(_evidence_line_html("分析：", analysis))
        if not quote and not quote_translation and not analysis:
            basis_parts.append('<div class="oar-evidence-line">-</div>')
        basis_parts.append("</div>")

    basis_parts.append("</div>")
    return "".join(basis_parts)


def _evidence_line_html(label: str, value: str, extra_class: str = "") -> str:
    class_attr = " ".join(part for part in ["oar-evidence-line", str(extra_class).strip()] if part)
    return (
        f'<div class="{class_attr}">'
        f'<span class="oar-evidence-line-label">{_escape_text(label)}</span>'
        f'{_html_text(value, default="-")}'
        "</div>"
    )


def _argument_paragraph_html(label: str, value: Any) -> str:
    return _argument_paragraph_html_with_body(label, _html_text(value, default="-"))


def _argument_paragraph_html_with_body(
    label: str,
    body_html: str,
    paragraph_class: str = "",
    body_class: str = "",
) -> str:
    paragraph_classes = " ".join(
        part for part in ["oar-opinion-paragraph", str(paragraph_class).strip()] if part
    )
    body_classes = " ".join(
        part for part in ["oar-opinion-body", str(body_class).strip()] if part
    )
    return (
        f'<div class="{paragraph_classes}">'
        f'<div class="oar-opinion-label">{_escape_text(label)}</div>'
        f'<div class="{body_classes}">{body_html}</div>'
        "</div>"
    )
