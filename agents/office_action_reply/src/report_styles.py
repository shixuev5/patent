"""
Office action reply final report styles.
"""

from agents.common.rendering.styles import DEFAULT_REPORT_CSS

OAR_REPORT_CSS = DEFAULT_REPORT_CSS + """

body {
    background: #ffffff;
    font-size: 15px;
    line-height: 1.62;
    color: #0f172a;
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
    border: 1px solid #dbeafe;
    background: linear-gradient(180deg, #f8fbff 0%, #f1f7ff 100%);
    border-radius: 10px;
    padding: 12px 12px 10px 12px;
}

.oar-kpi-label {
    font-size: 12px;
    color: #475569;
    margin-bottom: 6px;
}

.oar-kpi-value {
    font-size: 24px;
    font-weight: 700;
    color: #0b3b62;
}

.oar-risk-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px;
    margin: 8px 0 16px 0;
}

.oar-risk-card {
    border: 1px solid #dce7f5;
    background: #ffffff;
    border-radius: 10px;
    padding: 12px;
}

.oar-risk-card-wide {
    grid-column: 1 / -1;
}

.oar-risk-label {
    font-size: 12px;
    color: #64748b;
    margin-bottom: 6px;
}

.oar-risk-value {
    font-size: 15px;
    font-weight: 700;
    color: #111827;
}

.oar-chart-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
    margin: 6px 0 10px 0;
}

.oar-chart-card {
    border: 1px solid #dce7f5;
    border-radius: 10px;
    padding: 9px 9px 7px 9px;
    background: #ffffff;
    overflow: hidden;
}

.oar-chart-card-wide {
    grid-column: 1 / -1;
}

.oar-chart-title {
    text-align: center;
    font-size: 13px;
    font-weight: 700;
    color: #334155;
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
    color: #64748b;
    margin-top: -4px;
}

.oar-conclusion-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10px;
    margin: 8px 0 16px 0;
}

.oar-conclusion-card {
    border: 1px solid #d7e8f8;
    border-left: 5px solid #0284c7;
    border-radius: 10px;
    background: linear-gradient(180deg, #f9fcff 0%, #f3f9ff 100%);
    padding: 10px 12px;
}

.oar-conclusion-title {
    font-size: 12px;
    color: #64748b;
    margin-bottom: 5px;
}

.oar-conclusion-primary {
    font-size: 16px;
    font-weight: 700;
    color: #0f172a;
    margin-bottom: 3px;
}

.oar-conclusion-secondary {
    font-size: 12px;
    color: #475569;
}

.oar-kpi-grid,
.oar-risk-grid,
.oar-chart-grid,
.oar-conclusion-grid,
.oar-chart-card,
.oar-conclusion-card,
.oar-kpi-card,
.oar-risk-card {
    page-break-inside: avoid;
    break-inside: avoid;
}
"""
