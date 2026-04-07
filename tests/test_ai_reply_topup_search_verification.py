from agents.ai_reply.src.nodes.topup_search_verification import TopupSearchVerificationNode
from agents.ai_reply.src.retrieval_utils import make_query_spec


def test_topup_search_verification_builds_prefix_messages_from_evidence_cards() -> None:
    node = TopupSearchVerificationNode()

    messages = node._build_prefix_messages(
        [
            {
                "doc_id": "D1",
                "quote": "对比文件公开了该控制关系。",
                "location": "段落[0012]",
                "analysis": "直接对应新增特征",
                "source_url": "https://example.com/d1",
                "source_title": "对比文件1",
                "source_type": "comparison_document",
            }
        ]
    )

    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "证据卡 D1" in messages[1]["content"]
    assert "对比文件公开了该控制关系。" in messages[1]["content"]
    assert "https://example.com/d1" in messages[1]["content"]


def test_topup_search_verification_runs_followup_search_on_low_confidence(monkeypatch) -> None:
    node = TopupSearchVerificationNode()
    evaluate_calls = []
    local_search_calls = []
    external_search_calls = []

    def _fake_search_local_evidence(**kwargs):
        local_search_calls.append(kwargs.get("extra_queries"))
        if kwargs.get("extra_queries"):
            return (
                [
                    {
                        "doc_id": "D1",
                        "quote": "对比文件补充公开了目标加速度控制细节。",
                        "location": "段落[0020]",
                        "analysis": "补充命中",
                        "source_type": "comparison_document",
                    }
                ],
                {"queries": kwargs.get("extra_queries", []), "selected_cards": []},
            )
        return (
            [
                {
                    "doc_id": "D1",
                    "quote": "对比文件初次公开了轮胎参数控制。",
                    "location": "段落[0010]",
                    "analysis": "首次命中",
                    "source_type": "comparison_document",
                }
            ],
            {"queries": ["首次查询"], "selected_cards": []},
        )

    def _fake_search_evidence(**kwargs):
        external_search_calls.append(kwargs["queries"])
        if any("区别特征" in str((query or {}).get("text", "")) for queries in kwargs["queries"].values() for query in queries):
            return (
                [
                    {
                        "doc_id": "EXT2",
                        "snippet": "现有技术明确公开基于RRC值控制目标加速度。",
                        "source_type": "tavily",
                        "published": "2020-01-01",
                        "url": "https://example.com/ext2",
                        "title": "补充外部证据",
                    }
                ],
                ["tavily"],
                {"retrieval": {"tavily": {"queries": kwargs["queries"].get("tavily", []), "filters": {}, "result_count": 1, "results": []}}},
            )
        return (
            [
                {
                    "doc_id": "EXT1",
                    "snippet": "现有技术提到轮胎参数与车辆控制相关。",
                    "source_type": "tavily",
                    "published": "2019-01-01",
                    "url": "https://example.com/ext1",
                    "title": "首次外部证据",
                }
            ],
            ["tavily"],
            {"retrieval": {"tavily": {"queries": kwargs["queries"].get("tavily", []), "filters": {}, "result_count": 1, "results": []}}},
        )

    def _fake_evaluate_with_evidence_cards(**kwargs):
        evaluate_calls.append(
            {
                "queries": kwargs["external_queries"],
                "doc_ids": [item.get("doc_id") for item in kwargs["evidence_cards"]],
            }
        )
        if len(evaluate_calls) == 1:
            return {
                "examiner_opinion": {"type": "document_based", "supporting_docs": [{"doc_id": "D1", "cited_text": ""}], "reasoning": "首轮证据不足"},
                "applicant_opinion": {"type": "fact_dispute", "reasoning": "申请人认为未公开", "core_conflict": "是否公开RRC控制"},
                "assessment": {"verdict": "INCONCLUSIVE", "reasoning": "需要更多区别特征证据", "confidence": 0.35, "examiner_rejection_rationale": ""},
                "evidence": [{"doc_id": "D1", "quote": "对比文件初次公开了轮胎参数控制。"}],
                "used_doc_ids": ["D1"],
            }
        return {
            "examiner_opinion": {"type": "document_based", "supporting_docs": [{"doc_id": "EXT2", "cited_text": ""}], "reasoning": "二次检索证据足够"},
            "applicant_opinion": {"type": "fact_dispute", "reasoning": "申请人认为未公开", "core_conflict": "是否公开RRC控制"},
            "assessment": {"verdict": "EXAMINER_CORRECT", "reasoning": "补充证据直接公开该特征", "confidence": 0.86, "examiner_rejection_rationale": ""},
            "evidence": [{"doc_id": "EXT2", "quote": "现有技术明确公开基于RRC值控制目标加速度。"}],
            "used_doc_ids": ["EXT2"],
        }

    monkeypatch.setattr(node, "_search_local_evidence", _fake_search_local_evidence)
    monkeypatch.setattr(
        node,
        "_build_engine_queries",
        lambda *args, **kwargs: {
            "openalex": [make_query_spec('"rrc target acceleration" AND implementation', "boolean", "anchor")],
            "zhihuiya": [make_query_spec('"RRC 目标加速度" AND 专利 AND 技术公开', "lexical", "core_patent")],
            "tavily": [make_query_spec("RRC 目标加速度 技术公开 实现方案 白皮书 论文", "web", "technical")],
        },
    )
    monkeypatch.setattr(
        node,
        "_build_followup_engine_queries",
        lambda *args, **kwargs: {
            "openalex": [make_query_spec('"rrc target acceleration" AND comparative study', "boolean", "anchor")],
            "zhihuiya": [make_query_spec('"RRC 目标加速度" AND 区别特征 AND 现有技术', "lexical", "core_patent")],
            "tavily": [make_query_spec("RRC 目标加速度 区别特征 技术公开 实现方案", "web", "technical")],
        },
    )
    monkeypatch.setattr(node.external_evidence_aggregator, "search_evidence", _fake_search_evidence)
    monkeypatch.setattr(node, "_evaluate_with_evidence_cards", _fake_evaluate_with_evidence_cards)

    dispute, assessment = node._evaluate_task(
        task={"task_id": "F1", "claim_ids": ["1"], "feature_text": "基于轮胎的RRC值控制车辆的目标加速度"},
        claims=[{"claim_id": "1", "claim_text": "权利要求1: 一种车辆控制装置。"}],
        comparison_docs={"D1": {"title": "D1", "location": "D1", "content": "..." }},
        priority_date="2020-01-01",
        local_retriever=None,
    )

    assert len(evaluate_calls) == 2
    assert len(local_search_calls) == 2
    assert len(external_search_calls) == 2
    assert assessment["assessment"]["verdict"] == "EXAMINER_CORRECT"
    assert assessment["trace"]["followup_retrieval"] != {}
    assert dispute["examiner_opinion"]["reasoning"] == "二次检索证据足够"


