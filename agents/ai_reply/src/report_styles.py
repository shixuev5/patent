"""
Office action reply final report styles.
"""

from agents.common.rendering.styles import DEFAULT_REPORT_CSS

OAR_REPORT_CSS = DEFAULT_REPORT_CSS + """

:root {
    --oar-ink: #0f172a;
    --oar-ink-soft: #334155;
    --oar-muted: #475569;
    --oar-subtle: #64748b;
    --oar-accent: #0e7490;
    --oar-accent-strong: #155e75;
    --oar-border: #d6dee8;
    --oar-border-soft: #e7edf4;
    --oar-surface: #ffffff;
    --oar-surface-soft: #f8fafc;
    --oar-surface-tint: #f0f9ff;
    --oar-surface-tint-strong: #ecfeff;
    --oar-success-bg: #ecfdf3;
    --oar-success-border: #16a34a;
    --oar-success-text: #166534;
    --oar-danger-bg: #fef2f2;
    --oar-danger-border: #dc2626;
    --oar-danger-text: #991b1b;
    --oar-warn-bg: #fff7ed;
    --oar-warn-border: #ea580c;
    --oar-warn-text: #9a3412;
    --oar-neutral-bg: #f1f5f9;
    --oar-neutral-border: #64748b;
    --oar-neutral-text: #334155;
}

body {
    background: var(--oar-surface);
    font-size: 15px;
    line-height: 1.62;
    color: var(--oar-ink);
}

h1 {
    font-size: 28px;
    margin-bottom: 22px;
    letter-spacing: 0.4px;
}

h2 {
    font-size: 19px;
    margin-top: 22px;
}

h3 {
    font-size: 17px;
}

table {
    font-size: 13px;
}

th, td {
    padding: 8px 10px;
    line-height: 1.5;
}

.oar-risk-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px;
    margin: 8px 0 16px 0;
}

.oar-risk-card {
    border: 1px solid var(--oar-border);
    background: var(--oar-surface);
    border-radius: 10px;
    padding: 12px;
}

.oar-risk-card-wide {
    grid-column: 1 / -1;
}

.oar-risk-label {
    font-size: 12px;
    color: var(--oar-subtle);
    margin-bottom: 6px;
}

.oar-risk-value {
    font-size: 15px;
    font-weight: 700;
    color: var(--oar-ink);
}

.oar-conclusion-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px;
    margin: 8px 0 16px 0;
}

.oar-conclusion-card {
    border: 1px solid var(--oar-border);
    border-left: 5px solid var(--oar-accent);
    border-radius: 10px;
    background: linear-gradient(180deg, var(--oar-surface) 0%, var(--oar-surface-tint) 100%);
    padding: 10px 12px;
}

.oar-conclusion-title {
    font-size: 12px;
    color: var(--oar-subtle);
    margin-bottom: 5px;
}

.oar-conclusion-primary {
    font-size: 16px;
    font-weight: 700;
    color: var(--oar-ink);
    margin-bottom: 3px;
}

.oar-conclusion-secondary {
    font-size: 12px;
    color: var(--oar-muted);
}

.oar-layered-table {
    table-layout: fixed;
}

.oar-layered-table thead {
    border-bottom: 1px solid var(--oar-border);
}

.oar-layered-table thead tr {
    border-bottom: 1px solid var(--oar-border);
}

.oar-layered-table thead th {
    border-bottom: 1px solid var(--oar-border);
}

.oar-col-index {
    width: 40px;
}

.oar-col-claims {
    width: 96px;
}

.oar-col-type,
.oar-col-verdict {
    width: 132px;
}

.oar-layered-group {
    page-break-inside: avoid;
    break-inside: avoid;
}

.oar-layered-cell {
    padding: 0;
    background: var(--oar-surface);
    vertical-align: top;
}

.oar-claim-change-cell-claim {
    width: 80px;
    padding: 8px 6px;
    background: #ffffff;
    border-right: 1px solid var(--oar-border-soft);
    vertical-align: top;
}

.oar-claim-change-cell-feature,
.oar-claim-change-cell-detail {
    padding: 0;
}

.oar-claim-change-cell-detail {
    background: #f8fafc;
}

.oar-index-cell {
    text-align: center;
    font-weight: 700;
    background: var(--oar-surface-soft);
    vertical-align: top;
    width: 40px;
    padding-left: 0;
    padding-right: 0;
    padding-top: 8px;
}

.oar-layered-grid {
    display: grid;
    width: 100%;
    page-break-inside: avoid;
    break-inside: avoid;
}

.oar-layered-grid-overview {
    grid-template-columns: 96px minmax(0, 1fr) 132px 132px;
}

.oar-grid-summary-cell {
    padding: 8px 10px;
    background: var(--oar-surface);
    border-right: 1px solid var(--oar-border-soft);
    line-height: 1.5;
}

.oar-grid-summary-cell:last-of-type {
    border-right: none;
}

.oar-grid-summary-cell-verdict {
    display: flex;
    align-items: center;
    justify-content: center;
    background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
    padding: 6px 8px;
    overflow: hidden;
    box-sizing: border-box;
}

.oar-verdict-badge {
    display: block;
    width: 100%;
    min-width: 0;
    max-width: 100%;
    text-align: center;
    padding: 6px 10px;
    border-radius: 8px;
    border: 1.5px solid transparent;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 0.1px;
    line-height: 1.25;
    box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65);
    box-sizing: border-box;
    white-space: normal;
    word-break: break-word;
}

.oar-verdict-badge-applicant {
    background: var(--oar-success-bg);
    border-color: var(--oar-success-border);
    color: var(--oar-success-text);
}

.oar-verdict-badge-examiner {
    background: var(--oar-danger-bg);
    border-color: var(--oar-danger-border);
    color: var(--oar-danger-text);
}

.oar-verdict-badge-inconclusive {
    background: var(--oar-warn-bg);
    border-color: var(--oar-warn-border);
    color: var(--oar-warn-text);
}

.oar-verdict-badge-unassessed {
    background: var(--oar-neutral-bg);
    border-color: var(--oar-neutral-border);
    color: var(--oar-neutral-text);
}

.oar-grid-detail {
    grid-column: 1 / -1;
    background: linear-gradient(180deg, #f8fafc 0%, #f3f7fb 100%);
    border-top: 1px solid var(--oar-border-soft);
    padding: 10px;
    page-break-before: avoid;
    break-before: avoid;
}

.oar-detail-block + .oar-detail-block {
    margin-top: 10px;
}

.oar-detail-label {
    font-size: 12px;
    font-weight: 700;
    color: var(--oar-accent);
    margin-bottom: 4px;
}

.oar-detail-body {
    color: var(--oar-ink);
    line-height: 1.75;
}

.oar-detail-block:not(.oar-detail-block-evidence) .oar-detail-body {
    text-indent: 2em;
}

.oar-detail-block-no-indent .oar-detail-body {
    text-indent: 0;
}

.oar-change-item-card {
    border: 1px solid var(--oar-border-soft);
    border-radius: 10px;
    background: linear-gradient(180deg, #ffffff 0%, #fbfdff 100%);
    padding: 10px 12px;
    margin: 6px 8px;
}

.oar-change-item-card + .oar-change-item-card {
    margin-top: 6px;
}

.oar-change-item-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 8px;
    padding-bottom: 8px;
    border-bottom: 1px dashed var(--oar-border-soft);
}

.oar-change-item-title {
    font-size: 12px;
    font-weight: 800;
    color: var(--oar-ink-soft);
    letter-spacing: 0.1px;
    padding-top: 2px;
}

.oar-change-item-body {
    display: block;
}

.oar-change-item-label {
    font-size: 12px;
    font-weight: 700;
    color: var(--oar-accent);
    margin-bottom: 4px;
}

.oar-change-claims {
    display: flex;
    flex-direction: column;
    gap: 6px;
    flex-shrink: 0;
}

.oar-change-claims-compact {
    align-items: flex-end;
    gap: 0;
}

.oar-change-claims-main {
    font-size: 12px;
    font-weight: 700;
    color: var(--oar-ink);
    word-break: break-word;
    text-align: left;
}

.oar-change-source-tag {
    display: inline-flex;
    align-items: center;
    border-radius: 999px;
    padding: 2px 8px;
    font-size: 11px;
    font-weight: 700;
    line-height: 1.3;
    border: 1px solid var(--oar-border);
    color: var(--oar-ink-soft);
    background: var(--oar-surface-soft);
}

.oar-change-source-tag-claim {
    color: var(--oar-accent-strong);
    background: var(--oar-surface-tint);
    border-color: #8fd3e2;
}

.oar-change-source-tag-spec {
    color: var(--oar-warn-text);
    background: var(--oar-warn-bg);
    border-color: #fdba74;
}

.oar-change-source-tag-unknown {
    color: var(--oar-muted);
}

.oar-change-diff {
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.55;
    color: #334155;
}

.oar-change-add {
    color: var(--oar-danger-border);
    font-weight: 700;
}

.oar-change-del {
    color: #94a3b8;
    text-decoration: line-through;
    text-decoration-color: #cbd5e1;
}

.oar-evidence-list {
    display: block;
}

.oar-evidence-item + .oar-evidence-item {
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px dashed var(--oar-border);
}

.oar-evidence-item {
    padding-left: 1.25em;
}

.oar-evidence-head {
    font-size: 12px;
    font-weight: 700;
    color: var(--oar-ink-soft);
    margin-bottom: 4px;
}

.oar-evidence-line {
    line-height: 1.72;
}

.oar-evidence-line + .oar-evidence-line {
    margin-top: 4px;
}

.oar-evidence-line-label {
    font-weight: 700;
    color: var(--oar-ink);
}

.oar-change-ai-panel {
    display: flex;
    flex-direction: column;
    gap: 4px;
}

.oar-change-ai-verdict {
    display: flex;
    justify-content: flex-start;
}

.oar-change-ai-detail-stack {
    display: flex;
    flex-direction: column;
    gap: 4px;
}

.oar-change-ai-card {
    border: 1px solid var(--oar-border-soft);
    border-radius: 10px;
    background: #ffffff;
    padding: 7px 9px;
}

.oar-change-ai-card .oar-detail-label {
    margin-bottom: 6px;
}

.oar-change-ai-card .oar-detail-body {
    line-height: 1.66;
    text-indent: 0;
}

.oar-change-ai-card.oar-detail-block-evidence .oar-detail-body {
    text-indent: 0;
}

.oar-change-unassessed {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
    padding: 4px 8px 8px 8px;
}

.oar-change-unassessed .oar-verdict-badge {
    max-width: 108px;
    margin: 0;
    padding: 4px 8px;
}

.oar-change-unassessed-note {
    font-size: 11px;
    line-height: 1.35;
    color: var(--oar-subtle);
    text-align: left;
}

.oar-opinion-block {
    border: 1px solid var(--oar-border);
    border-left: 5px solid var(--oar-accent);
    border-radius: 10px;
    background: #ffffff;
    padding: 14px 16px;
    margin-bottom: 14px;
    page-break-inside: avoid;
    break-inside: avoid;
}

.oar-opinion-title {
    font-size: 14px;
    font-weight: 700;
    color: var(--oar-ink);
    line-height: 1.55;
    margin-bottom: 10px;
}

.oar-opinion-paragraph {
    color: var(--oar-ink);
}

.oar-opinion-paragraph + .oar-opinion-paragraph {
    margin-top: 12px;
}

.oar-opinion-paragraph-claims {
    text-indent: 0;
}

.oar-opinion-label {
    display: block;
    font-weight: 700;
    color: var(--oar-accent-strong);
}

.oar-opinion-body {
    margin-top: 6px;
    line-height: 1.85;
    color: var(--oar-ink);
    text-indent: 2em;
}

.oar-opinion-body-formal {
    text-indent: 0;
    line-height: 1.82;
}

.oar-opinion-body-summary {
    text-indent: 0;
    line-height: 1.65;
}

.oar-claim-snapshot-list {
    margin-top: 10px;
}

.oar-claim-snapshot-item + .oar-claim-snapshot-item {
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px dashed rgba(148, 163, 184, 0.38);
}

.oar-claim-snapshot-head {
    font-weight: 700;
    color: var(--oar-ink);
    margin-bottom: 4px;
}

.oar-claim-snapshot-body,
.oar-claim-snapshot-empty {
    line-height: 1.82;
    color: var(--oar-ink);
    text-indent: 2em;
}

.oar-opinion-empty {
    border: 1px dashed var(--oar-border);
    border-radius: 10px;
    background: var(--oar-surface-soft);
    color: var(--oar-muted);
    padding: 14px 16px;
}

.oar-review-summary-list {
    display: grid;
    gap: 8px;
}

.oar-review-summary-item {
    display: grid;
    grid-template-columns: 18px minmax(0, 1fr);
    gap: 6px;
    align-items: start;
    padding: 8px 10px;
    border: 1px solid var(--oar-border-soft);
    border-radius: 8px;
    background: var(--oar-surface-soft);
}

.oar-review-summary-bullet {
    font-size: 12px;
    font-weight: 800;
    color: var(--oar-accent-strong);
    line-height: 1.55;
}

.oar-review-summary-text {
    color: var(--oar-ink);
    line-height: 1.65;
    word-break: break-word;
}

.oar-structural-adjustment-list {
    display: grid;
    gap: 12px;
}

.oar-structural-adjustment-item {
    border: 1px solid var(--oar-border);
    border-radius: 10px;
    background: #ffffff;
    padding: 14px 16px;
    page-break-inside: avoid;
    break-inside: avoid;
}

.oar-structural-adjustment-head {
    font-size: 14px;
    font-weight: 700;
    color: var(--oar-ink);
}

.oar-structural-adjustment-tag {
    margin-top: 6px;
    display: inline-block;
    font-size: 12px;
    font-weight: 700;
    color: var(--oar-accent-strong);
}

.oar-structural-adjustment-reason {
    margin-top: 6px;
    margin-bottom: 8px;
    font-size: 12px;
    color: var(--oar-muted);
}

.oar-ai-badge-stack-item + .oar-ai-badge-stack-item {
    margin-top: 8px;
}

.oar-risk-grid,
.oar-conclusion-grid,
.oar-conclusion-card,
.oar-risk-card,
.oar-opinion-block {
    page-break-inside: avoid;
    break-inside: avoid;
}
"""
