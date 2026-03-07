from __future__ import annotations

import json
from typing import Any, Dict, List

from loguru import logger

from agents.common.utils.llm import get_llm_service
from config import Settings


class QueryRewriteService:
    """Generic retrieval query rewrite service."""

    def __init__(self, llm_service: Any = None):
        self.llm_service = llm_service or get_llm_service()

    def rewrite(self, task_intent: str, raw_query: str) -> Dict[str, Any]:
        query = " ".join(str(raw_query or "").split()).strip()
        if not query:
            return {"query": "", "alt_queries": [], "rewritten": False}

        system_prompt = """
你是语义检索查询改写器。请将输入问题改写为更适合向量检索的高召回查询。

要求：
1. 保留原始意图，不得引入无关推断。
2. 输出 1 条主查询 query 和最多 2 条补充查询 alt_queries。
3. 查询应覆盖：对象、关键机制、约束条件。
4. 不依赖固定术语词表，适配不同技术领域与文体。
5. 仅输出 JSON。

输出格式：
{
  "query": "主查询",
  "alt_queries": ["补充查询1", "补充查询2"]
}
"""

        user_prompt = json.dumps(
            {
                "task_intent": str(task_intent or "").strip(),
                "raw_query": query,
            },
            ensure_ascii=False,
        )

        try:
            response = self.llm_service.chat_completion_json(
                model=Settings.LLM_MODEL_REASONING,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                thinking=True,
            )
            data = response if isinstance(response, dict) else {}
            rewritten = self._normalize_query(data.get("query", ""))
            alt_queries = self._normalize_query_list(data.get("alt_queries", []), limit=2)

            if not rewritten:
                return {"query": query, "alt_queries": [], "rewritten": False}

            alt_queries = [item for item in alt_queries if item != rewritten and item != query]
            return {"query": rewritten, "alt_queries": alt_queries, "rewritten": True}
        except Exception as ex:
            logger.warning(f"[QueryRewrite] rewrite failed, fallback to raw query: {ex}")
            return {"query": query, "alt_queries": [], "rewritten": False}

    def _normalize_query(self, value: Any) -> str:
        text = " ".join(str(value or "").split()).strip()
        return text[:360]

    def _normalize_query_list(self, values: Any, limit: int = 2) -> List[str]:
        items: List[str] = []
        if not isinstance(values, list):
            return items
        for value in values:
            text = self._normalize_query(value)
            if not text or text in items:
                continue
            items.append(text)
            if len(items) >= limit:
                break
        return items