def test_topup_search_verification_prefers_search_feature_text_for_retrieval(monkeypatch) -> None:
    node = TopupSearchVerificationNode()
    local_queries = []
    engine_query_inputs = []

    monkeypatch.setattr(
        node,
        "_search_local_evidence",
        lambda **kwargs: (
            local_queries.append(kwargs["search_feature_text"]) or [],
            {"queries": [], "selected_cards": []},
        ),
    )
    monkeypatch.setattr(
        node,
        "_build_engine_queries",
        lambda task, claim_text, feature_text, priority_date: (
            engine_query_inputs.append(feature_text) or {"openalex": [], "zhihuiya": [], "tavily": []}
        ),
    )
    monkeypatch.setattr(node.external_evidence_aggregator, "search_evidence", lambda **kwargs: ([], [], {}))
    monkeypatch.setattr(node, "_build_evidence_cards", lambda **kwargs: ([], {}))
    monkeypatch.setattr(
        node,
        "_evaluate_with_evidence_cards",
        lambda **kwargs: {
            "examiner_opinion": {"type": "common_knowledge_based", "supporting_docs": [], "reasoning": "证据不足"},
            "applicant_opinion": {"type": "logic_dispute", "reasoning": "申请人主张缺少启示", "core_conflict": "是否存在启示"},
            "assessment": {"verdict": "INCONCLUSIVE", "reasoning": "证据不足", "confidence": 0.2, "examiner_rejection_rationale": ""},
            "evidence": [],
            "used_doc_ids": [],
        },
    )
    monkeypatch.setattr(node, "_should_run_followup", lambda parsed, evidence_cards: False)

    node._evaluate_task(
        task={
            "task_id": "A1",
            "claim_ids": ["1"],
            "feature_text": "基于轮胎信息控制车辆加速度",
            "search_feature_text": "基于轮胎的RRC值控制车辆的目标加速度",
        },
        claims=[{"claim_id": "1", "claim_text": "权利要求1: 一种车辆控制装置。"}],
        comparison_docs={"D1": {"title": "D1", "location": "D1", "content": "..."}},
        priority_date="2020-01-01",
        local_retriever=None,
    )

    assert local_queries == ["基于轮胎的RRC值控制车辆的目标加速度"]
    assert engine_query_inputs == ["基于轮胎的RRC值控制车辆的目标加速度"]
