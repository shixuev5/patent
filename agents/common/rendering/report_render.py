"""
Shared markdown/html/pdf rendering utilities.
"""

from pathlib import Path
import json
from typing import Optional, Dict, Any, List, Sequence, Union
from uuid import uuid4

import markdown
from loguru import logger
from playwright.sync_api import sync_playwright

from agents.common.rendering.styles import DEFAULT_REPORT_CSS
from agents.common.rendering.models import EChartSpec

_ASSET_BASE_URL = "https://cdn.jsdelivr.net/npm"
_MATHJAX_ASSET_PATH = "mathjax@3.2.2/es5/tex-mml-chtml.js"
_ECHARTS_ASSET_PATH = "echarts@5/dist/echarts.min.js"


_MATHJAX_CONFIG_SCRIPT = """
<script>
window.MathJax = {
  tex: {
    inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
    displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']]
  },
  svg: {
    fontCache: 'global'
  },
  startup: {
    pageReady: () => {
      return MathJax.startup.defaultPageReady();
    }
  }
};
</script>
"""


def _build_asset_url(asset_path: str) -> str:
    value = str(asset_path or "").strip()
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return f"{_ASSET_BASE_URL.rstrip('/')}/{value.lstrip('/')}"


def _build_head_scripts(enable_mathjax: bool, enable_echarts: bool) -> str:
    parts = []
    if enable_mathjax:
        parts.append(_MATHJAX_CONFIG_SCRIPT)
        parts.append(
            f'<script id="MathJax-script" async src="{_build_asset_url(_MATHJAX_ASSET_PATH)}"></script>'
        )
    if enable_echarts:
        parts.append(
            f'<script src="{_build_asset_url(_ECHARTS_ASSET_PATH)}"></script>'
        )
    return "\n".join(parts)


def build_wait_for_flag_function(flag_name: str) -> str:
    """Build a Playwright wait_for_function expression for a window flag."""
    return f"() => window[{json.dumps(str(flag_name))}] === true"


