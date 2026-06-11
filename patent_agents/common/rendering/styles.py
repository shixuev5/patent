"""
Shared CSS styles for HTML/PDF report rendering.
"""

DEFAULT_REPORT_CSS = """
@page {
    size: A4;
    margin: 2cm 1.5cm;
}

body {
    font-family: "Arial", "SimHei", "STHeiti", "Microsoft YaHei", sans-serif !important;
    font-size: 14px;
    line-height: 1.6;
    color: #333;
    -webkit-print-color-adjust: exact;
    print-color-adjust: exact;
}

/* --- 1. Pagination --- */
.page-break {
    page-break-before: always;
    break-before: page;
}

h1, h2, h3, h4, h5, h6 {
    page-break-after: avoid;
    break-after: avoid;
    page-break-inside: avoid;
    break-inside: avoid;
}

figure, blockquote, pre, .no-break {
    page-break-inside: avoid;
    break-inside: avoid;
}

tr {
    page-break-inside: avoid;
    break-inside: avoid;
}

p {
    orphans: 2;
    widows: 2;
}

/* --- 2. Basic elements --- */
h1 {
    text-align: center;
    color: #2c3e50;
    padding-bottom: 20px;
    border-bottom: 3px solid #3498db;
    margin-bottom: 30px;
}

h2 {
    border-bottom: 2px solid #eee;
    padding-bottom: 8px;
    margin-top: 30px;
    margin-bottom: 15px;
    color: #2c3e50;
    font-size: 18px;
}

h3 {
    margin-top: 25px;
    margin-bottom: 10px;
    border-left: 4px solid #3498db;
    padding-left: 10px;
    color: #34495e;
    font-size: 16px;
}

p {
    margin-bottom: 10px;
    text-align: justify;
}

ul, ol {
    padding-left: 20px;
    margin-bottom: 15px;
}

li {
    margin-bottom: 6px;
}

/* --- 3. Tables --- */
table {
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 20px;
    font-size: 12px;
    table-layout: auto;
    page-break-inside: auto;
    break-inside: auto;
}

thead {
    display: table-header-group;
}

th, td {
    border: 1px solid #dfe2e5;
    padding: 6px 8px;
    text-align: left;
    vertical-align: top;
    word-break: break-word;
    overflow-wrap: break-word;
}

th {
    background-color: #f2f6f9;
    color: #2c3e50;
    font-weight: bold;
    white-space: nowrap;
}

/* --- 4. Images --- */
figure {
    margin: 20px auto;
    text-align: center;
    display: block;
}

img {
    max-width: 95%;
    max-height: 400px;
    object-fit: contain;
    border: 1px solid #e1e4e8;
    border-radius: 4px;
    padding: 4px;
    background-color: #fff;
}

figcaption {
    margin-top: 8px;
    font-size: 12px;
    color: #7f8c8d;
    font-weight: bold;
}

blockquote {
    border-left: 4px solid #3498db;
    background-color: #f8f9fa;
    margin: 15px 0;
    padding: 10px 15px;
    color: #555;
    font-style: italic;
}

/* --- 5. Code blocks --- */
pre {
    background-color: #f0f4f8;
    border: 1px solid #d1d9e6;
    border-radius: 4px;
    padding: 10px;
    margin: 10px 0;
    white-space: pre-wrap;
    word-wrap: break-word;
    font-family: "Consolas", "Monaco", "Courier New", monospace;
    font-size: 12px;
    color: #24292e;
}

code {
    font-family: "Consolas", "Monaco", "Courier New", monospace;
    font-size: 12px;
    color: #2c3e50;
    background-color: #f0f4f8;
    padding: 2px 4px;
    border-radius: 3px;
}
"""
