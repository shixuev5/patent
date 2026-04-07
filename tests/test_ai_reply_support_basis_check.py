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


def test_build_user_prompt_prefers_feature_after_text_as_support_anchor() -> None:
    node = SupportBasisCheckNode()

    prompt = node._build_user_prompt(
        [
            {
                "amendment_id": "A1",
                "feature_text": "基于轮胎的RRC值控制车辆的目标加速度",
                "feature_before_text": "",
                "feature_after_text": "轮胎的RRC值小于预定阈值时控制车辆的加速度",
                "amendment_kind": "spec_feature_addition",
            }
        ],
        "【具体实施方式】\n实施方式文本",
    )

    assert "feature_anchor_text" in prompt
    assert "feature_anchor_text` 为修改后原句片段" in prompt
    assert "轮胎的RRC值小于预定阈值时控制车辆的加速度" in prompt
    assert "基于轮胎的RRC值控制车辆的目标加速度" in prompt


def test_build_user_prompt_falls_back_to_feature_text_when_feature_after_text_missing() -> None:
    node = SupportBasisCheckNode()

    prompt = node._build_user_prompt(
        [
            {
                "amendment_id": "A2",
                "feature_text": "车辆加速度受轮胎RRC值影响",
                "feature_before_text": "",
                "feature_after_text": "",
                "amendment_kind": "spec_feature_addition",
            }
        ],
        "【发明内容】\n发明内容文本",
    )

    assert '"feature_anchor_text": "车辆加速度受轮胎RRC值影响"' in prompt
