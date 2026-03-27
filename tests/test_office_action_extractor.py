from __future__ import annotations

from agents.common.office_action_structuring.rule_based_extractor import OfficeActionExtractor


def test_extract_latest_notice_section_accepts_markdown_heading() -> None:
    markdown = """
申请号：202510236679.2

# 第一次审查意见通知书

1、权利要求1不具备专利法第二十二条第三款规定的创造性。
"""

    result = OfficeActionExtractor().extract(markdown)

    assert result.application_number == "202510236679.2"
    assert result.current_notice_round == 1
    assert len(result.paragraphs) == 1


def test_extract_latest_notice_section_accepts_bold_title() -> None:
    markdown = """
申请号：202510236679.2

**第一次审查意见通知书**

1、权利要求1不具备专利法第二十二条第三款规定的创造性。
"""

    result = OfficeActionExtractor().extract(markdown)

    assert result.current_notice_round == 1
    assert len(result.paragraphs) == 1


def test_extract_latest_notice_section_accepts_plain_title() -> None:
    markdown = """
申请号：202510236679.2

第一次审查意见通知书

1、权利要求1不具备专利法第二十二条第三款规定的创造性。
"""

    result = OfficeActionExtractor().extract(markdown)

    assert result.current_notice_round == 1
    assert len(result.paragraphs) == 1