def build_echarts_post_render_script(
    charts: Sequence[Union[EChartSpec, Dict[str, Any]]],
    data_element_id: str = "oar-chart-data",
    done_flag: str = "__echartsRendered",
) -> str:
    """
    Build a generic post-render script that renders multiple ECharts charts.

    Each chart spec supports:
    - element_id: DOM id of chart container
    - data_key: key in JSON data payload
    - title: chart title
    - chart_type: "donut" | "bar"
    """
    normalized_charts: List[Dict[str, str]] = []
    for item in charts or []:
        if isinstance(item, EChartSpec):
            normalized_charts.append(
                {
                    "element_id": str(item.element_id),
                    "data_key": str(item.data_key),
                    "title": str(item.title),
                    "chart_type": str(item.chart_type or "donut"),
                }
            )
            continue

        if isinstance(item, dict):
            normalized_charts.append(
                {
                    "element_id": str(item.get("element_id", "")),
                    "data_key": str(item.get("data_key", "")),
                    "title": str(item.get("title", "")),
                    "chart_type": str(item.get("chart_type", "donut")),
                }
            )

    payload = {
        "charts": normalized_charts,
        "data_element_id": str(data_element_id),
        "done_flag": str(done_flag),
    }
    payload_json = json.dumps(payload, ensure_ascii=False)

    return f"""
() => {{
    try {{
        const cfg = {payload_json};
        const done = () => {{
            window[cfg.done_flag] = true;
        }};

        if (!window.echarts) {{
            done();
            return;
        }}

        const dataNode = document.getElementById(cfg.data_element_id);
        if (!dataNode) {{
            done();
            return;
        }}

        let parsed = {{}};
        try {{
            parsed = JSON.parse(dataNode.textContent || "{{}}");
        }} catch (_) {{
            parsed = {{}};
        }}

        const colorPalette = [
            "#0ea5e9",
            "#22c55e",
            "#f59e0b",
            "#ef4444",
            "#6366f1",
            "#14b8a6",
        ];

        const normalizeSeries = (data) => {{
            if (!Array.isArray(data)) {{
                return [];
            }}
            return data.map((item) => ({{
                name: String((item && item.name) || "-"),
                value: Number((item && item.value) || 0),
            }}));
        }};

        const hasPositiveValue = (data) => (data || []).some((item) => Number(item.value) > 0);

        const buildDonutOption = (data) => ({{
            animation: false,
            tooltip: {{
                trigger: "item",
                formatter: (params) => `${{params.name}}: ${{params.value}} 项`,
            }},
            legend: {{
                bottom: 0,
                left: "center",
                textStyle: {{ fontSize: 11 }},
                formatter: (name) => {{
                    const row = (data || []).find((item) => item.name === name);
                    return `${{name}}: ${{row ? row.value : 0}}项`;
                }},
            }},
            color: colorPalette,
            series: [{{
                type: "pie",
                center: ["50%", "44%"],
                radius: ["42%", "64%"],
                avoidLabelOverlap: true,
                label: {{ show: false }},
                labelLine: {{ show: false }},
                data
            }}]
        }});

        const buildBarOption = (data) => {{
            const categories = data.map((item) => item.name);
            const values = data.map((item) => item.value);
            return {{
                animation: false,
                tooltip: {{
                    trigger: "axis",
                    axisPointer: {{ type: "shadow" }},
                    formatter: (params) => {{
                        const row = Array.isArray(params) && params[0] ? params[0] : null;
                        if (!row) {{
                            return "";
                        }}
                        return `${{row.name}}: ${{row.value}} 项`;
                    }},
                }},
                grid: {{
                    left: "4%",
                    right: "14%",
                    top: 22,
                    bottom: 24,
                    containLabel: true,
                }},
                xAxis: {{
                    type: "value",
                    minInterval: 1,
                    axisLabel: {{
                        formatter: (value) => `${{value}}`,
                    }},
                    splitLine: {{
                        lineStyle: {{
                            color: "#e5e7eb",
                            type: "dashed",
                        }},
                    }},
                }},
                yAxis: {{
                    type: "category",
                    data: categories,
                    axisTick: {{ show: false }},
                }},
                series: [{{
                    type: "bar",
                    data: values,
                    barMaxWidth: 26,
                    label: {{
                        show: true,
                        position: "insideRight",
                        distance: 2,
                        color: "#ffffff",
                        formatter: "{{c}}项",
                    }},
                    itemStyle: {{
                        borderRadius: [0, 4, 4, 0],
                        color: (params) => colorPalette[params.dataIndex % colorPalette.length],
                    }},
                }}],
            }};
        }};

        (cfg.charts || []).forEach((item) => {{
            const elementId = String(item.element_id || "").trim();
            const dataKey = String(item.data_key || "").trim();
            if (!elementId || !dataKey) {{
                return;
            }}
            const el = document.getElementById(elementId);
            if (!el) {{
                return;
            }}
            // Fallback for missing chart height styles in custom templates/tests.
            if (!el.style.height && Number(el.clientHeight || 0) < 16) {{
                el.style.height = "280px";
            }}
            const seriesData = normalizeSeries(parsed[dataKey]);
            if (!hasPositiveValue(seriesData)) {{
                el.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#94a3b8;font-size:13px;">暂无可视化数据</div>';
                return;
            }}
            const chart = window.echarts.init(el, null, {{ renderer: "svg" }});
            const chartType = String(item.chart_type || "donut").toLowerCase();
            if (chartType === "bar") {{
                chart.setOption(buildBarOption(seriesData));
                chart.resize();
                return;
            }}
            chart.setOption(buildDonutOption(seriesData));
            chart.resize();
        }});

        done();
    }} catch (_) {{
        window[{json.dumps(str(done_flag))}] = true;
    }}
}}
"""


