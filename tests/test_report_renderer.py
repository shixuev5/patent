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
    assert "```text\n　　关键词A\n```\n" in content


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
                {
                    "element_name": "效果限定E1",
                    "element_role": "Effect",
                    "block_id": "E",
                    "effect_cluster_ids": ["E1"],
                    "element_type": "Parameter_Condition",
                    "keywords_zh": ["降低摩擦"],
                    "keywords_en": ["friction* reduc*"],
                    "ipc_cpc_ref": ["F16N 3/00"],
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
    assert "效果限定E1" in first_group

    assert "特征B2" in second_group
    assert "特征B1" not in second_group
    assert "主题A" in second_group
    assert "共通C" in second_group
    assert "效果限定E1" not in second_group
    assert content.count("布尔检索策略配置指南") == 1
    assert "效果簇" not in content
    assert "关联技术效果" not in content
    assert "属性标签" not in content


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
                "content": "legacy query\nnext line",
            },
        }
    )

    assert "## 2. 检索要素表" in content
    assert "## 3. 语义检索" in content
    assert "```text\n　　legacy query\n　　next line\n```\n" in content


def test_render_analysis_section_accepts_non_numeric_tcs_score() -> None:
    renderer = ReportRenderer(patent_data={})
    content = renderer._render_analysis_section(
        {
            "ai_title": "测试报告",
            "ai_abstract": "摘要",
            "technical_field": "技术领域",
            "technical_problem": "技术问题",
            "technical_scheme": "技术方案",
            "technical_means": "技术手段",
            "technical_effects": [
                {
                    "effect": "核心效果",
                    "tcs_score": "5",
                    "contributing_features": ["特征A"],
                },
                {
                    "effect": "异常效果",
                    "tcs_score": "暂无",
                    "contributing_features": ["特征B"],
                },
            ],
            "figure_explanations": [],
        }
    )

    assert "color: #c7254e" in content
    assert "🔴 5</span>" in content
    assert "异常效果" in content


def test_render_analysis_section_shows_array_dependent_on() -> None:
    renderer = ReportRenderer(patent_data={})
    content = renderer._render_analysis_section(
        {
            "ai_title": "测试报告",
            "ai_abstract": "摘要",
            "technical_field": "技术领域",
            "technical_problem": "技术问题",
            "technical_scheme": "技术方案",
            "technical_means": "技术手段",
            "technical_features": [
                {"name": "核心特征A", "description": "核心A描述"},
                {"name": "协同特征X", "description": "协同X描述"},
            ],
            "technical_effects": [
                {"effect": "核心效果", "tcs_score": 5, "contributing_features": ["核心特征A"]},
                {
                    "effect": "协同效果",
                    "tcs_score": 4,
                    "dependent_on": ["核心特征Z", "核心特征Y"],
                    "contributing_features": ["协同特征X"],
                },
            ],
            "figure_explanations": [],
        }
    )

    assert "依附: 核心特征Z, 核心特征Y" in content


def test_render_analysis_section_renders_feature_numbering_by_claim_order() -> None:
    renderer = ReportRenderer(
        patent_data={
            "claims": [
                {"claim_id": "1", "claim_type": "independent", "parent_claim_ids": []},
                {"claim_id": "2", "claim_type": "dependent", "parent_claim_ids": ["1"]},
                {"claim_id": "3", "claim_type": "independent", "parent_claim_ids": []},
            ]
        }
    )
    content = renderer._render_analysis_section(
        {
            "ai_title": "测试报告",
            "ai_abstract": "摘要",
            "technical_field": "技术领域",
            "technical_problem": "技术问题",
            "technical_scheme": "技术方案",
            "technical_means": "技术手段",
            "technical_features": [
                {
                    "name": "从属特征",
                    "claim_id": "2",
                    "description": "从属描述",
                    "rationale": "[权2] 从属限定 - 逻辑",
                    "is_distinguishing": False,
                },
                {
                    "name": "独立特征",
                    "claim_id": "1",
                    "description": "独立描述",
                    "rationale": "[权1] 特征部分 - 逻辑",
                    "is_distinguishing": True,
                },
            ],
            "technical_effects": [],
            "figure_explanations": [],
        }
    )

    assert "### 关键技术特征表" in content
    assert "特征编号" in content
    assert "1.1" in content
    assert "2.1" in content
    assert "独权" not in content
    assert "引用权 1" in content
    assert content.index("独立特征") < content.index("从属特征")


def test_render_analysis_section_does_not_bold_independent_preamble_feature_name() -> None:
    renderer = ReportRenderer(
        patent_data={
            "claims": [
                {"claim_id": "1", "claim_type": "independent", "parent_claim_ids": []},
                {"claim_id": "2", "claim_type": "dependent", "parent_claim_ids": ["1"]},
            ]
        }
    )
    content = renderer._render_analysis_section(
        {
            "ai_title": "测试报告",
            "ai_abstract": "摘要",
            "technical_field": "技术领域",
            "technical_problem": "技术问题",
            "technical_scheme": "技术方案",
            "technical_means": "技术手段",
            "technical_features": [
                {
                    "name": "前序特征",
                    "claim_id": "1",
                    "claim_source": "independent",
                    "description": "前序描述",
                    "rationale": "[权1] 前序部分 - 背景限定",
                    "is_distinguishing": False,
                },
                {
                    "name": "区别特征",
                    "claim_id": "1",
                    "claim_source": "independent",
                    "description": "区别描述",
                    "rationale": "[权1] 特征部分 - 解决技术问题",
                    "is_distinguishing": True,
                },
                {
                    "name": "从权特征",
                    "claim_id": "2",
                    "claim_source": "dependent",
                    "description": "从权描述",
                    "rationale": "[权2] 从属限定 - 进一步限定",
                    "is_distinguishing": False,
                },
            ],
            "technical_effects": [],
            "figure_explanations": [],
        }
    )

    assert '<td style="font-weight: normal; color: #666;">前序特征</td>' in content
    assert '<td style="font-weight: bold; color: #222;">区别特征</td>' in content
    assert '<td style="font-weight: normal; color: #222;">从权特征</td>' in content
    assert "<td style=\"text-align: center;\">前序特征</td>" in content
    assert "<td style=\"text-align: center;\">区别特征</td>" in content
    assert "<td style=\"text-align: center;\">从权特征</td>" in content


def test_get_search_matrix_guide_returns_wrapped_newlines() -> None:
    renderer = ReportRenderer(patent_data={})
    guide = renderer._get_search_matrix_guide()

    assert guide.startswith("\n<div")
    assert guide.endswith("\n")


def test_render_matrix_table_shows_block_a_and_term_frequency_badges() -> None:
    renderer = ReportRenderer(patent_data={})
    lines = renderer._render_matrix_table(
        [
            {
                "element_name": "应用场景",
                "block_id": "A",
                "priority_tier": "assist",
                "term_frequency": "high",
                "element_type": "Product_Structure",
                "keywords_zh": ["应用场景"],
                "keywords_en": ["application*"],
                "ipc_cpc_ref": [],
            },
            {
                "element_name": "关键特征",
                "block_id": "B1",
                "priority_tier": "core",
                "term_frequency": "low",
                "element_type": "Product_Structure",
                "keywords_zh": ["关键特征"],
                "keywords_en": ["feature*"],
                "ipc_cpc_ref": [],
            },
        ]
    )
    table = "\n".join(lines)

    assert "基准环境" in table
    assert "限字段 TAC" in table
    assert "全文 TX" in table
    assert "margin-top:6px; font-size:12px; color:#888;" in table
    assert "margin-top:4px;'><span style='border:1px solid #b8daff;" in table
