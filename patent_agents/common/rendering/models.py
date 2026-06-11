"""
Typed models for shared rendering helpers.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class EChartSpec:
    """ECharts render specification for one chart container."""

    element_id: str
    data_key: str
    title: str
    chart_type: str = "donut"