def markdown_to_html_document(
    md_text: str,
    title: str = "Report",
    css_text: Optional[str] = None,
    enable_mathjax: bool = True,
    enable_echarts: bool = False,
    extra_head_html: Optional[str] = None,
) -> str:
    """Convert markdown text to a full HTML document string."""
    html_body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "nl2br", "sane_lists", "extra"],
    )

    final_css = css_text if css_text is not None else DEFAULT_REPORT_CSS
    builtin_head_scripts = _build_head_scripts(
        enable_mathjax=enable_mathjax,
        enable_echarts=enable_echarts,
    )
    extra_head_block = extra_head_html or ""

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>{title}</title>
    {builtin_head_scripts}
    {extra_head_block}
    <style>
        {final_css}
    </style>
</head>
<body>
    {html_body}
</body>
</html>
"""


def write_markdown(md_text: str, output_path: Path) -> Path:
    """Write markdown content to file and return the output path."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(md_text, encoding="utf-8")
    return output_path


def render_markdown_to_pdf(
    md_text: str,
    output_path: Path,
    title: str = "Report",
    css_text: Optional[str] = None,
    enable_mathjax: bool = True,
    enable_echarts: bool = False,
    extra_head_html: Optional[str] = None,
    post_render_script: Optional[str] = None,
    wait_for_function: Optional[str] = None,
    wait_timeout_ms: int = 15000,
    pdf_options: Optional[Dict[str, Any]] = None,
) -> Path:
    """Render markdown content to a PDF using Playwright."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    full_html = markdown_to_html_document(
        md_text=md_text,
        title=title,
        css_text=css_text,
        enable_mathjax=enable_mathjax,
        enable_echarts=enable_echarts,
        extra_head_html=extra_head_html,
    )

    temp_html_path = output_path.parent / f".temp_render_{uuid4().hex}.html"

    default_pdf_options: Dict[str, Any] = {
        "path": str(output_path),
        "format": "A4",
        "print_background": True,
        "margin": {
            "top": "2cm",
            "bottom": "2cm",
            "left": "1.5cm",
            "right": "1.5cm",
        },
        "display_header_footer": True,
        "footer_template": (
            '<div style="font-size: 10px; text-align: center; width: 100%;">'
            'Page <span class="pageNumber"></span> of <span class="totalPages"></span>'
            "</div>"
        ),
        "header_template": "<div></div>",
    }

    if pdf_options:
        default_pdf_options.update(pdf_options)

    try:
        temp_html_path.write_text(full_html, encoding="utf-8")

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"file://{temp_html_path.absolute()}", wait_until="networkidle")

            if enable_mathjax:
                try:
                    page.wait_for_function("() => window.MathJax", timeout=10000)
                    page.evaluate(
                        """
                        async () => {
                            if (window.MathJax && window.MathJax.startup) {
                                await window.MathJax.startup.promise;
                            }
                        }
                        """
                    )
                    logger.info("MathJax 渲染已完成。")
                except Exception as ex:
                    logger.warning(
                        f"MathJax 等待已跳过或失败（无数学公式时可忽略）：{ex}"
                    )

            if post_render_script:
                try:
                    page.evaluate(post_render_script)
                except Exception as ex:
                    logger.warning(f"后置渲染脚本执行失败：{ex}")

            if wait_for_function:
                try:
                    page.wait_for_function(wait_for_function, timeout=wait_timeout_ms)
                except Exception as ex:
                    logger.warning(f"wait_for_function 已跳过或失败：{ex}")

            page.pdf(**default_pdf_options)
            browser.close()

        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError(f"PDF 生成失败：输出文件缺失或为空：{output_path}")

        logger.success(f"PDF 生成成功：{output_path}")
        return output_path
    except Exception as ex:
        logger.error(f"PDF 生成失败：{ex}")
        raise
    finally:
        if temp_html_path.exists():
            temp_html_path.unlink()
