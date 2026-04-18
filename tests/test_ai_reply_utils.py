from agents.ai_reply.src.utils import (
    is_patent_application_number,
    is_patent_document,
    normalize_quote_translation,
    normalize_patent_identifier,
    quote_needs_translation,
)


def test_is_patent_document_supports_common_publication_number_variants() -> None:
    positives = [
        "CN108010365A",
        "CN 108010365 A",
        "US 2023/0403781 A1",
        "US-10234567-B2",
        "WO 2020/123456 A1",
        "EP 3 379 496 A1",
        "EP1234567B1",
        "JP2020000001A",
        "JPH115534A",
        "JPS62123456A",
        "KR2020000001A",
        "TW202412345A",
        "CH708501A1",
        "DE102023000123A1",
        "FR3123456A1",
        "GB2612345A",
        "AU2023201234A1",
        "CA3145678A1",
    ]

    negatives = [
        "",
        "ISO 12345",
        "GB/T 7714",
        "IEEE 802.11",
        "202211411308.6",
        "一种列车运行状态显示方法和乘客信息系统",
    ]

    assert all(is_patent_document(value) for value in positives)
    assert not any(is_patent_document(value) for value in negatives)


def test_is_patent_application_number_detects_common_formats() -> None:
    positives = [
        "202211411308.6",
        "202310001234.5",
        "202310658730.X",
        "PCT/CN2024/123456",
    ]
    negatives = [
        "CN108010365A",
        "US 2023/0403781 A1",
        "ISO 12345",
    ]

    assert all(is_patent_application_number(value) for value in positives)
    assert not any(is_patent_application_number(value) for value in negatives)


def test_normalize_patent_identifier_strips_common_separators() -> None:
    assert normalize_patent_identifier("US 2023/0403781 A1") == "US20230403781A1"
    assert normalize_patent_identifier("EP-3-379-496-A1") == "EP3379496A1"


def test_quote_needs_translation_detects_non_chinese_quotes() -> None:
    assert quote_needs_translation("A system and a method for monitoring pressure.") is True
    assert quote_needs_translation("車両がトンネル内走行中に、車内圧力検出装置５で検出した車内圧力値") is True
    assert quote_needs_translation("该专利公开了车内压力监测与报警的基础系统架构。") is False
    assert quote_needs_translation("D1-2020") is False


def test_normalize_quote_translation_only_keeps_valid_non_chinese_translation() -> None:
    assert normalize_quote_translation("A system and a method.", "一种系统和方法。") == "一种系统和方法。"
    assert normalize_quote_translation("A system and a method.", "A system and a method.") == ""
    assert normalize_quote_translation("该专利公开了基础系统架构。", "This patent discloses the basic architecture.") == ""
