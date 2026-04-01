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


def test_extract_comparison_documents_keeps_body_order_and_backfills_dates_from_table() -> None:
    markdown = """
申请号：202510236679.2

第一次审查意见通知书

本通知书引用下列对比文件(其编号在今后的审查过程中继续沿用)：
<table style="width:97%;">
<tr><td style="text-align: center;">编号</td><td style="text-align: center;">文 件 号 或 名 称</td><td style="text-align: center;">公开日期</td></tr>
<tr><td style="text-align: center;">1</td><td style="text-align: center;">CN106226035A</td><td style="text-align: center;">2016-12-14</td></tr>
<tr><td style="text-align: center;">2</td><td style="text-align: center;">CN113567089A</td><td style="text-align: center;">2021-10-29</td></tr>
<tr><td style="text-align: center;">3</td><td style="text-align: center;">CN108225745A</td><td style="text-align: center;">2018-06-29</td></tr>
</table>

1、权利要求1不具备创造性。对比文件1（CN108225745A）为最接近的现有技术，结合对比文件2（CN106226035A）和对比文件3（CN113567089A）可得。
"""

    result = OfficeActionExtractor().extract(markdown)

    assert [(item.document_id, item.document_number) for item in result.comparison_documents] == [
        ("D1", "CN108225745A"),
        ("D2", "CN106226035A"),
        ("D3", "CN113567089A"),
    ]
    assert [item.publication_date for item in result.comparison_documents] == [
        "2018-06-29",
        "2016-12-14",
        "2021-10-29",
    ]
