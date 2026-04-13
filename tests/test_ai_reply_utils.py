from agents.ai_reply.src.utils import (
    is_patent_application_number,
    is_patent_document,
    normalize_patent_identifier,
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
