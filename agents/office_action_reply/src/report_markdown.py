"""
最终报告 Markdown 组装（纯函数，无外部依赖副作用）。
"""

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
    lines.append("# 审查意见答复最终报告")
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

    lines.append("## 3. 争论点数据总表")
    lines.append("")
    lines.append("| 序号 | 权利要求 | 争议特征 | 审查员依据类型 | 审查员理由 | 申请人理由 |")
    lines.append("| ---: | :---: | :--- | :--- | :--- | :--- |")
    if not disputes:
        lines.append("| 1 | - | 无争议点 | - | - | - |")
    else:
        for index, dispute in enumerate(disputes, start=1):
            claim_id = _cell(_item_get(dispute, "original_claim_id", "") or "-")
            feature_text = _cell(_truncate(_item_get(dispute, "feature_text", ""), 56))
            examiner_type = _cell(
                _examiner_type_label(
                    _item_get(_item_get(dispute, "examiner_opinion", {}) or {}, "type", "")
                )
            )
            examiner_reasoning = _cell(
                _truncate(
                    _item_get(_item_get(dispute, "examiner_opinion", {}) or {}, "reasoning", ""),
                    92,
                )
            )
            applicant_reasoning = _cell(
                _truncate(
                    _item_get(_item_get(dispute, "applicant_opinion", {}) or {}, "reasoning", ""),
                    92,
                )
            )
            lines.append(
                f"| {index} | {claim_id} | {feature_text} | {examiner_type} | {examiner_reasoning} | {applicant_reasoning} |"
            )
    lines.append("")

    lines.append("## 4. 争论点 AI 判断总表")
    lines.append("")
    lines.append("| 序号 | 权利要求 | 争议特征 | AI判断 | AI理由 | AI依据 |")
    lines.append("| ---: | :---: | :--- | :--- | :--- | :--- |")
    if not disputes:
        lines.append("| 1 | - | 无争议点 | - | - | - |")
    else:
        for index, dispute in enumerate(disputes, start=1):
            claim_id = _cell(_item_get(dispute, "original_claim_id", "") or "-")
            feature_text = _cell(_truncate(_item_get(dispute, "feature_text", ""), 56))
            evidence_assessment = _item_get(dispute, "evidence_assessment", None)
            assessment = _item_get(evidence_assessment, "assessment", {}) if evidence_assessment else {}
            verdict = str(_item_get(assessment, "verdict", "")).strip()

            if verdict in {"APPLICANT_CORRECT", "EXAMINER_CORRECT", "INCONCLUSIVE"}:
                ai_verdict = _verdict_label(verdict)
                ai_reason = _cell(_truncate(_item_get(assessment, "reasoning", ""), 100))
                ai_basis = _cell(_truncate(_build_ai_basis_text(evidence_assessment), 120))
            else:
                ai_verdict = "未核查"
                ai_reason = "该争议点尚未完成核查。"
                ai_basis = "-"

            lines.append(
                f"| {index} | {claim_id} | {feature_text} | {_cell(ai_verdict)} | {ai_reason} | {ai_basis} |"
            )
    lines.append("")

    lines.append("## 5. 二次审查意见通知书要点")
    lines.append("")
    second_notice_text = str(_item_get(second_notice, "text", "")).strip()
    second_notice_items = _item_get(second_notice, "items", []) or []
    lines.append(f"> {_cell(second_notice_text) if second_notice_text else '当前无可复用二通审查要点。'}")
    lines.append("")

    lines.append("| 序号 | 权利要求 | 争议特征 | AI驳回理由 |")
    lines.append("| ---: | :---: | :--- | :--- |")
    if not second_notice_items:
        lines.append("| 1 | - | - | 无 |")
    else:
        for index, item in enumerate(second_notice_items, start=1):
            lines.append(
                f"| {index} | {_cell(_item_get(item, 'original_claim_id', '') or '-')} | "
                f"{_cell(_truncate(_item_get(item, 'feature_text', '') or '-', 48))} | "
                f"{_cell(_truncate(_item_get(item, 'examiner_rejection_reason', '') or '-', 120))} |"
            )
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


def _build_ai_basis_text(evidence_assessment: Any) -> str:
    evidence_list = _item_get(evidence_assessment, "evidence", []) or []
    if not evidence_list:
        return "-"

    basis_parts: List[str] = []
    for item in evidence_list[:2]:
        source_title = str(_item_get(item, "source_title", "")).strip() or str(_item_get(item, "doc_id", "")).strip()
        location = str(_item_get(item, "location", "")).strip()
        quote = str(_item_get(item, "quote", "")).strip()
        analysis = str(_item_get(item, "analysis", "")).strip()

        content = _truncate(quote or analysis or "-", 44)
        head = " ".join([seg for seg in [source_title, location] if seg]).strip()
        if head:
            basis_parts.append(f"{head}：{content}")
        else:
            basis_parts.append(content)

    return "；".join(basis_parts) if basis_parts else "-"


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


def _truncate(value: Any, max_len: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


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
