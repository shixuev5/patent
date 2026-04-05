from agents.ai_reply.src.nodes.common_knowledge_verification import CommonKnowledgeVerificationNode


def test_common_knowledge_verification_runs_followup_search_on_low_confidence(monkeypatch) -> None:
    node = CommonKnowledgeVerificationNode()
    external_calls = []
    local_calls = []
    verify_calls = []

    def _fake_search_evidence(**kwargs):
        external_calls.append(kwargs["queries"])
        if any("教材" in query for queries in kwargs["queries"].values() for query in queries):
            return (
                [
                    {
                        "doc_id": "EXT2",
                        "snippet": "教材明确记载该技术手段属于常规技术手段。",
                        "source_type": "tavily",
                        "published": "2018-01-01",
                        "url": "https://example.com/ext2",
                        "title": "教材证据",
                    }
                ],
                ["tavily"],
                {"retrieval": {"tavily": {"queries": kwargs["queries"].get("tavily", []), "filters": {}, "result_count": 1, "results": []}}},
            )
        return (
            [
                {
                    "doc_id": "EXT1",
                    "snippet": "普通文献提到该技术手段。",
                    "source_type": "tavily",
                    "published": "2019-01-01",
                    "url": "https://example.com/ext1",
                    "title": "普通文献",
                }
            ],
            ["tavily"],
            {"retrieval": {"tavily": {"queries": kwargs["queries"].get("tavily", []), "filters": {}, "result_count": 1, "results": []}}},
        )

    def _fake_search_local_candidates(**kwargs):
        local_calls.append(kwargs["flat_queries"])
        return [], {
            "enabled": False,
            "fallback": "no_local_retriever",
            "queries": kwargs["flat_queries"],
            "queries_by_language": {},
            "doc_filters": kwargs["local_doc_ids"],
            "hit_chunks": [],
            "lexical_hits": [],
            "dense_hits": [],
            "fusion_hits": [],
        }

    def _fake_verify_single_dispute(**kwargs):
        verify_calls.append(
            {
                "queries": kwargs["queries_by_engine"],
                "doc_ids": [item.get("doc_id") for item in kwargs["evidence_cards"]],
            }
        )
        if len(verify_calls) == 1:
            return {
                "dispute_id": "DSP_1",
                "claim_ids": ["1"],
                "claim_text": kwargs["claim_text"],
                "feature_text": "移动定位架的锁定结构",
                "examiner_opinion": {"type": "common_knowledge_based", "reasoning": "本领域常规手段"},
                "applicant_opinion": {"type": "logic_dispute", "reasoning": "缺乏教材证据", "core_conflict": "是否公知"},
                "assessment": {"verdict": "INCONCLUSIVE", "reasoning": "缺乏教材或手册类证据", "confidence": 0.35, "examiner_rejection_rationale": ""},
                "evidence": [{"doc_id": "EXT1", "quote": "普通文献提到该技术手段。"}],
                "trace": {},
            }
        return {
            "dispute_id": "DSP_1",
            "claim_ids": ["1"],
            "claim_text": kwargs["claim_text"],
            "feature_text": "移动定位架的锁定结构",
            "examiner_opinion": {"type": "common_knowledge_based", "reasoning": "教材证据足够"},
            "applicant_opinion": {"type": "logic_dispute", "reasoning": "缺乏教材证据", "core_conflict": "是否公知"},
            "assessment": {"verdict": "EXAMINER_CORRECT", "reasoning": "教材明确记载为常规技术手段", "confidence": 0.84, "examiner_rejection_rationale": ""},
            "evidence": [{"doc_id": "EXT2", "quote": "教材明确记载该技术手段属于常规技术手段。"}],
            "trace": {},
        }

    monkeypatch.setattr(
        node,
        "_build_engine_queries",
        lambda *args, **kwargs: {
            "openalex": ["locking structure common knowledge"],
            "zhihuiya": ["锁定结构 公知常识"],
            "tavily": ["锁定结构 常规手段"],
        },
    )
    monkeypatch.setattr(
        node,
        "_build_followup_engine_queries",
        lambda *args, **kwargs: {
            "openalex": ["locking structure textbook handbook"],
            "zhihuiya": ["锁定结构 教材 手册 公知常识"],
            "tavily": ["锁定结构 教材 手册"],
        },
    )
    monkeypatch.setattr(node.external_evidence_aggregator, "search_evidence", _fake_search_evidence)
    monkeypatch.setattr(node, "_search_local_candidates", _fake_search_local_candidates)
    monkeypatch.setattr(node, "_verify_single_dispute", _fake_verify_single_dispute)

    result = node._evaluate_common_knowledge_dispute(
        dispute={
            "dispute_id": "DSP_1",
            "claim_ids": ["1"],
            "feature_text": "移动定位架的锁定结构",
            "examiner_opinion": {"type": "common_knowledge_based", "reasoning": "本领域常规手段"},
            "applicant_opinion": {"type": "logic_dispute", "reasoning": "缺乏证据", "core_conflict": "是否公知"},
        },
        claims=[{"claim_id": "1", "claim_text": "一种定位装置。"}],
        priority_date="2020-01-01",
        local_retriever=None,
        local_doc_ids=[],
    )

    assert len(external_calls) == 2
    assert len(local_calls) == 2
    assert len(verify_calls) == 2
    assert result["assessment"]["verdict"] == "EXAMINER_CORRECT"
    assert result["trace"]["followup_retrieval"] != {}
