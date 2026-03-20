from agents.ai_reply.src.nodes.support_basis_check import SupportBasisCheckNode


def test_build_specification_context_includes_multiple_description_sections() -> None:
    node = SupportBasisCheckNode()

    context = node._build_specification_context(
        {
            "summary_of_invention": "发明内容文本",
            "technical_effect": "技术效果文本",
            "brief_description_of_drawings": "图1为结构示意图。",
            "detailed_description": "实施方式文本",
        }
    )

    assert "【发明内容】\n发明内容文本" in context
    assert "【有益效果/技术效果】\n技术效果文本" in context
    assert "【附图说明】\n图1为结构示意图。" in context
    assert "【具体实施方式】\n实施方式文本" in context


def test_build_specification_context_is_non_empty_without_detailed_description() -> None:
    node = SupportBasisCheckNode()

    context = node._build_specification_context(
        {
            "summary_of_invention": "发明内容文本",
            "technical_effect": "",
            "brief_description_of_drawings": "",
            "detailed_description": "",
        }
    )

    assert context == "【发明内容】\n发明内容文本"
