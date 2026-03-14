from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.patent_analysis.src.engines.renderer import ReportRenderer


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
            "semantic_strategy": {
                "name": "语义检索",
                "description": "desc",
                "queries": [
                    {
                        "block_id": "B1",
                        "effect_cluster_ids": ["E1"],
                        "effect": "效果描述1",
                        "content": "query",
                    }
                ],
            },
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
            "semantic_strategy": {
                "name": "语义检索",
                "description": "desc",
                "queries": [
                    {
                        "block_id": "B1",
                        "effect_cluster_ids": ["E1"],
                        "effect": "效果描述1",
                        "content": "query",
                    }
                ],
            },
        }
    )

    assert "- **申请人**: -" in content
    assert "- **发明人**: -" in content


def test_render_analysis_section_numbers_are_continuous_without_background_knowledge() -> None:
    renderer = ReportRenderer(patent_data={})
    content = renderer._render_analysis_section(
        {
            "ai_title": "测试报告",
            "ai_abstract": "摘要",
            "technical_field": "技术领域",
            "technical_problem": "技术问题",
            "background_knowledge": [],
            "technical_scheme": "技术方案",
            "technical_means": "技术手段",
            "technical_effects": [],
            "figure_explanations": [],
        }
    )

    assert "## 1. 技术领域" in content
    assert "## 2. 现有技术问题" in content
    assert "## 3. 技术方案概要" in content
    assert "## 4. 核心技术手段" in content
    assert "## 5. 技术效果" in content
    assert "## 6. 图解说明" in content
    assert "核心概念百科" not in content


def test_render_search_section_sanitizes_semantic_html_fragments() -> None:
    renderer = ReportRenderer(
        patent_data={
            "bibliographic_data": {
                "invention_title": "一种方法",
                "application_date": "2024.02.02",
            }
        }
    )

    content = renderer._render_search_section(
        {
            "search_matrix": [],
            "semantic_strategy": {
                "name": "语义检索",
                "description": "基于核心技术词",
                "queries": [
                    {
                        "block_id": "B1",
                        "effect_cluster_ids": ["E1"],
                        "effect": "效果描述A",
                        "content": "```html\n<tr><td>关键词A</td></tr>\n```",
                    }
                ],
            },
        }
    )

    assert "关键词A" in content
    assert "<td>关键词A</td>" not in content
    assert "<tr>" not in content
    assert "### 核心效果1：效果描述A" in content
    assert "效果描述A" in content


def test_render_search_section_groups_by_effect_and_filters_matrix() -> None:
    renderer = ReportRenderer(
        patent_data={
            "bibliographic_data": {
                "invention_title": "一种装置",
                "application_date": "2024.02.02",
            }
        }
    )

    content = renderer._render_search_section(
        {
            "search_matrix": [
                {
                    "element_name": "主题A",
                    "element_role": "Subject",
                    "block_id": "A",
                    "effect_cluster_ids": [],
                    "element_type": "Product_Structure",
                    "keywords_zh": ["主题A"],
                    "keywords_en": ["subject*"],
                    "ipc_cpc_ref": ["A61N 5/00"],
                },
                {
                    "element_name": "特征B1",
                    "element_role": "KeyFeature",
                    "block_id": "B1",
                    "effect_cluster_ids": ["E1"],
                    "element_type": "Product_Structure",
                    "keywords_zh": ["特征B1"],
                    "keywords_en": ["featureb1*"],
                    "ipc_cpc_ref": ["A61N 5/01"],
                },
                {
                    "element_name": "特征B2",
                    "element_role": "KeyFeature",
                    "block_id": "B2",
                    "effect_cluster_ids": ["E2"],
                    "element_type": "Product_Structure",
                    "keywords_zh": ["特征B2"],
                    "keywords_en": ["featureb2*"],
                    "ipc_cpc_ref": ["A61N 5/02"],
                },
                {
                    "element_name": "共通C",
                    "element_role": "Functional",
                    "block_id": "C",
                    "effect_cluster_ids": [],
                    "element_type": "Parameter_Condition",
                    "keywords_zh": ["共通C"],
                    "keywords_en": ["commonc*"],
                    "ipc_cpc_ref": ["A61N 5/03"],
                },
            ],
            "semantic_strategy": {
                "name": "语义检索",
                "description": "desc",
                "queries": [
                    {
                        "block_id": "B1",
                        "effect_cluster_ids": ["E1"],
                        "effect": "核心效果一",
                        "tcs_score": 5,
                        "content": "query1",
                    },
                    {
                        "block_id": "B2",
                        "effect_cluster_ids": ["E2"],
                        "effect": "核心效果二",
                        "tcs_score": 5,
                        "content": "query2",
                    },
                ],
            },
        }
    )

    assert "### 核心效果1：核心效果一" in content
    assert "### 核心效果2：核心效果二" in content
    first_group = content.split("### 核心效果1：核心效果一", 1)[1].split("### 核心效果2：核心效果二", 1)[0]
    second_group = content.split("### 核心效果2：核心效果二", 1)[1]

    assert "特征B1" in first_group
    assert "特征B2" not in first_group
    assert "主题A" in first_group
    assert "共通C" in first_group

    assert "特征B2" in second_group
    assert "特征B1" not in second_group
    assert "主题A" in second_group
    assert "共通C" in second_group


def test_render_search_section_falls_back_when_queries_missing() -> None:
    renderer = ReportRenderer(
        patent_data={
            "bibliographic_data": {
                "invention_title": "一种装置",
                "application_date": "2024.02.02",
            }
        }
    )

    content = renderer._render_search_section(
        {
            "search_matrix": [
                {
                    "element_name": "特征A",
                    "element_role": "Subject",
                    "block_id": "A",
                    "effect_cluster_ids": [],
                    "element_type": "Product_Structure",
                    "keywords_zh": ["特征A"],
                    "keywords_en": ["feature*"],
                    "ipc_cpc_ref": ["A61N 5/00"],
                }
            ],
            "semantic_strategy": {
                "name": "语义检索",
                "description": "desc",
                "content": "legacy query",
            },
        }
    )

    assert "## 2. 检索要素表" in content
    assert "## 3. 语义检索" in content
    assert "legacy query" in content
