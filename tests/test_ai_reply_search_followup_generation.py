from agents.ai_reply.src.nodes.search_followup_generation import SearchFollowupGenerationNode


def _prepared_materials() -> dict:
    return {
        "original_patent": {
            "application_number": "202310001234.5",
            "data": {
                "application_date": "2023-01-15",
                "bibliographic_data": {
                    "invention_title": "车辆控制方法",
                    "priority_date": "2022-06-01",
                    "applicants": [{"name": "示例申请人"}],
                },
                "abstract": "一种用于控制目标加速度的车辆控制方法。",
                "claims": [{"claim_text": "一种车辆控制装置，包括控制器。"}],
            },
        },
        "office_action": {
            "application_number": "202310001234.5",
            "current_notice_round": 2,
            "paragraphs": [],
        },
        "comparison_documents": [
            {"document_id": "D1", "document_number": "CN101", "is_patent": True, "data": {}},
        ],
    }


def test_search_followup_generation_returns_not_needed_section_without_triggers() -> None:
    node = SearchFollowupGenerationNode()

    result = node._generate_section(
        topup_tasks=[],
        evidence_assessments=[],
        prepared_materials=_prepared_materials(),
        claims_effective_structured=[{"claim_id": "1", "claim_text": "一种车辆控制装置。"}],
        disputes=[],
    )

    assert result["needed"] is False
    assert result["search_elements"] == []
    assert result["source_dispute_ids"] == []


def test_search_followup_generation_uses_llm_output_for_topup_tasks(monkeypatch) -> None:
    node = SearchFollowupGenerationNode()

    monkeypatch.setattr(
        node.llm_service,
        "invoke_text_json",
        lambda **kwargs: {
            "status": "complete",
            "objective": "围绕新增特征补充检索可用于创造性判断的现有技术证据。",
            "gap_summaries": [
                {
                    "claim_ids": ["1"],
                    "feature_text": "基于轮胎 RRC 值控制目标加速度",
                    "gap_type": "topup_feature",
                    "gap_summary": "现有证据尚未稳定覆盖该新增特征。",
                    "source_dispute_id": "TOPUP_A1",
                    "source_feature_id": "A1",
                }
            ],
            "search_elements": [
                {
                    "element_name": "RRC 目标加速度控制",
                    "keywords_zh": ["RRC", "目标加速度控制"],
                    "keywords_en": ["RRC", "target acceleration control"],
                    "block_id": "B",
                    "notes": "优先围绕新增特征检索",
                }
            ],
            "suggested_constraints": {
                "notes": ["优先检索优先权日之前公开的技术方案。"],
            },
        },
    )

    result = node._generate_section(
        topup_tasks=[
            {
                "task_id": "A1",
                "claim_ids": ["1"],
                "feature_text": "基于轮胎 RRC 值控制目标加速度",
                "search_feature_text": "基于轮胎 RRC 值控制车辆目标加速度",
            }
        ],
        evidence_assessments=[],
        prepared_materials=_prepared_materials(),
        claims_effective_structured=[{"claim_id": "1", "claim_text": "一种车辆控制装置，包括控制器。"}],
        disputes=[],
    )

    assert result["needed"] is True
    assert result["status"] == "complete"
    assert result["objective"].startswith("围绕新增特征补充检索")
    assert result["search_elements"][0]["block_id"] == "A"
    assert result["search_elements"][0]["element_name"] == "车辆控制方法"
    assert result["search_elements"][1]["element_name"] == "RRC 目标加速度控制"
    assert result["search_elements"][1]["block_id"] == "B1"
    assert result["source_feature_ids"] == ["A1"]
    assert result["source_dispute_ids"] == ["TOPUP_A1"]
    assert result["suggested_constraints"]["comparison_document_ids"] == ["D1"]


def test_search_followup_generation_triggers_for_low_confidence_response_dispute(monkeypatch) -> None:
    node = SearchFollowupGenerationNode()

    monkeypatch.setattr(
        node.llm_service,
        "invoke_text_json",
        lambda **kwargs: {
            "status": "complete",
            "objective": "围绕未闭环争点补充检索现有技术证据。",
            "search_elements": [
                {
                    "element_name": "锁定结构",
                    "keywords_zh": ["锁定结构", "定位架锁定"],
                    "keywords_en": ["locking structure"],
                    "block_id": "",
                    "notes": "优先补教材、手册类证据",
                }
            ],
            "gap_summaries": [],
            "suggested_constraints": {},
        },
    )

    result = node._generate_section(
        topup_tasks=[],
        evidence_assessments=[
            {
                "dispute_id": "DSP_1",
                "claim_ids": ["1"],
                "feature_text": "移动定位架的锁定结构",
                "assessment": {
                    "verdict": "INCONCLUSIVE",
                    "reasoning": "缺乏稳定证据",
                    "confidence": 0.35,
                    "examiner_rejection_rationale": "",
                },
                "evidence": [],
                "trace": {"used_doc_ids": [], "missing_doc_ids": ["D1"]},
            }
        ],
        prepared_materials=_prepared_materials(),
        claims_effective_structured=[{"claim_id": "1", "claim_text": "一种定位装置。"}],
        disputes=[
            {
                "dispute_id": "DSP_1",
                "origin": "response_dispute",
                "claim_ids": ["1"],
                "feature_text": "移动定位架的锁定结构",
                "examiner_opinion": {"type": "common_knowledge_based", "reasoning": "本领域常规手段"},
                "applicant_opinion": {"type": "logic_dispute", "reasoning": "缺乏教材证据", "core_conflict": "是否公知"},
            }
        ],
    )

    assert result["needed"] is True
    assert "现有核查结论暂不确定" in result["trigger_reasons"]
    assert "现有核查置信度偏低" in result["trigger_reasons"]
    assert "存在缺失或不可用的对比文件" in result["trigger_reasons"]
    assert result["search_elements"][0]["block_id"] == "A"
    assert result["search_elements"][1]["block_id"] == "B1"
    assert result["source_dispute_ids"] == ["DSP_1"]


def test_search_followup_generation_falls_back_when_llm_errors(monkeypatch) -> None:
    node = SearchFollowupGenerationNode()

    def _raise(**kwargs):
        raise RuntimeError("llm offline")

    monkeypatch.setattr(node.llm_service, "invoke_text_json", _raise)

    result = node._generate_section(
        topup_tasks=[
            {
                "task_id": "A2",
                "claim_ids": ["2"],
                "feature_text": "双通道同步控制",
            }
        ],
        evidence_assessments=[],
        prepared_materials=_prepared_materials(),
        claims_effective_structured=[{"claim_id": "2", "claim_text": "一种控制系统。"}],
        disputes=[],
    )

    assert result["needed"] is True
    assert result["status"] == "complete"
    assert result["search_elements"][0]["block_id"] == "A"
    assert result["search_elements"][1]["keywords_zh"] == ["双通道同步控制"]
    assert result["search_elements"][1]["block_id"] == "B1"
