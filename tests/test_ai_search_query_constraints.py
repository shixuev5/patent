from agents.ai_search.src.query_constraints import (
    build_query_text,
    build_search_constraints,
    build_semantic_text,
    resolve_cutoff_date,
)


def test_resolve_cutoff_date_prefers_priority_date():
    cutoff = resolve_cutoff_date(
        {
            "filing_date": "2024-03-01",
            "priority_date": "2023-10-15",
        }
    )

    assert cutoff == "2023-10-15"


def test_resolve_cutoff_date_falls_back_to_filing_date():
    cutoff = resolve_cutoff_date(
        {
            "filing_date": "2024-03-01",
            "priority_date": None,
        }
    )

    assert cutoff == "2024-03-01"


def test_build_query_text_includes_applicant_and_cutoff_date():
    query_text = build_query_text(
        {
            "must_terms_zh": ["异常检测", "网络摄像机"],
            "should_terms_zh": ["边缘计算"],
            "negative_terms": ["云端"],
        },
        {
            "applicants": ["杭州海康威视数字技术股份有限公司"],
            "priority_date": "2023-10-15",
        },
    )

    assert "AN:(\"杭州海康威视数字技术股份有限公司\")" in query_text
    assert "PBD:[* TO 20231015]" in query_text
    assert "\"异常检测\"" in query_text


def test_build_search_constraints_and_semantic_text():
    constraints = build_search_constraints(
        {
            "applicants": ["杭州海康威视数字技术股份有限公司"],
            "filing_date": "2024-03-01",
        }
    )
    semantic_text = build_semantic_text(
        {
            "goal": "",
            "must_terms_zh": [],
            "should_terms_zh": [],
            "negative_terms": [],
            "result_limit": 10,
        },
        {
            "applicants": ["杭州海康威视数字技术股份有限公司"],
            "filing_date": "2024-03-01",
        },
    )

    assert constraints["cutoff_date_yyyymmdd"] == "20240301"
    assert "相关申请人：杭州海康威视数字技术股份有限公司" in semantic_text
