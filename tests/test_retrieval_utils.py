from agents.ai_reply.src.retrieval_utils import (
    ENGINE_HINTS,
    extract_must_keep_phrases,
    make_query_spec,
    normalize_query_specs,
    plan_engine_queries,
)


class _FakeLLM:
    def __init__(self, response):
        self.response = response
        self.messages = []

    def invoke_text_json(self, messages, task_kind, temperature):
        self.messages.append(messages)
        return self.response


def test_plan_engine_queries_returns_structured_specs():
    llm = _FakeLLM(
        {
            "openalex": [
                {
                    "text": "\"large language models\" AND \"patent claims\" AND generation",
                    "mode": "boolean",
                    "intent": "anchor",
                },
                {
                    "text": "\"large language models\" AND patent drafting review",
                    "mode": "boolean",
                    "intent": "expansion",
                },
            ],
            "zhihuiya": [
                {
                    "text": "\"专利权利要求\" AND 生成 AND 撰写",
                    "mode": "lexical",
                    "intent": "core_patent",
                },
                {
                    "text": "专利权利要求 自动生成 撰写",
                    "mode": "semantic",
                    "intent": "expansion",
                },
            ],
            "tavily": [
                {
                    "text": "大语言模型 专利权利要求 教材 手册 标准 PDF 高校",
                    "mode": "web",
                    "intent": "reference",
                },
                {
                    "text": "大语言模型 专利权利要求 技术公开 实现方案 白皮书 论文 产品文档",
                    "mode": "web",
                    "intent": "technical",
                },
            ],
        }
    )

    queries = plan_engine_queries(
        llm_service=llm,
        user_context={
            "retrieval_goal": "topup_search",
            "engine_hints": dict(ENGINE_HINTS),
            "must_keep_phrases": ["large language models", "patent claims"],
            "feature_text": "large language model patent claim generation",
            "claim_text": "Use large language models to draft patent claims.",
        },
        fallback_queries={},
        scenario="补充检索核查",
        per_engine_limit=2,
    )

    assert queries["openalex"][0]["intent"] == "anchor"
    assert "large language models" in queries["openalex"][0]["text"]
    assert queries["zhihuiya"][0]["mode"] == "lexical"
    assert queries["tavily"][0]["intent"] in {"reference", "technical"}


def test_plan_engine_queries_falls_back_when_llm_shape_invalid():
    llm = _FakeLLM({"openalex": ["bad-shape"]})
    fallback = {
        "openalex": [make_query_spec("\"large language models\" AND \"patent claims\"", "boolean", "anchor")],
        "zhihuiya": [make_query_spec("\"专利权利要求\" AND 生成", "lexical", "core_patent")],
        "tavily": [make_query_spec("专利权利要求 教材 手册 标准 PDF", "web", "reference")],
    }

    queries = plan_engine_queries(
        llm_service=llm,
        user_context={"feature_text": "x", "claim_text": "y"},
        fallback_queries=fallback,
        scenario="补充检索核查",
        per_engine_limit=2,
    )

    assert queries == {
        "openalex": normalize_query_specs(fallback["openalex"], engine="openalex", limit=2),
        "zhihuiya": normalize_query_specs(fallback["zhihuiya"], engine="zhihuiya", limit=2),
        "tavily": normalize_query_specs(fallback["tavily"], engine="tavily", limit=2),
    }


def test_extract_must_keep_phrases_keeps_acronyms_and_quoted_phrases():
    phrases = extract_must_keep_phrases(
        feature_text="基于轮胎的RRC值控制车辆的目标加速度",
        claim_text='权利要求1: "large language models" 用于专利权利要求生成',
        primary_queries={
            "openalex": [make_query_spec('"large language models" AND "patent claims"', "boolean", "anchor")],
            "zhihuiya": [],
            "tavily": [],
        },
    )

    assert "RRC" in phrases
    assert "large language models" in phrases
