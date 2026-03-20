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

.oar-kpi-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 10px;
    margin: 8px 0 16px 0;
}

.oar-kpi-card {
    border: 1px solid var(--oar-border);
    background: linear-gradient(180deg, var(--oar-surface) 0%, var(--oar-surface-tint) 100%);
    border-radius: 10px;
    padding: 12px 12px 10px 12px;
}

.oar-kpi-label {
    font-size: 12px;
    color: var(--oar-muted);
    margin-bottom: 6px;
}

.oar-kpi-value {
    font-size: 24px;
    font-weight: 700;
    color: var(--oar-accent-strong);
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

.oar-chart-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
    margin: 6px 0 10px 0;
}

.oar-chart-card {
    border: 1px solid var(--oar-border);
    border-radius: 10px;
    padding: 9px 9px 7px 9px;
    background: var(--oar-surface);
    overflow: hidden;
}

.oar-chart-card-wide {
    grid-column: 1 / -1;
}

.oar-chart-title {
    text-align: center;
    font-size: 13px;
    font-weight: 700;
    color: var(--oar-ink-soft);
    margin-bottom: 3px;
}

.oar-chart {
    width: 100%;
    height: 300px;
    overflow: hidden;
}

.oar-chart-note {
    text-align: center;
    font-size: 12px;
    color: var(--oar-subtle);
    margin-top: -4px;
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

.oar-layered-grid-data,
.oar-layered-grid-ai {
    grid-template-columns: 96px minmax(0, 1fr) 132px;
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

.oar-grid-detail {
    grid-column: 1 / -1;
    background: var(--oar-surface-soft);
    border-top: 1px solid var(--oar-border-soft);
    padding: 12px 10px;
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

.oar-evidence-list {
    display: block;
}

.oar-evidence-item + .oar-evidence-item {
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px dashed var(--oar-border);
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

.oar-opinion-block {
    border: 1px solid var(--oar-border);
    border-left: 5px solid var(--oar-accent);
    border-radius: 10px;
    background: linear-gradient(180deg, var(--oar-surface) 0%, var(--oar-surface-tint-strong) 100%);
    padding: 14px 16px;
    margin-bottom: 14px;
    page-break-inside: avoid;
    break-inside: avoid;
}

.oar-opinion-title {
    font-size: 14px;
    font-weight: 700;
    color: var(--oar-ink);
    line-height: 1.7;
    margin-bottom: 10px;
}

.oar-opinion-paragraph {
    line-height: 1.85;
    color: var(--oar-ink);
}

.oar-opinion-paragraph + .oar-opinion-paragraph {
    margin-top: 12px;
}

.oar-opinion-label {
    font-weight: 700;
    color: var(--oar-accent-strong);
}

.oar-opinion-empty {
    border: 1px dashed var(--oar-border);
    border-radius: 10px;
    background: var(--oar-surface-soft);
    color: var(--oar-muted);
    padding: 14px 16px;
}

.oar-kpi-grid,
.oar-risk-grid,
.oar-chart-grid,
.oar-conclusion-grid,
.oar-chart-card,
.oar-conclusion-card,
.oar-kpi-card,
.oar-risk-card,
.oar-opinion-block {
    page-break-inside: avoid;
    break-inside: avoid;
}
"""
