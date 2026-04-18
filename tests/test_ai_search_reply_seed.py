from backend.ai_search.reply_seed import (
    build_execution_spec_from_reply,
    seed_prompt_from_reply,
    seed_search_elements_from_reply,
)


def _reply_report() -> dict:
    return {
        "task_id": "reply-task-1",
        "title": "AI 答复任务 - 示例",
        "pn": "CN123456A",
        "notice_context": {
            "current_notice_round": 2,
        },
        "search_followup_section": {
            "needed": True,
            "status": "complete",
            "objective": "围绕新增特征继续补检。",
            "applicants": ["示例申请人"],
            "filing_date": "2023-01-15",
            "priority_date": "2022-06-01",
            "trigger_reasons": ["现有核查结论暂不确定"],
            "gap_summaries": [
                {
                    "claim_ids": ["1"],
                    "feature_text": "星间激光通信模块",
                    "gap_summary": "当前证据未稳定覆盖新增特征。",
                }
            ],
            "search_elements": [
                {
                    "element_name": "星间通信系统",
                    "keywords_zh": ["星间通信系统"],
                    "keywords_en": ["inter-satellite communication system"],
                    "block_id": "A",
                },
                {
                    "element_name": "星间激光通信模块",
                    "keywords_zh": ["星间激光通信", "激光通信模块"],
                    "keywords_en": ["laser inter-satellite communication"],
                    "block_id": "B1",
                },
            ],
            "suggested_constraints": {
                "comparison_document_ids": ["D1", "D2"],
                "notes": ["优先围绕未闭环新增特征补强证据。"],
            },
            "source_dispute_ids": ["TOPUP_A1"],
            "source_feature_ids": ["A1"],
            "missing_items": [],
        },
    }


def test_seed_search_elements_from_reply_maps_followup_section() -> None:
    seeded = seed_search_elements_from_reply(_reply_report())

    assert seeded["status"] == "complete"
    assert seeded["objective"] == "围绕新增特征继续补检。"
    assert seeded["comparison_document_ids"] == ["D1", "D2"]
    assert seeded["source_dispute_ids"] == ["TOPUP_A1"]
    assert seeded["search_elements"][0]["block_id"] == "A"
    assert seeded["search_elements"][1]["block_id"] == "B1"


def test_build_execution_spec_from_reply_uses_followup_scope() -> None:
    report = _reply_report()
    seeded = seed_search_elements_from_reply(report)
    spec = build_execution_spec_from_reply(report, seeded)

    assert spec["search_scope"]["objective"] == "围绕新增特征继续补检。"
    assert spec["search_scope"]["source"]["reply_task_id"] == "reply-task-1"
    assert spec["constraints"]["comparison_document_ids"] == ["D1", "D2"]
    assert spec["sub_plans"][0]["title"] == "答复补检计划"


def test_seed_prompt_from_reply_mentions_followup_goal() -> None:
    report = _reply_report()
    seeded = seed_search_elements_from_reply(report)
    prompt = seed_prompt_from_reply(report, seeded)

    assert "补检/检索建议" in prompt
    assert "星间激光通信模块" in prompt
    assert "D1" in prompt
