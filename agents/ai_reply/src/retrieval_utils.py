"""
外部检索公共工具
复用多引擎查询条件生成与 trace 组装逻辑。
"""

import json
from typing import Any, Dict, List

from loguru import logger


_ENGINE_ALIASES = {
    "openalex": "openalex",
    "academic": "openalex",
    "scholar": "openalex",
    "zhihuiya": "zhihuiya",
    "patent": "zhihuiya",
    "tavily": "tavily",
    "web": "tavily",
}


def normalize_query_list(values: List[Any], limit: int = 2) -> List[str]:
    normalized: List[str] = []
    for value in values or []:
        text = " ".join(str(value).split())
        if not text or text in normalized:
            continue
        normalized.append(text)
        if len(normalized) >= limit:
            break
    return normalized


def normalize_engine_queries(raw: Dict[str, Any], limit: int = 2) -> Dict[str, List[str]]:
    engine_queries: Dict[str, List[str]] = {
        "openalex": [],
        "zhihuiya": [],
        "tavily": [],
    }
    for key, value in (raw or {}).items():
        engine = _ENGINE_ALIASES.get(str(key).strip().lower())
        if not engine:
            continue
        if isinstance(value, list):
            engine_queries[engine] = normalize_query_list(value, limit=limit)
    return engine_queries


def plan_engine_queries(
    llm_service: Any,
    user_context: Dict[str, Any],
    fallback_queries: Dict[str, List[str]],
    scenario: str,
    per_engine_limit: int = 2,
) -> Dict[str, List[str]]:
    messages = [
        {
            "role": "system",
            "content": (
                f"你是专利检索策略专家，当前场景：{scenario}。"
                "请按引擎输出 JSON 查询条件，格式为 "
                "{\"openalex\": [\"...\"], \"zhihuiya\": [\"...\"], \"tavily\": [\"...\"]}。"
                f"每个引擎最多 {per_engine_limit} 条。"
                "openalex 使用英文学术风格关键词；zhihuiya/tavily 使用中文关键词。"
                "只输出 JSON，不要额外文本。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(user_context, ensure_ascii=False),
        },
    ]
    try:
        response = llm_service.invoke_text_json(
            messages=messages,
            task_kind="retrieval_query_planning",
            temperature=0.1,
        )
        parsed = _to_dict(response)
        normalized = normalize_engine_queries(parsed, limit=per_engine_limit)
        if any(normalized.values()):
            return normalized
    except Exception as ex:
        logger.warning(f"LLM 生成检索条件失败，将使用规则兜底: {ex}")

    return normalize_engine_queries(fallback_queries, limit=per_engine_limit)


def build_trace_retrieval(
    queries_by_engine: Dict[str, List[str]],
    retrieval_engines: List[str],
    retrieval_meta: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    retrieval = _to_dict(retrieval_meta).get("retrieval", {})
    if isinstance(retrieval, dict) and retrieval:
        return retrieval

    fallback: Dict[str, Dict[str, Any]] = {}
    for engine in retrieval_engines:
        fallback[engine] = {
            "queries": queries_by_engine.get(engine, []),
            "filters": {},
            "result_count": 0,
            "results": [],
        }
    return fallback


def _to_dict(item: Any) -> Dict[str, Any]:
    if isinstance(item, dict):
        return item
    if hasattr(item, "model_dump"):
        return item.model_dump()
    if hasattr(item, "dict"):
        return item.dict()
    return {}
