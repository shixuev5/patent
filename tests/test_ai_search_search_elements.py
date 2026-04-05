from agents.ai_search.src.subagents.search_elements import normalize_search_elements_payload


def test_normalize_search_elements_payload_keeps_applicants_optional():
    payload = normalize_search_elements_payload(
        {
            "status": "complete",
            "objective": "检索视频异常检测方案",
            "applicants": [],
            "filing_date": "2024-03-01",
            "search_elements": [
                {
                    "feature": "异常检测",
                    "keywords_zh": ["异常检测"],
                    "keywords_en": ["anomaly detection"],
                }
            ],
            "missing_items": [],
            "clarification_summary": "已有核心技术要素。",
        }
    )

    assert payload["status"] == "complete"
    assert payload["applicants"] == []
    assert "未提供申请人，无法执行申请人追溯检索。" in payload["clarification_summary"]


def test_normalize_search_elements_payload_adds_missing_date_boundary():
    payload = normalize_search_elements_payload(
        {
            "status": "complete",
            "objective": "检索视频异常检测方案",
            "search_elements": [
                {
                    "element_name": "异常检测",
                    "keywords_zh": ["异常检测"],
                }
            ],
            "missing_items": [],
        }
    )

    assert payload["status"] == "complete"
    assert payload["filing_date"] is None
    assert payload["priority_date"] is None
    assert "申请日或优先权日" in payload["missing_items"]


def test_normalize_search_elements_payload_drops_synonyms_and_keeps_optional_notes():
    payload = normalize_search_elements_payload(
        {
            "status": "complete",
            "objective": "检索视频异常检测方案",
            "search_elements": [
                {
                    "element_name": "异常检测",
                    "keywords_zh": ["异常检测"],
                    "keywords_en": ["anomaly detection"],
                    "synonyms": ["异常识别", "anomaly recognition"],
                    "notes": "核心算法要素",
                }
            ],
        }
    )

    assert "synonyms" not in payload["search_elements"][0]
    assert payload["search_elements"][0]["notes"] == "核心算法要素"
