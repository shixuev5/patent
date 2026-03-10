from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.patent_analysis.src.engines.renderer import ReportRenderer


def test_render_formal_check_section_includes_legal_source_note() -> None:
    renderer = ReportRenderer(patent_data={})

    content = renderer._render_formal_check_section(
        {"consistency": "✅ **检查通过**：说明书文字部分与附图标记完全对应。"}
    )

    assert "# 形式缺陷审查报告" in content
    assert "## 1. 审查依据" in content
    assert "## 2. 最终结论" in content
    assert "《中华人民共和国专利法实施细则》" in content
    assert "第二十一条" in content
    assert "附图中除必需的词语外，不应当含有其他注释" in content


def test_render_search_section_shows_applicants_and_inventors() -> None:
    renderer = ReportRenderer(
        patent_data={
            "bibliographic_data": {
                "invention_title": "一种装置",
                "application_date": "2024.01.01",
                "priority_date": "2023.05.20",
                "applicants": [{"name": "甲公司"}, {"name": "乙研究院"}],
                "inventors": ["张三", "李四"],
            }
        }
    )

    content = renderer._render_search_section(
        {
            "search_matrix": [],
            "semantic_strategy": {"name": "语义检索", "description": "desc", "content": "query"},
        }
    )

    assert "# 专利审查检索策略建议书" in content
    assert "- **申请人**: 甲公司、乙研究院" in content
    assert "- **发明人**: 张三、李四" in content


def test_render_search_section_shows_dash_when_applicants_or_inventors_missing() -> None:
    renderer = ReportRenderer(
        patent_data={
            "bibliographic_data": {
                "invention_title": "一种方法",
                "application_date": "2024.02.02",
                "applicants": None,
                "inventors": "",
            }
        }
    )

    content = renderer._render_search_section(
        {
            "search_matrix": [],
            "semantic_strategy": {"name": "语义检索", "description": "desc", "content": "query"},
        }
    )

    assert "- **申请人**: -" in content
    assert "- **发明人**: -" in content
