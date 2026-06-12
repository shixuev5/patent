"""Conversational AI search runtime built on the OpenAI Agents SDK."""

from __future__ import annotations

import inspect
import asyncio
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from agents import (
    Agent,
    AsyncOpenAI,
    ModelSettings,
    OpenAIChatCompletionsModel,
    RunContextWrapper,
    Runner,
    function_tool,
    set_tracing_disabled,
)

from config import settings
from patent_agents.ai_search.src.ids import (
    build_ai_search_canonical_id,
    stable_ai_search_document_id,
)
from patent_agents.ai_search.src.state import (
    PHASE_RUNNING,
    get_ai_search_meta,
    merge_ai_search_meta,
    phase_progress,
    phase_step,
    phase_to_task_status,
)
from patent_agents.ai_search.src.time_utils import parse_storage_ts, utc_now, utc_now_z
from patent_agents.common.retrieval.academic_search import AcademicSearchClient
from patent_agents.common.search_clients.factory import SearchClientFactory


DEFAULT_STOP_POLICY: Dict[str, Any] = {
    "max_rounds": 5,
    "max_queries": 30,
    "max_candidates": 200,
    "max_selected_documents": 8,
    "max_no_new_result_rounds": 2,
    "deadline_seconds": 600,
    "target_coverage": "",
    "stop_when": "",
    "databases": ["zhihuiya", "openalex", "semanticscholar", "crossref"],
}


SYSTEM_PROMPT = """
你是一个自由对话式专利/文献检索 agent。

你的工作方式接近 Codex/Claude Code：直接阅读会话上下文、调用工具、检索、保存候选、抽取证据、比较特征、生成阶段性结论。不要把自己当作状态机调度器，也不要暴露内部工具调用细节。

你可以和用户自然对话。用户可以只聊天、直接要求检索、基于已有 AI 分析/答复上下文检索，或随时调整检索停止条件。你必须尊重用户给出的停止条件，同时工具层会强制执行硬限制。

每次正式检索前先确认当前检索目标。如果目标足够明确，直接检索；如果缺少核心技术特征、时间范围或检索对象，先简短追问。检索后应把有价值候选保存，并说明下一步建议。

你仍然负责检索策略和是否继续检索的最终判断。需要批量召回或补检时，调用 retrieval-agent 工具，让它执行语义/布尔/学术多路召回并只返回压缩态势；当检索目标能拆成多个互不依赖的方向时，可以并行发起多个 retrieval-agent 子 Agent。需要候选粗筛或少量精读时，调用 review-agent 工具。用户明确指定公开号、或你需要补齐少量专利详情时，调用 detail-agent 工具。

不要要求工具返回完整候选池；需要更多候选信息时按 topK 或阶段读取候选摘要。

输出要求：
- 面向用户只输出简洁自然语言。
- 不输出 JSON、工具名、trace、内部字段。
- 涉及文献判断时说明依据：标题、摘要、关键段落或权利要求命中点。
- 如果停止条件已满足，明确说明停止原因和当前结果是否足够。
""".strip()


RETRIEVAL_AGENT_SUMMARY_PROMPT = """
你是 retrieval-agent，负责把一轮多路检索结果压缩成主 agent 可用的态势摘要。

输入是已经执行完成的检索批次报告。请只输出简洁中文摘要：
- 本轮覆盖了哪些检索方向。
- 新增候选数量、重复/跳过情况。
- 最值得主 agent 继续关注的 3-5 个候选及理由。
- 是否建议继续扩展检索，以及建议方向。

不要输出原始 JSON，不要复述完整摘要列表。
""".strip()


REVIEW_AGENT_SUMMARY_PROMPT = """
你是 review-agent，负责把候选粗筛/精读结果压缩成主 agent 可用的审阅摘要。

输入是候选评分、少量详情片段和更新结果。请只输出简洁中文摘要：
- 哪些候选值得保留或优先精读。
- 依据来自标题、摘要、权利要求或说明书的哪些命中点。
- 哪些候选目前证据不足。
- 下一步是否需要继续检索或拉更多详情。

不要输出原始 JSON，不要做最终法律结论。
""".strip()


def _compact_trace_value(value: Any, *, max_string: int = 1200, max_items: int = 8, depth: int = 3) -> Any:
    if value is None:
        return None
    if depth <= 0:
        text = " ".join(str(value).split()).strip()
        return text[:max_string] + ("..." if len(text) > max_string else "")
    if isinstance(value, str):
        text = " ".join(value.split()).strip()
        return text[:max_string] + ("..." if len(text) > max_string else "")
    if isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, dict):
        items = list(value.items())[:max_items]
        compacted = {
            str(key): _compact_trace_value(item, max_string=max_string, max_items=max_items, depth=depth - 1)
            for key, item in items
        }
        if len(value) > max_items:
            compacted["_truncated"] = f"省略 {len(value) - max_items} 个字段"
        return compacted
    if isinstance(value, (list, tuple)):
        compacted_items = [
            _compact_trace_value(item, max_string=max_string, max_items=max_items, depth=depth - 1)
            for item in list(value)[:max_items]
        ]
        if len(value) > max_items:
            compacted_items.append({"_truncated": f"省略 {len(value) - max_items} 项"})
        return compacted_items
    text = " ".join(str(value).split()).strip()
    return text[:max_string] + ("..." if len(text) > max_string else "")


@dataclass
class AiSearchRuntimeContext:
    storage: Any
    task_id: str
    run_id: str
    plan_version: int = 1

    def task(self) -> Any:
        return self.storage.get_task(self.task_id)

    def meta(self) -> Dict[str, Any]:
        return get_ai_search_meta(self.task())

    def stop_policy(self) -> Dict[str, Any]:
        policy = self.meta().get("stop_policy")
        return normalize_stop_policy(policy if isinstance(policy, dict) else {})

    def update_meta(self, **updates: Any) -> None:
        task = self.task()
        self.storage.update_task(task.id, metadata=merge_ai_search_meta(task, **updates))

    def append_event(self, event_type: str, payload: Dict[str, Any], *, entity_id: str = "") -> Optional[Dict[str, Any]]:
        return self.storage.append_ai_search_stream_event(
            {
                "event_id": uuid.uuid4().hex,
                "session_id": self.task_id,
                "task_id": self.task_id,
                "run_id": self.run_id,
                "event_type": event_type,
                "entity_id": entity_id or None,
                "payload": payload,
            }
        )

    def start_trace(
        self,
        *,
        tool_name: str,
        label: str,
        detail: str = "",
        trace_type: str = "tool",
        actor_name: str = "ai-search-agent",
        input: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
        parent_trace_id: str = "",
    ) -> Tuple[str, str]:
        trace_id = uuid.uuid4().hex
        started_at = utc_now_z()
        payload: Dict[str, Any] = {
            "traceId": trace_id,
            "traceType": trace_type,
            "status": "running",
            "label": label,
            "detail": detail,
            "actorName": actor_name,
            "toolName": tool_name,
            "phase": PHASE_RUNNING,
            "startedAt": started_at,
        }
        if parent_trace_id:
            payload["parentTraceId"] = parent_trace_id
        if input is not None:
            payload["input"] = _compact_trace_value(input)
            payload["arguments"] = payload["input"]
        if metadata:
            payload["metadata"] = _compact_trace_value(metadata)
        self.append_event("trace.started", payload, entity_id=trace_id)
        return trace_id, started_at

    def finish_trace(
        self,
        trace: Tuple[str, str],
        *,
        tool_name: str,
        label: str,
        detail: str = "",
        status: str = "completed",
        trace_type: str = "tool",
        actor_name: str = "ai-search-agent",
        output: Any = None,
        result: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
        parent_trace_id: str = "",
    ) -> None:
        trace_id, started_at = trace
        payload: Dict[str, Any] = {
            "traceId": trace_id,
            "traceType": trace_type,
            "status": status,
            "label": label,
            "detail": detail,
            "actorName": actor_name,
            "toolName": tool_name,
            "phase": PHASE_RUNNING,
            "startedAt": started_at,
            "endedAt": utc_now_z(),
        }
        if parent_trace_id:
            payload["parentTraceId"] = parent_trace_id
        if output is not None:
            payload["output"] = _compact_trace_value(output)
        if result is not None:
            payload["result"] = _compact_trace_value(result)
        if metadata:
            payload["metadata"] = _compact_trace_value(metadata)
        self.append_event("trace.completed", payload, entity_id=trace_id)

    def documents(self) -> List[Dict[str, Any]]:
        return self.storage.list_ai_search_documents(self.task_id, self.plan_version)

    def selected_documents(self) -> List[Dict[str, Any]]:
        return self.storage.list_ai_search_documents(self.task_id, self.plan_version, stages=["selected"])


def _safe_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_patent_number(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text.startswith("PATENT:"):
        text = text.split(":", 1)[1]
    return re.sub(r"[^A-Z0-9]", "", text)


def _extract_patent_numbers(value: Any) -> List[str]:
    text = str(value or "").upper()
    if not text:
        return []
    matches = re.findall(r"\b[A-Z]{2}\s*\d{5,}[A-Z]\d?\b", text)
    return [_normalize_patent_number(item) for item in matches if _normalize_patent_number(item)]


def _target_patent_numbers(ctx: AiSearchRuntimeContext) -> set[str]:
    meta = ctx.meta()
    candidates: List[Any] = [
        meta.get("source_pn"),
        meta.get("source_title"),
        meta.get("target_pn"),
        getattr(ctx.task(), "pn", None),
    ]
    numbers: set[str] = set()
    for value in candidates:
        normalized = _normalize_patent_number(value)
        if normalized:
            numbers.add(normalized)
        numbers.update(_extract_patent_numbers(value))
    return numbers


def _is_target_patent(ctx: AiSearchRuntimeContext, item: Dict[str, Any]) -> bool:
    targets = _target_patent_numbers(ctx)
    if not targets:
        return False
    identifiers = [
        item.get("pn"),
        item.get("external_id"),
        item.get("canonical_id"),
    ]
    return any(_normalize_patent_number(value) in targets for value in identifiers if _normalize_patent_number(value))


def _ensure_policy_databases(ctx: AiSearchRuntimeContext, sources: List[str]) -> None:
    normalized_sources = [item for item in _source_list(sources) if item]
    if not normalized_sources:
        return
    current = ctx.stop_policy()
    databases = _source_list(current.get("databases"))
    changed = False
    for source in normalized_sources:
        if source not in databases:
            databases.append(source)
            changed = True
    if changed:
        ctx.update_meta(stop_policy=normalize_stop_policy({"databases": databases}, current_policy=current))


def _limit(value: int, *, default: int, upper: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(1, min(parsed or default, upper))


def normalize_stop_policy(
    raw_policy: Optional[Dict[str, Any]] = None,
    *,
    current_policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge user-facing stop-policy input with bounded defaults."""
    merged = dict(DEFAULT_STOP_POLICY)
    if isinstance(current_policy, dict):
        merged.update({key: value for key, value in current_policy.items() if value not in (None, "")})
    if isinstance(raw_policy, dict):
        merged.update({key: value for key, value in raw_policy.items() if value not in (None, "")})

    bounds = {
        "max_rounds": (1, 30),
        "max_queries": (1, 200),
        "max_candidates": (1, 1000),
        "max_selected_documents": (1, 50),
        "max_no_new_result_rounds": (1, 20),
        "deadline_seconds": (30, 7200),
    }
    for key, (lower, upper) in bounds.items():
        try:
            value = int(merged.get(key) or DEFAULT_STOP_POLICY[key])
        except Exception:
            value = int(DEFAULT_STOP_POLICY[key])
        merged[key] = max(lower, min(value, upper))

    merged["target_coverage"] = _safe_text(merged.get("target_coverage"))
    merged["stop_when"] = _safe_text(merged.get("stop_when"))
    databases = _source_list(merged.get("databases")) or list(DEFAULT_STOP_POLICY["databases"])
    allowed_databases = {"zhihuiya", "openalex", "semanticscholar", "crossref"}
    merged["databases"] = [item for item in databases if item in allowed_databases] or list(DEFAULT_STOP_POLICY["databases"])
    return merged


def _source_list(value: Any) -> List[str]:
    if isinstance(value, str):
        return [item.strip().lower() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item or "").strip().lower() for item in value if str(item or "").strip()]
    return []


def _normalize_patent_item(item: Dict[str, Any]) -> Dict[str, Any]:
    pn = _safe_text(item.get("pn") or item.get("PN") or item.get("publication_number")).upper()
    source_type = "patent"
    canonical_id = build_ai_search_canonical_id(source_type=source_type, external_id=pn, pn=pn)
    return {
        "source_type": source_type,
        "external_id": pn,
        "canonical_id": canonical_id,
        "pn": pn,
        "doi": "",
        "url": _safe_text(item.get("url")) or (f"https://patents.google.com/patent/{pn}" if pn else ""),
        "title": _safe_text(item.get("title") or item.get("TITLE")),
        "abstract": _safe_text(item.get("abstract") or item.get("ABST")),
        "venue": "",
        "language": _safe_text(item.get("language")),
        "publication_date": _safe_text(item.get("publication_date") or item.get("PBD")),
        "application_date": _safe_text(item.get("application_date") or item.get("APD")),
        "primary_ipc": _safe_text((item.get("ipc") or [""])[0] if isinstance(item.get("ipc"), list) and item.get("ipc") else item.get("primary_ipc")),
        "ipc_cpc_json": item.get("ipc") if isinstance(item.get("ipc"), list) else [],
        "score": item.get("score"),
    }


def _normalize_academic_item(item: Dict[str, Any], source_type: str) -> Dict[str, Any]:
    doi = _safe_text(item.get("doi") or item.get("DOI")).lower()
    external_id = _safe_text(item.get("external_id") or item.get("paperId") or item.get("id") or item.get("url"))
    canonical_id = build_ai_search_canonical_id(source_type=source_type, external_id=external_id, doi=doi)
    return {
        "source_type": source_type,
        "external_id": external_id,
        "canonical_id": canonical_id,
        "pn": "",
        "doi": doi,
        "url": _safe_text(item.get("url") or item.get("URL")),
        "title": _safe_text(item.get("title")),
        "abstract": _safe_text(item.get("abstract")),
        "venue": _safe_text(item.get("venue")),
        "language": _safe_text(item.get("language")),
        "publication_date": _safe_text(item.get("publication_date") or item.get("year")),
        "application_date": "",
        "primary_ipc": "",
        "ipc_cpc_json": [],
        "score": item.get("score"),
    }


def _patent_items_from_response(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, list):
        candidates = raw
    elif isinstance(raw, dict):
        candidates = raw.get("items")
        if not isinstance(candidates, list):
            candidates = raw.get("results")
        if not isinstance(candidates, list):
            data = raw.get("data")
            candidates = data.get("results") if isinstance(data, dict) else []
    else:
        candidates = []
    return [item for item in candidates if isinstance(item, dict)]


def _document_trace_preview(items: List[Dict[str, Any]], *, limit: int = 5) -> List[Dict[str, Any]]:
    return [
        {
            "id": item.get("pn") or item.get("doi") or item.get("external_id") or item.get("canonical_id"),
            "title": item.get("title"),
            "source_type": item.get("source_type"),
            "publication_date": item.get("publication_date"),
        }
        for item in items[:limit]
    ]


def _document_identity(item: Dict[str, Any]) -> str:
    return _safe_text(item.get("pn") or item.get("doi") or item.get("external_id") or item.get("canonical_id"))


def _compact_document_summary(
    item: Dict[str, Any],
    *,
    include_abstract: bool = False,
    max_abstract_chars: int = 240,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "document_id": item.get("document_id"),
        "id": _document_identity(item),
        "source_type": item.get("source_type"),
        "pn": item.get("pn"),
        "doi": item.get("doi"),
        "title": item.get("title"),
        "publication_date": item.get("publication_date"),
        "application_date": item.get("application_date"),
        "primary_ipc": item.get("primary_ipc"),
        "score": item.get("score"),
        "stage": item.get("stage"),
        "coarse_status": item.get("coarse_status"),
        "close_read_status": item.get("close_read_status"),
    }
    reason = (
        _safe_text(item.get("close_read_reason"))
        or _safe_text(item.get("coarse_reason"))
        or _safe_text(item.get("agent_reason"))
    )
    if reason:
        payload["reason"] = reason[:300]
    if include_abstract:
        abstract = _safe_text(item.get("abstract"))
        if abstract:
            payload["abstract_preview"] = abstract[:max_abstract_chars]
    return {key: value for key, value in payload.items() if value not in (None, "", [])}


def _stored_document_summaries(
    ctx: AiSearchRuntimeContext,
    raw_items: List[Dict[str, Any]],
    *,
    limit: int = 5,
    include_abstract: bool = False,
) -> List[Dict[str, Any]]:
    order = [
        _safe_text(item.get("canonical_id"))
        for item in raw_items
        if _safe_text(item.get("canonical_id"))
    ]
    if not order:
        return []
    order_index = {canonical_id: index for index, canonical_id in enumerate(order)}
    docs = [
        item
        for item in ctx.documents()
        if _safe_text(item.get("canonical_id")) in order_index
    ]
    docs.sort(key=lambda item: order_index.get(_safe_text(item.get("canonical_id")), 999999))
    return [
        _compact_document_summary(item, include_abstract=include_abstract)
        for item in docs[:limit]
    ]


def _candidate_summaries(
    ctx: AiSearchRuntimeContext,
    *,
    stage: str = "",
    top_k: int = 10,
    include_abstract: bool = False,
) -> List[Dict[str, Any]]:
    wanted_stage = _safe_text(stage).lower()
    docs = ctx.documents()
    if wanted_stage:
        docs = [item for item in docs if _safe_text(item.get("stage")).lower() == wanted_stage]
    else:
        docs = [item for item in docs if _safe_text(item.get("stage")).lower() != "rejected"]
    return [
        _compact_document_summary(item, include_abstract=include_abstract)
        for item in docs[: _limit(top_k, default=10, upper=50)]
    ]


def _parse_query_list(value: Any, *, limit: int = 6) -> List[str]:
    if isinstance(value, list):
        raw_items = value
    else:
        text = str(value or "").strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            raw_items = parsed if isinstance(parsed, list) else [text]
        except Exception:
            raw_items = re.split(r"[\n；;]+", text)
    queries: List[str] = []
    seen: set[str] = set()
    for item in raw_items:
        query = _safe_text(item)
        if not query or query in seen:
            continue
        seen.add(query)
        queries.append(query)
        if len(queries) >= limit:
            break
    return queries


def _build_openai_client() -> AsyncOpenAI:
    client_kwargs: Dict[str, Any] = {"api_key": settings.LLM_API_KEY}
    if str(settings.LLM_BASE_URL or "").strip():
        client_kwargs["base_url"] = settings.LLM_BASE_URL
    return AsyncOpenAI(**client_kwargs)


def _default_model_name() -> str:
    return str(settings.LLM_MODEL_DEFAULT or settings.LLM_MODEL_LARGE or "").strip()


async def _run_default_child_summary(agent_name: str, instructions: str, payload: Dict[str, Any]) -> str:
    model_name = _default_model_name()
    if not settings.LLM_API_KEY or not model_name:
        return ""
    set_tracing_disabled(True)
    agent: Agent[Any] = Agent(
        name=agent_name,
        instructions=instructions,
        model=OpenAIChatCompletionsModel(model=model_name, openai_client=_build_openai_client()),
        model_settings=ModelSettings(temperature=0, parallel_tool_calls=False),
    )
    result = await Runner.run(
        agent,
        json.dumps(_compact_trace_value(payload, max_string=1800, max_items=12, depth=4), ensure_ascii=False),
        max_turns=2,
    )
    return str(result.final_output or "").strip()


def _upsert_candidates(ctx: AiSearchRuntimeContext, raw_items: List[Dict[str, Any]], *, source_label: str, query: str) -> Dict[str, Any]:
    existing = {
        str(item.get("canonical_id") or "").strip(): item
        for item in ctx.documents()
        if str(item.get("canonical_id") or "").strip()
    }
    records: List[Dict[str, Any]] = []
    new_count = 0
    skipped_target = 0
    now = utc_now_z()
    for item in raw_items:
        if str(item.get("source_type") or "").strip().lower() == "patent" and _is_target_patent(ctx, item):
            skipped_target += 1
            continue
        canonical_id = _safe_text(item.get("canonical_id"))
        if not canonical_id:
            continue
        is_new = canonical_id not in existing
        if is_new:
            new_count += 1
        document_id = stable_ai_search_document_id(
            ctx.task_id,
            ctx.plan_version,
            canonical_id,
            fallback_seed=json.dumps(item, ensure_ascii=False, sort_keys=True),
        )
        records.append(
            {
                **item,
                "run_id": ctx.run_id,
                "task_id": ctx.task_id,
                "plan_version": ctx.plan_version,
                "document_id": document_id,
                "stage": "candidate" if not existing.get(canonical_id, {}).get("stage") else existing[canonical_id]["stage"],
                "source_batches_json": [source_label],
                "source_lanes_json": [query],
                "agent_reason": f"由检索式 `{query}` 召回。",
                "created_at": now,
                "updated_at": now,
            }
        )
    changed = ctx.storage.upsert_ai_search_documents(records)
    selected_count = len(ctx.selected_documents())
    ctx.update_meta(selected_document_count=selected_count)
    payload = documents_payload(ctx)
    ctx.append_event("documents.updated", payload)
    return {"stored": changed, "new": new_count, "total": len(ctx.documents()), "skipped_target": skipped_target}


def documents_payload(ctx: AiSearchRuntimeContext) -> Dict[str, Any]:
    documents = ctx.documents()
    candidates = [item for item in documents if str(item.get("stage") or "") not in {"selected", "rejected"}]
    selected = [item for item in documents if str(item.get("stage") or "") == "selected"]
    return {"candidates": candidates, "selected": selected}


def _record_query(ctx: AiSearchRuntimeContext, *, query_count: int = 1, new_count: int = 0) -> None:
    meta = ctx.meta()
    total_queries = int(meta.get("query_count") or 0) + max(int(query_count or 0), 0)
    search_rounds = int(meta.get("search_rounds") or 0) + 1
    no_new_rounds = 0 if new_count > 0 else int(meta.get("no_new_result_rounds") or 0) + 1
    ctx.update_meta(query_count=total_queries, search_rounds=search_rounds, no_new_result_rounds=no_new_rounds)


def _stop_status(ctx: AiSearchRuntimeContext) -> Dict[str, Any]:
    policy = ctx.stop_policy()
    meta = ctx.meta()
    documents = ctx.documents()
    selected = ctx.selected_documents()
    reasons: List[str] = []
    if int(meta.get("search_rounds") or 0) >= int(policy.get("max_rounds") or 0):
        reasons.append("达到最大检索轮次")
    if int(meta.get("query_count") or 0) >= int(policy.get("max_queries") or 0):
        reasons.append("达到最大检索式数量")
    if len(documents) >= int(policy.get("max_candidates") or 0):
        reasons.append("达到最大候选数量")
    if len(selected) >= int(policy.get("max_selected_documents") or 0):
        reasons.append("达到最大选中文献数量")
    if int(meta.get("no_new_result_rounds") or 0) >= int(policy.get("max_no_new_result_rounds") or 0):
        reasons.append("连续多轮无新增候选")
    run = ctx.storage.get_ai_search_run(ctx.task_id, ctx.run_id) if ctx.run_id else None
    started_at = str((run or {}).get("created_at") or "").strip()
    started = parse_storage_ts(started_at, naive_strategy="utc") if started_at else None
    elapsed_seconds = int(max(0, (utc_now() - started).total_seconds())) if started else 0
    if elapsed_seconds and elapsed_seconds >= int(policy.get("deadline_seconds") or 0):
        reasons.append("达到检索时间上限")
    return {
        "should_stop": bool(reasons),
        "reasons": reasons,
        "policy": policy,
        "stats": {
            "rounds": int(meta.get("search_rounds") or 0),
            "queries": int(meta.get("query_count") or 0),
            "candidates": len(documents),
            "selected": len(selected),
            "no_new_result_rounds": int(meta.get("no_new_result_rounds") or 0),
            "elapsed_seconds": elapsed_seconds,
        },
    }


def _remaining_candidate_capacity(ctx: AiSearchRuntimeContext) -> int:
    policy = ctx.stop_policy()
    max_candidates = int(policy.get("max_candidates") or DEFAULT_STOP_POLICY["max_candidates"])
    return max(0, max_candidates - len(ctx.documents()))


@function_tool
def read_workspace_context(ctx: RunContextWrapper[AiSearchRuntimeContext]) -> Dict[str, Any]:
    """读取当前会话、停止条件、历史消息、候选/已选文献的压缩摘要。"""
    runtime = ctx.context
    trace = runtime.start_trace(tool_name="read_workspace_context", label="读取会话上下文")
    messages = runtime.storage.list_ai_search_messages(runtime.task_id)[-12:]
    meta = runtime.meta()
    documents = runtime.documents()
    candidates = [item for item in documents if str(item.get("stage") or "") not in {"selected", "rejected"}]
    selected = runtime.selected_documents()
    result = {
        "stop_policy": runtime.stop_policy(),
        "stats": _stop_status(runtime)["stats"],
        "source": {
            "source_type": meta.get("source_type"),
            "source_task_id": meta.get("source_task_id"),
            "source_pn": meta.get("source_pn"),
            "source_title": meta.get("source_title"),
        },
        "recent_messages": [
            {
                "role": item.get("role"),
                "kind": item.get("kind"),
                "content": item.get("content"),
            }
            for item in messages
            if str(item.get("kind") or "") in {"chat", "answer"}
        ],
        "candidate_total": len(candidates),
        "selected_total": len(selected),
        "candidate_documents": [
            _compact_document_summary(item, include_abstract=False)
            for item in candidates[:12]
        ],
        "selected_documents": [
            _compact_document_summary(item, include_abstract=True, max_abstract_chars=180)
            for item in selected[:12]
        ],
    }
    runtime.finish_trace(
        trace,
        tool_name="read_workspace_context",
        label="会话上下文已读取",
        output={
            "messages": len(result["recent_messages"]),
            "candidates": result["candidate_total"],
            "selected": result["selected_total"],
            "stop_policy": result["stop_policy"],
            "source": result["source"],
        },
    )
    return result


@function_tool
def read_candidate_summaries(
    ctx: RunContextWrapper[AiSearchRuntimeContext],
    stage: str = "",
    top_k: int = 10,
    include_abstract: bool = False,
) -> Dict[str, Any]:
    """按阶段/topK 读取候选压缩摘要；默认不返回完整摘要。"""
    runtime = ctx.context
    resolved_top_k = _limit(top_k, default=10, upper=50)
    trace = runtime.start_trace(
        tool_name="read_candidate_summaries",
        label=f"读取候选摘要 {resolved_top_k} 条",
        input={"stage": stage, "top_k": resolved_top_k, "include_abstract": include_abstract},
    )
    documents = _candidate_summaries(
        runtime,
        stage=stage,
        top_k=resolved_top_k,
        include_abstract=bool(include_abstract),
    )
    result = {
        "stage": _safe_text(stage) or "all",
        "count": len(documents),
        "documents": documents,
        "stats": _stop_status(runtime)["stats"],
    }
    runtime.finish_trace(
        trace,
        tool_name="read_candidate_summaries",
        label=f"候选摘要已读取 {len(documents)} 条",
        output={"count": len(documents), "stage": result["stage"]},
    )
    return result


@function_tool
def set_stop_conditions(
    ctx: RunContextWrapper[AiSearchRuntimeContext],
    max_rounds: int = 0,
    max_queries: int = 0,
    max_candidates: int = 0,
    max_selected_documents: int = 0,
    max_no_new_result_rounds: int = 0,
    deadline_seconds: int = 0,
    target_coverage: str = "",
    stop_when: str = "",
    databases: str = "",
) -> Dict[str, Any]:
    """设置本会话的检索停止条件。"""
    runtime = ctx.context
    current = runtime.stop_policy()
    updates: Dict[str, Any] = {}
    for key, value in {
        "max_rounds": max_rounds,
        "max_queries": max_queries,
        "max_candidates": max_candidates,
        "max_selected_documents": max_selected_documents,
        "max_no_new_result_rounds": max_no_new_result_rounds,
        "deadline_seconds": deadline_seconds,
    }.items():
        if int(value or 0) > 0:
            updates[key] = int(value)
    if target_coverage.strip():
        updates["target_coverage"] = target_coverage.strip()
    if stop_when.strip():
        updates["stop_when"] = stop_when.strip()
    parsed_databases = _source_list(databases)
    if parsed_databases:
        updates["databases"] = parsed_databases
    trace = runtime.start_trace(
        tool_name="set_stop_conditions",
        label="更新停止条件",
        input={"requested": updates, "current": current},
    )
    next_policy = normalize_stop_policy(updates, current_policy=current)
    runtime.update_meta(stop_policy=next_policy)
    runtime.finish_trace(
        trace,
        tool_name="set_stop_conditions",
        label="停止条件已更新",
        output={"stop_policy": next_policy},
    )
    return {"stop_policy": next_policy}


@function_tool
def evaluate_stop_conditions(ctx: RunContextWrapper[AiSearchRuntimeContext]) -> Dict[str, Any]:
    """评估停止条件是否已经满足。"""
    runtime = ctx.context
    trace = runtime.start_trace(tool_name="evaluate_stop_conditions", label="评估停止条件")
    status = _stop_status(runtime)
    runtime.finish_trace(
        trace,
        tool_name="evaluate_stop_conditions",
        label="停止条件已评估",
        output=status,
    )
    return status


@function_tool
def search_patents(
    ctx: RunContextWrapper[AiSearchRuntimeContext],
    query: str,
    limit: int = 20,
    mode: str = "boolean",
    to_date: str = "",
) -> Dict[str, Any]:
    """执行专利检索并保存候选。mode 支持 boolean 或 semantic。"""
    runtime = ctx.context
    stop = _stop_status(runtime)
    if stop["should_stop"]:
        trace = runtime.start_trace(
            tool_name="search_patents",
            label="停止条件已满足，跳过专利检索",
            input={"query": query, "limit": limit, "mode": mode, "to_date": to_date},
        )
        runtime.finish_trace(
            trace,
            tool_name="search_patents",
            label="停止条件已满足，未继续检索",
            output={"stop": stop},
        )
        return {"blocked": True, "stop": stop}
    query_text = _safe_text(query)
    if not query_text:
        return {"error": "query 不能为空。"}
    remaining_capacity = _remaining_candidate_capacity(runtime)
    if remaining_capacity <= 0:
        trace = runtime.start_trace(
            tool_name="search_patents",
            label="候选数量已达上限，跳过专利检索",
            input={"query": query_text, "limit": limit, "mode": mode, "to_date": to_date},
        )
        runtime.finish_trace(
            trace,
            tool_name="search_patents",
            label="候选数量已达上限，未继续检索",
            output={"stop": _stop_status(runtime)},
        )
        return {"blocked": True, "stop": _stop_status(runtime)}
    resolved_limit = min(_limit(limit, default=20, upper=50), remaining_capacity)
    _ensure_policy_databases(runtime, ["zhihuiya"])
    trace = runtime.start_trace(
        tool_name="search_patents",
        label=f"检索智慧芽：{query_text[:80]}",
        detail=f"mode={mode}, limit={resolved_limit}",
        input={"query": query_text, "limit": resolved_limit, "mode": mode, "to_date": to_date},
    )
    try:
        client = SearchClientFactory.get_client("zhihuiya")
        if str(mode or "").strip().lower() == "semantic":
            raw = client.search_semantic(query_text, to_date=to_date, limit=resolved_limit)
        else:
            raw = client.search(query_text, limit=resolved_limit)
        retrieved = [_normalize_patent_item(item) for item in _patent_items_from_response(raw)]
        normalized = retrieved[:resolved_limit]
        stored = _upsert_candidates(runtime, normalized, source_label="zhihuiya", query=query_text)
        _record_query(runtime, new_count=int(stored.get("new") or 0))
        top_documents = _stored_document_summaries(runtime, normalized, limit=5, include_abstract=False)
        runtime.finish_trace(
            trace,
            tool_name="search_patents",
            label=f"智慧芽检索完成，新增 {int(stored.get('new') or 0)} 个候选",
            detail=f"召回 {len(retrieved)} 条，保留 {len(normalized)} 条，候选池 {int(stored.get('total') or 0)} 条",
            output={
                "retrieved_count": len(retrieved),
                "kept_count": len(normalized),
                "new_count": int(stored.get("new") or 0),
                "skipped_target": int(stored.get("skipped_target") or 0),
                "candidate_total": int(stored.get("total") or 0),
                "top_documents": top_documents,
                "stop": _stop_status(runtime),
            },
        )
        return {
            "query": query_text,
            "mode": mode,
            "retrieved_count": len(retrieved),
            "count": len(normalized),
            "top_documents": top_documents,
            **stored,
            "stop": _stop_status(runtime),
        }
    except Exception as exc:
        runtime.finish_trace(
            trace,
            tool_name="search_patents",
            label="智慧芽检索失败",
            detail=str(exc),
            status="failed",
            output={"error": str(exc)},
        )
        raise


@function_tool
def search_academic(
    ctx: RunContextWrapper[AiSearchRuntimeContext],
    query: str,
    databases: str = "openalex,semanticscholar,crossref",
    priority_date: str = "",
    per_database: int = 10,
) -> Dict[str, Any]:
    """执行论文/非专利文献检索并保存候选。"""
    runtime = ctx.context
    stop = _stop_status(runtime)
    if stop["should_stop"]:
        trace = runtime.start_trace(
            tool_name="search_academic",
            label="停止条件已满足，跳过论文检索",
            input={"query": query, "databases": databases, "priority_date": priority_date, "per_database": per_database},
        )
        runtime.finish_trace(
            trace,
            tool_name="search_academic",
            label="停止条件已满足，未继续检索",
            output={"stop": stop},
        )
        return {"blocked": True, "stop": stop}
    query_text = _safe_text(query)
    if not query_text:
        return {"error": "query 不能为空。"}
    selected_sources = _source_list(databases) or ["openalex", "semanticscholar", "crossref"]
    _ensure_policy_databases(runtime, selected_sources)
    remaining_capacity = _remaining_candidate_capacity(runtime)
    if remaining_capacity <= 0:
        trace = runtime.start_trace(
            tool_name="search_academic",
            label="候选数量已达上限，跳过论文检索",
            input={"query": query_text, "databases": databases, "priority_date": priority_date, "per_database": per_database},
        )
        runtime.finish_trace(
            trace,
            tool_name="search_academic",
            label="候选数量已达上限，未继续检索",
            output={"stop": _stop_status(runtime)},
        )
        return {"blocked": True, "stop": _stop_status(runtime)}
    per_query = min(_limit(per_database, default=10, upper=25), remaining_capacity)
    trace = runtime.start_trace(
        tool_name="search_academic",
        label=f"检索论文数据库：{query_text[:80]}",
        detail=",".join(selected_sources),
        input={
            "query": query_text,
            "databases": selected_sources,
            "priority_date": priority_date,
            "per_database": per_query,
        },
    )
    try:
        client = AcademicSearchClient()
        normalized: List[Dict[str, Any]] = []
        errors: Dict[str, str] = {}
        for source in selected_sources:
            if len(normalized) >= remaining_capacity:
                break
            source_limit = max(1, min(per_query, remaining_capacity - len(normalized)))
            try:
                if source == "openalex":
                    items = client.search_openalex(query_text, priority_date or None, source_limit)
                elif source == "semanticscholar":
                    items = client.search_semanticscholar(query_text, priority_date or None, source_limit)
                elif source == "crossref":
                    items = client.search_crossref(query_text, priority_date or None, source_limit)
                else:
                    continue
                source_items = [_normalize_academic_item(item, source) for item in items if isinstance(item, dict)]
                normalized.extend(source_items[: max(0, remaining_capacity - len(normalized))])
            except Exception as exc:
                errors[source] = str(exc)
        stored = _upsert_candidates(runtime, normalized, source_label="academic", query=query_text)
        _record_query(runtime, query_count=len(selected_sources), new_count=int(stored.get("new") or 0))
        top_documents = _stored_document_summaries(runtime, normalized, limit=5, include_abstract=False)
        runtime.finish_trace(
            trace,
            tool_name="search_academic",
            label=f"论文检索完成，新增 {int(stored.get('new') or 0)} 个候选",
            detail=f"召回 {len(normalized)} 条，错误来源 {len(errors)} 个",
            output={
                "sources": selected_sources,
                "retrieved_count": len(normalized),
                "new_count": int(stored.get("new") or 0),
                "skipped_target": int(stored.get("skipped_target") or 0),
                "candidate_total": int(stored.get("total") or 0),
                "errors": errors,
                "top_documents": top_documents,
                "stop": _stop_status(runtime),
            },
        )
        return {
            "query": query_text,
            "sources": selected_sources,
            "count": len(normalized),
            "errors": errors,
            "top_documents": top_documents,
            **stored,
            "stop": _stop_status(runtime),
        }
    except Exception as exc:
        runtime.finish_trace(
            trace,
            tool_name="search_academic",
            label="论文检索失败",
            detail=str(exc),
            status="failed",
            output={"error": str(exc)},
        )
        raise


def _fetch_patent_results_sync(query: str, mode: str, to_date: str, limit: int) -> List[Dict[str, Any]]:
    client = SearchClientFactory.get_client("zhihuiya")
    if str(mode or "").strip().lower() == "semantic":
        raw = client.search_semantic(query, to_date=to_date, limit=limit)
    else:
        raw = client.search(query, limit=limit)
    return [_normalize_patent_item(item) for item in _patent_items_from_response(raw)]


def _fetch_academic_results_sync(
    query: str,
    sources: List[str],
    priority_date: str,
    per_source: int,
    max_total: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    client = AcademicSearchClient()
    normalized: List[Dict[str, Any]] = []
    errors: Dict[str, str] = {}
    for source in sources:
        if len(normalized) >= max_total:
            break
        source_limit = max(1, min(per_source, max_total - len(normalized)))
        try:
            if source == "openalex":
                items = client.search_openalex(query, priority_date or None, source_limit)
            elif source == "semanticscholar":
                items = client.search_semanticscholar(query, priority_date or None, source_limit)
            elif source == "crossref":
                items = client.search_crossref(query, priority_date or None, source_limit)
            else:
                continue
            source_items = [_normalize_academic_item(item, source) for item in items if isinstance(item, dict)]
            normalized.extend(source_items[: max(0, max_total - len(normalized))])
        except Exception as exc:
            errors[source] = str(exc)
    return normalized, errors


async def _fetch_retrieval_lane(
    runtime: AiSearchRuntimeContext,
    lane: Dict[str, Any],
    *,
    parent_trace_id: str,
) -> Dict[str, Any]:
    kind = str(lane.get("kind") or "").strip()
    query = _safe_text(lane.get("query"))
    label_prefix = "智慧芽" if kind == "patent" else "论文"
    trace = runtime.start_trace(
        tool_name="retrieval_lane",
        label=f"{label_prefix}召回：{query[:80]}",
        detail=str(lane.get("mode") or lane.get("sources") or ""),
        trace_type="tool",
        actor_name="retrieval-agent",
        input=lane,
        parent_trace_id=parent_trace_id,
    )
    try:
        if kind == "patent":
            retrieved = await asyncio.to_thread(
                _fetch_patent_results_sync,
                query,
                str(lane.get("mode") or "boolean"),
                str(lane.get("to_date") or ""),
                int(lane.get("limit") or 10),
            )
            return {**lane, "trace": trace, "retrieved": retrieved, "errors": {}}
        sources = _source_list(lane.get("sources")) or ["openalex", "semanticscholar", "crossref"]
        retrieved, errors = await asyncio.to_thread(
            _fetch_academic_results_sync,
            query,
            sources,
            str(lane.get("priority_date") or ""),
            int(lane.get("per_source") or 5),
            int(lane.get("limit") or 10),
        )
        return {**lane, "trace": trace, "retrieved": retrieved, "errors": errors}
    except Exception as exc:
        runtime.finish_trace(
            trace,
            tool_name="retrieval_lane",
            label=f"{label_prefix}召回失败",
            detail=str(exc),
            status="failed",
            trace_type="tool",
            actor_name="retrieval-agent",
            output={"error": str(exc)},
            parent_trace_id=parent_trace_id,
        )
        return {**lane, "trace": trace, "retrieved": [], "errors": {"lane": str(exc)}, "failed": True}


def _fallback_retrieval_summary(lanes: List[Dict[str, Any]]) -> str:
    if not lanes:
        return "本轮未执行新的召回。"
    new_count = sum(int(item.get("new_count") or 0) for item in lanes)
    retrieved_count = sum(int(item.get("retrieved_count") or 0) for item in lanes)
    modes = "、".join(
        sorted({str(item.get("mode") or item.get("kind") or "").strip() for item in lanes if item.get("mode") or item.get("kind")})
    )
    return f"本轮通过 {modes or '多路'} 召回共检索 {retrieved_count} 条，新增候选 {new_count} 条。"


@function_tool
async def run_retrieval_agent(
    ctx: RunContextWrapper[AiSearchRuntimeContext],
    search_goal: str,
    semantic_queries: str = "",
    boolean_queries: str = "",
    academic_queries: str = "",
    to_date: str = "",
    priority_date: str = "",
    per_query_limit: int = 15,
    include_academic: bool = False,
) -> Dict[str, Any]:
    """运行 retrieval-agent 批量召回。主 agent 决定检索目标和查询，子 agent 只执行并压缩态势。"""
    runtime = ctx.context
    goal = _safe_text(search_goal)
    resolved_limit = _limit(per_query_limit, default=15, upper=25)
    semantic = _parse_query_list(semantic_queries, limit=3)
    boolean = _parse_query_list(boolean_queries, limit=3)
    academic = _parse_query_list(academic_queries, limit=2)
    if not semantic and not boolean and goal:
        semantic = [goal]
        boolean = [goal]
    if include_academic and not academic and goal:
        academic = [goal]
    trace = runtime.start_trace(
        tool_name="retrieval_agent",
        label=f"子 Agent 召回：{goal[:80] or '未命名目标'}",
        detail="semantic/boolean/academic batch retrieval",
        trace_type="agent",
        actor_name="retrieval-agent",
        input={
            "search_goal": goal,
            "semantic_queries": semantic,
            "boolean_queries": boolean,
            "academic_queries": academic,
            "to_date": to_date,
            "priority_date": priority_date,
            "per_query_limit": resolved_limit,
            "model": _default_model_name() or "unconfigured",
        },
        metadata={"model_tier": "default"},
    )
    stop = _stop_status(runtime)
    if stop["should_stop"]:
        result = {"blocked": True, "stop": stop, "summary": "停止条件已满足，retrieval-agent 未继续召回。"}
        runtime.finish_trace(
            trace,
            tool_name="retrieval_agent",
            label="子 Agent 召回已跳过",
            trace_type="agent",
            actor_name="retrieval-agent",
            output=result,
            metadata={"model_tier": "default"},
        )
        return result

    lanes: List[Dict[str, Any]] = []
    for query in semantic:
        lanes.append({"kind": "patent", "mode": "semantic", "query": query, "to_date": to_date, "limit": resolved_limit})
    for query in boolean:
        lanes.append({"kind": "patent", "mode": "boolean", "query": query, "to_date": to_date, "limit": resolved_limit})
    if academic:
        sources = [item for item in _source_list(runtime.stop_policy().get("databases")) if item != "zhihuiya"]
        sources = sources or ["openalex", "semanticscholar", "crossref"]
        per_source = max(1, min(8, resolved_limit // max(1, len(sources))))
        for query in academic:
            lanes.append(
                {
                    "kind": "academic",
                    "query": query,
                    "sources": sources,
                    "priority_date": priority_date,
                    "per_source": per_source,
                    "limit": resolved_limit,
                }
            )
    if not lanes:
        result = {"summary": "缺少可执行检索式，retrieval-agent 未召回。", "lanes": [], "stop": _stop_status(runtime)}
        runtime.finish_trace(
            trace,
            tool_name="retrieval_agent",
            label="子 Agent 召回无可执行检索式",
            trace_type="agent",
            actor_name="retrieval-agent",
            output=result,
            metadata={"model_tier": "default"},
        )
        return result

    _ensure_policy_databases(runtime, ["zhihuiya"] + [source for lane in lanes for source in _source_list(lane.get("sources"))])
    lane_results = await asyncio.gather(
        *[_fetch_retrieval_lane(runtime, lane, parent_trace_id=trace[0]) for lane in lanes]
    )
    reports: List[Dict[str, Any]] = []
    for lane_result in lane_results:
        lane_trace = lane_result["trace"]
        query = _safe_text(lane_result.get("query"))
        kind = str(lane_result.get("kind") or "").strip()
        if lane_result.get("failed"):
            reports.append(
                {
                    "kind": kind,
                    "query": query,
                    "mode": lane_result.get("mode"),
                    "retrieved_count": 0,
                    "new_count": 0,
                    "errors": lane_result.get("errors") or {},
                }
            )
            continue
        remaining_capacity = _remaining_candidate_capacity(runtime)
        if remaining_capacity <= 0 or _stop_status(runtime)["should_stop"]:
            runtime.finish_trace(
                lane_trace,
                tool_name="retrieval_lane",
                label="停止条件已满足，召回结果未继续入库",
                trace_type="tool",
                actor_name="retrieval-agent",
                output={"stop": _stop_status(runtime), "retrieved_count": len(lane_result.get("retrieved") or [])},
                parent_trace_id=trace[0],
            )
            reports.append(
                {
                    "kind": kind,
                    "query": query,
                    "mode": lane_result.get("mode"),
                    "retrieved_count": len(lane_result.get("retrieved") or []),
                    "new_count": 0,
                    "blocked": True,
                    "stop": _stop_status(runtime),
                }
            )
            continue
        normalized = list(lane_result.get("retrieved") or [])[:remaining_capacity]
        source_label = "zhihuiya" if kind == "patent" else "academic"
        stored = _upsert_candidates(runtime, normalized, source_label=source_label, query=query)
        query_count = len(_source_list(lane_result.get("sources"))) if kind == "academic" else 1
        _record_query(runtime, query_count=query_count, new_count=int(stored.get("new") or 0))
        top_documents = _stored_document_summaries(runtime, normalized, limit=5, include_abstract=False)
        lane_report = {
            "kind": kind,
            "query": query,
            "mode": lane_result.get("mode"),
            "sources": _source_list(lane_result.get("sources")),
            "retrieved_count": len(lane_result.get("retrieved") or []),
            "kept_count": len(normalized),
            "new_count": int(stored.get("new") or 0),
            "skipped_target": int(stored.get("skipped_target") or 0),
            "candidate_total": int(stored.get("total") or 0),
            "errors": lane_result.get("errors") or {},
            "top_documents": top_documents,
        }
        runtime.finish_trace(
            lane_trace,
            tool_name="retrieval_lane",
            label=f"召回完成，新增 {lane_report['new_count']} 个候选",
            detail=f"召回 {lane_report['retrieved_count']} 条，保留 {lane_report['kept_count']} 条",
            trace_type="tool",
            actor_name="retrieval-agent",
            output=lane_report,
            parent_trace_id=trace[0],
        )
        reports.append(lane_report)

    summary_payload = {"search_goal": goal, "lanes": reports, "stats": _stop_status(runtime)["stats"]}
    summary = ""
    summary_error = ""
    try:
        summary = await _run_default_child_summary("retrieval-agent", RETRIEVAL_AGENT_SUMMARY_PROMPT, summary_payload)
    except Exception as exc:
        summary_error = str(exc)
    result = {
        "summary": summary or _fallback_retrieval_summary(reports),
        "summary_error": summary_error,
        "lanes": reports,
        "stats": _stop_status(runtime)["stats"],
        "stop": _stop_status(runtime),
    }
    runtime.finish_trace(
        trace,
        tool_name="retrieval_agent",
        label=f"子 Agent 召回完成，新增 {sum(int(item.get('new_count') or 0) for item in reports)} 个候选",
        trace_type="agent",
        actor_name="retrieval-agent",
        output={
            "summary": result["summary"],
            "lane_count": len(reports),
            "new_count": sum(int(item.get("new_count") or 0) for item in reports),
            "candidate_total": result["stats"].get("candidates"),
            "summary_error": summary_error,
        },
        metadata={"model_tier": "default", "model": _default_model_name() or "unconfigured"},
    )
    return result


def _review_terms(goal: str) -> List[str]:
    terms = [_safe_text(item) for item in re.split(r"[\s,，;；、/|()（）]+", goal) if _safe_text(item)]
    compact_terms: List[str] = []
    seen: set[str] = set()
    for term in terms:
        if len(term) > 18:
            for start in range(0, min(len(term), 36), 6):
                chunk = term[start : start + 8]
                if len(chunk) >= 2 and chunk not in seen:
                    seen.add(chunk)
                    compact_terms.append(chunk)
        elif len(term) >= 2 and term not in seen:
            seen.add(term)
            compact_terms.append(term)
    return compact_terms[:30]


def _coarse_review_score(goal: str, item: Dict[str, Any]) -> Tuple[float, List[str]]:
    terms = _review_terms(goal)
    text = f"{item.get('title') or ''} {item.get('abstract') or ''} {item.get('primary_ipc') or ''}".lower()
    hits = [term for term in terms if term.lower() in text]
    base_score = 0.0
    try:
        base_score = float(item.get("score") or 0)
    except Exception:
        base_score = 0.0
    hit_score = (len(hits) / max(1, min(len(terms), 8))) if terms else 0.0
    score = min(1.0, max(base_score, 0.0) * 0.35 + hit_score * 0.65)
    return round(score, 4), hits[:8]


async def _close_read_patent_for_review(
    runtime: AiSearchRuntimeContext,
    item: Dict[str, Any],
    *,
    parent_trace_id: str,
) -> Dict[str, Any]:
    pn = _safe_text(item.get("pn")).upper()
    document_id = _safe_text(item.get("document_id"))
    trace = runtime.start_trace(
        tool_name="review_close_read",
        label=f"精读专利详情：{pn}",
        trace_type="tool",
        actor_name="review-agent",
        input={"pn": pn, "document_id": document_id},
        parent_trace_id=parent_trace_id,
    )
    try:
        detail = await asyncio.to_thread(SearchClientFactory.get_client("zhihuiya").get_patent_detail, pn)
        detail = detail if isinstance(detail, dict) else {}
        claims_preview = _safe_text(detail.get("claims_text") or detail.get("claims"))[:800]
        description_preview = _safe_text(detail.get("description_text") or detail.get("description"))[:800]
        evidence_summary = _safe_text(detail.get("full_text_combined"))[:2000]
        reason = "已读取权利要求和说明书片段，用于精读判断。"
        runtime.storage.update_ai_search_document(
            runtime.task_id,
            runtime.plan_version,
            document_id,
            close_read_status="completed",
            close_read_reason=reason,
            close_read_at=utc_now_z(),
            detail_source="zhihuiya_detail",
            evidence_summary=evidence_summary,
            key_passages_json=[
                {"source": "claims", "preview": claims_preview[:300]},
                {"source": "description", "preview": description_preview[:300]},
            ],
        )
        card = {
            "document_id": document_id,
            "pn": pn,
            "title": item.get("title"),
            "claims_preview": claims_preview,
            "description_preview": description_preview,
            "reason": reason,
        }
        runtime.finish_trace(
            trace,
            tool_name="review_close_read",
            label=f"精读完成：{pn}",
            trace_type="tool",
            actor_name="review-agent",
            output={
                "document_id": document_id,
                "pn": pn,
                "claims_chars": len(claims_preview),
                "description_chars": len(description_preview),
            },
            parent_trace_id=parent_trace_id,
        )
        return card
    except Exception as exc:
        runtime.storage.update_ai_search_document(
            runtime.task_id,
            runtime.plan_version,
            document_id,
            close_read_status="failed",
            close_read_reason=str(exc),
            close_read_at=utc_now_z(),
        )
        runtime.finish_trace(
            trace,
            tool_name="review_close_read",
            label=f"精读失败：{pn}",
            detail=str(exc),
            status="failed",
            trace_type="tool",
            actor_name="review-agent",
            output={"error": str(exc), "document_id": document_id, "pn": pn},
            parent_trace_id=parent_trace_id,
        )
        return {"document_id": document_id, "pn": pn, "error": str(exc)}


def _fallback_review_summary(shortlisted: List[Dict[str, Any]], close_read_cards: List[Dict[str, Any]]) -> str:
    if not shortlisted:
        return "本轮粗筛没有发现明显优先候选。"
    names = "、".join(str(item.get("id") or item.get("title") or "") for item in shortlisted[:5])
    return f"本轮粗筛保留 {len(shortlisted)} 个优先候选，其中 {len(close_read_cards)} 个已做详情精读。优先关注：{names}。"


@function_tool
async def run_review_agent(
    ctx: RunContextWrapper[AiSearchRuntimeContext],
    review_goal: str,
    top_k: int = 12,
    close_read_top_k: int = 3,
) -> Dict[str, Any]:
    """运行 review-agent 对候选做粗筛和少量精读；只返回压缩审阅摘要。"""
    runtime = ctx.context
    goal = _safe_text(review_goal)
    resolved_top_k = _limit(top_k, default=12, upper=30)
    try:
        requested_close_top_k = int(close_read_top_k or 0)
    except Exception:
        requested_close_top_k = 3
    resolved_close_top_k = 0 if requested_close_top_k <= 0 else min(_limit(requested_close_top_k, default=3, upper=8), resolved_top_k)
    trace = runtime.start_trace(
        tool_name="review_agent",
        label=f"子 Agent 筛选/精读：{goal[:80] or '候选审阅'}",
        detail=f"top_k={resolved_top_k}, close_read_top_k={resolved_close_top_k}",
        trace_type="agent",
        actor_name="review-agent",
        input={"review_goal": goal, "top_k": resolved_top_k, "close_read_top_k": resolved_close_top_k, "model": _default_model_name() or "unconfigured"},
        metadata={"model_tier": "default"},
    )
    candidates = [
        item
        for item in runtime.documents()
        if str(item.get("stage") or "") not in {"selected", "rejected"}
        and not bool(item.get("user_removed"))
    ][:resolved_top_k]
    scored: List[Dict[str, Any]] = []
    for item in candidates:
        score, hits = _coarse_review_score(goal, item)
        scored.append({"document": item, "score": score, "hits": hits})
    scored.sort(key=lambda item: item["score"], reverse=True)
    shortlist_size = min(max(resolved_close_top_k * 2, 5), len(scored))
    shortlisted_rows = scored[:shortlist_size]
    reviewed_at = utc_now_z()
    for index, row in enumerate(scored):
        item = row["document"]
        document_id = _safe_text(item.get("document_id"))
        if not document_id:
            continue
        hits = row["hits"]
        is_shortlisted = index < shortlist_size
        reason = (
            f"粗筛命中：{'、'.join(hits)}。" if hits else "粗筛未发现明确关键词命中，按现有排序保留观察。"
        )
        updates: Dict[str, Any] = {
            "coarse_status": "pass" if is_shortlisted else "reviewed",
            "coarse_reason": reason,
            "coarse_screened_at": reviewed_at,
            "score": max(float(item.get("score") or 0), float(row["score"])),
        }
        if is_shortlisted and str(item.get("stage") or "") == "candidate":
            updates["stage"] = "shortlisted"
        runtime.storage.update_ai_search_document(runtime.task_id, runtime.plan_version, document_id, **updates)

    refreshed_docs = runtime.documents()
    shortlisted_ids = {
        _safe_text(row["document"].get("document_id"))
        for row in shortlisted_rows
        if _safe_text(row["document"].get("document_id"))
    }
    shortlisted_docs = [item for item in refreshed_docs if _safe_text(item.get("document_id")) in shortlisted_ids]
    close_read_targets = [
        item
        for item in shortlisted_docs
        if str(item.get("source_type") or "") == "patent" and _safe_text(item.get("pn"))
    ][:resolved_close_top_k]
    close_read_cards = await asyncio.gather(
        *[
            _close_read_patent_for_review(runtime, item, parent_trace_id=trace[0])
            for item in close_read_targets
        ]
    ) if close_read_targets else []
    runtime.append_event("documents.updated", documents_payload(runtime))

    recommended = [
        _compact_document_summary(item, include_abstract=True, max_abstract_chars=180)
        for item in shortlisted_docs[:shortlist_size]
    ]
    summary_payload = {
        "review_goal": goal,
        "reviewed": len(scored),
        "recommended": recommended,
        "close_read_cards": close_read_cards,
        "stats": _stop_status(runtime)["stats"],
    }
    summary = ""
    summary_error = ""
    try:
        summary = await _run_default_child_summary("review-agent", REVIEW_AGENT_SUMMARY_PROMPT, summary_payload)
    except Exception as exc:
        summary_error = str(exc)
    result = {
        "summary": summary or _fallback_review_summary(recommended, close_read_cards),
        "summary_error": summary_error,
        "reviewed_count": len(scored),
        "shortlisted_count": len(recommended),
        "close_read_count": len([item for item in close_read_cards if not item.get("error")]),
        "recommended_documents": recommended,
        "stop": _stop_status(runtime),
    }
    runtime.finish_trace(
        trace,
        tool_name="review_agent",
        label=f"子 Agent 筛选/精读完成，保留 {len(recommended)} 个候选",
        trace_type="agent",
        actor_name="review-agent",
        output={
            "summary": result["summary"],
            "reviewed_count": result["reviewed_count"],
            "shortlisted_count": result["shortlisted_count"],
            "close_read_count": result["close_read_count"],
            "summary_error": summary_error,
        },
        metadata={"model_tier": "default", "model": _default_model_name() or "unconfigured"},
    )
    return result


def _parse_patent_number_list(value: Any, *, limit: int) -> List[str]:
    numbers = _extract_patent_numbers(value)
    if not numbers:
        tokens = re.split(r"[\s,，;；、|]+", str(value or "").upper())
        numbers = [
            _normalize_patent_number(token)
            for token in tokens
            if re.match(r"^[A-Z]{2}\s*\d{5,}[A-Z0-9]*$", token.strip())
        ]
    deduped: List[str] = []
    seen: set[str] = set()
    for number in numbers:
        normalized = _normalize_patent_number(number)
        if normalized and normalized not in seen:
            seen.add(normalized)
            deduped.append(normalized)
        if len(deduped) >= limit:
            break
    return deduped


def _patent_detail_record(normalized_pn: str, detail: Dict[str, Any]) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    basic_info = detail.get("basic_info") if isinstance(detail.get("basic_info"), dict) else {}
    record = _normalize_patent_item(
        {
            **detail,
            "pn": normalized_pn,
            "title": detail.get("title") or basic_info.get("title"),
            "abstract": detail.get("abstract") or basic_info.get("abstract"),
        }
    )
    canonical_id = build_ai_search_canonical_id(source_type="patent", external_id=normalized_pn, pn=normalized_pn)
    result = {
        "pn": normalized_pn,
        "title": record.get("title"),
        "abstract_preview": _safe_text(record.get("abstract"))[:500],
        "claims_preview": _safe_text(detail.get("claims_text") or detail.get("claims"))[:800],
        "description_preview": _safe_text(detail.get("description_text") or detail.get("description"))[:800],
    }
    return canonical_id, record, result


def _apply_patent_detail(
    runtime: AiSearchRuntimeContext,
    normalized_pn: str,
    detail: Dict[str, Any],
    *,
    allow_new_candidate: bool = True,
    append_documents_event: bool = True,
) -> Dict[str, Any]:
    detail = detail if isinstance(detail, dict) else {}
    canonical_id, record, result = _patent_detail_record(normalized_pn, detail)
    if _is_target_patent(runtime, record):
        runtime.update_meta(
            source_pn=normalized_pn,
            source_title=record.get("title") or runtime.meta().get("source_title") or normalized_pn,
            target_detail_preview=result,
        )
        return {**result, "stored_as_candidate": False, "target_detail": True}

    document_id = stable_ai_search_document_id(runtime.task_id, runtime.plan_version, canonical_id)
    exists = any(_safe_text(item.get("document_id")) == document_id for item in runtime.documents())
    if not exists and not allow_new_candidate:
        return {
            **result,
            "stored_as_candidate": False,
            "blocked": True,
            "reason": "候选数量已达上限，未新增候选。",
        }

    runtime.storage.upsert_ai_search_documents(
        [
            {
                **record,
                "run_id": runtime.run_id,
                "task_id": runtime.task_id,
                "plan_version": runtime.plan_version,
                "document_id": document_id,
                "stage": "candidate",
                "detail_source": "zhihuiya_detail",
                "key_passages_json": [],
                "evidence_summary": _safe_text(detail.get("full_text_combined"))[:2000],
            }
        ]
    )
    if append_documents_event:
        runtime.append_event("documents.updated", documents_payload(runtime))
    return {**result, "stored_as_candidate": True, "document_id": document_id}


async def _fetch_patent_detail_for_detail_agent(
    runtime: AiSearchRuntimeContext,
    normalized_pn: str,
    *,
    parent_trace_id: str,
    allow_new_candidate: bool,
) -> Dict[str, Any]:
    trace = runtime.start_trace(
        tool_name="fetch_patent_detail",
        label=f"拉取专利详情：{normalized_pn}",
        trace_type="tool",
        actor_name="detail-agent",
        input={"pn": normalized_pn},
        parent_trace_id=parent_trace_id,
    )
    if not allow_new_candidate:
        result = {
            "pn": normalized_pn,
            "blocked": True,
            "stored_as_candidate": False,
            "reason": "候选数量已达上限，未新增候选。",
        }
        runtime.finish_trace(
            trace,
            tool_name="fetch_patent_detail",
            label=f"跳过详情读取：{normalized_pn}",
            detail=result["reason"],
            trace_type="tool",
            actor_name="detail-agent",
            output=result,
            parent_trace_id=parent_trace_id,
        )
        return result
    try:
        detail = await asyncio.to_thread(SearchClientFactory.get_client("zhihuiya").get_patent_detail, normalized_pn)
        result = _apply_patent_detail(
            runtime,
            normalized_pn,
            detail if isinstance(detail, dict) else {},
            allow_new_candidate=True,
            append_documents_event=False,
        )
        runtime.finish_trace(
            trace,
            tool_name="fetch_patent_detail",
            label=(
                f"目标专利详情已读取：{normalized_pn}"
                if result.get("target_detail")
                else f"专利详情已更新：{normalized_pn}"
            ),
            trace_type="tool",
            actor_name="detail-agent",
            output=result,
            parent_trace_id=parent_trace_id,
        )
        return result
    except Exception as exc:
        result = {"pn": normalized_pn, "error": str(exc)}
        runtime.finish_trace(
            trace,
            tool_name="fetch_patent_detail",
            label=f"专利详情拉取失败：{normalized_pn}",
            detail=str(exc),
            status="failed",
            trace_type="tool",
            actor_name="detail-agent",
            output=result,
            parent_trace_id=parent_trace_id,
        )
        return result


@function_tool
async def run_detail_agent(
    ctx: RunContextWrapper[AiSearchRuntimeContext],
    patent_numbers: str,
    detail_goal: str = "",
    max_patents: int = 6,
) -> Dict[str, Any]:
    """运行 detail-agent 批量读取指定公开号的专利详情。"""
    runtime = ctx.context
    resolved_limit = _limit(max_patents, default=6, upper=10)
    numbers = _parse_patent_number_list(patent_numbers, limit=resolved_limit)
    goal = _safe_text(detail_goal)
    trace = runtime.start_trace(
        tool_name="detail_agent",
        label=f"子 Agent 详情读取：{goal[:80] or '指定公开号'}",
        detail=f"patents={len(numbers)}",
        trace_type="agent",
        actor_name="detail-agent",
        input={"detail_goal": goal, "patent_numbers": numbers, "max_patents": resolved_limit},
        metadata={"model_tier": "default"},
    )
    if not numbers:
        result = {
            "summary": "未识别到有效公开号，detail-agent 未读取详情。",
            "fetched_count": 0,
            "stored_count": 0,
            "failed_count": 0,
            "blocked_count": 0,
            "details": [],
        }
        runtime.finish_trace(
            trace,
            tool_name="detail_agent",
            label="子 Agent 详情读取无有效公开号",
            trace_type="agent",
            actor_name="detail-agent",
            output=result,
            metadata={"model_tier": "default"},
        )
        return result

    _ensure_policy_databases(runtime, ["zhihuiya"])
    existing_ids = {_safe_text(item.get("document_id")) for item in runtime.documents()}
    remaining_capacity = _remaining_candidate_capacity(runtime)
    target_numbers = _target_patent_numbers(runtime)
    new_candidate_slots = 0
    allowed_by_number: Dict[str, bool] = {}
    for normalized_pn in numbers:
        canonical_id = build_ai_search_canonical_id(source_type="patent", external_id=normalized_pn, pn=normalized_pn)
        document_id = stable_ai_search_document_id(runtime.task_id, runtime.plan_version, canonical_id)
        if document_id in existing_ids or normalized_pn in target_numbers:
            allowed_by_number[normalized_pn] = True
            continue
        if new_candidate_slots < remaining_capacity:
            allowed_by_number[normalized_pn] = True
            new_candidate_slots += 1
        else:
            allowed_by_number[normalized_pn] = False

    detail_results = await asyncio.gather(
        *[
            _fetch_patent_detail_for_detail_agent(
                runtime,
                number,
                parent_trace_id=trace[0],
                allow_new_candidate=bool(allowed_by_number.get(number)),
            )
            for number in numbers
        ]
    )
    stored_count = len([item for item in detail_results if item.get("stored_as_candidate")])
    failed_count = len([item for item in detail_results if item.get("error")])
    blocked_count = len([item for item in detail_results if item.get("blocked")])
    fetched_count = len([item for item in detail_results if not item.get("error") and not item.get("blocked")])
    if stored_count:
        runtime.append_event("documents.updated", documents_payload(runtime))
    summary = f"已读取 {fetched_count} 篇专利详情，更新候选 {stored_count} 篇"
    if blocked_count:
        summary += f"，因候选上限跳过 {blocked_count} 篇"
    if failed_count:
        summary += f"，失败 {failed_count} 篇"
    summary += "。"
    result = {
        "summary": summary,
        "fetched_count": fetched_count,
        "stored_count": stored_count,
        "failed_count": failed_count,
        "blocked_count": blocked_count,
        "details": detail_results,
        "stop": _stop_status(runtime),
    }
    runtime.finish_trace(
        trace,
        tool_name="detail_agent",
        label=f"子 Agent 详情读取完成，读取 {fetched_count} 篇",
        trace_type="agent",
        actor_name="detail-agent",
        output={
            "summary": summary,
            "fetched_count": fetched_count,
            "stored_count": stored_count,
            "failed_count": failed_count,
            "blocked_count": blocked_count,
        },
        metadata={"model_tier": "default"},
    )
    return result


@function_tool
def fetch_patent_detail(ctx: RunContextWrapper[AiSearchRuntimeContext], pn: str) -> Dict[str, Any]:
    """按公开号拉取专利详情，并更新候选文献详情。"""
    runtime = ctx.context
    normalized_pn = _normalize_patent_number(pn)
    if not normalized_pn:
        return {"error": "pn 不能为空。"}
    trace = runtime.start_trace(
        tool_name="fetch_patent_detail",
        label=f"拉取专利详情：{normalized_pn}",
        input={"pn": normalized_pn},
    )
    try:
        _ensure_policy_databases(runtime, ["zhihuiya"])
        detail = SearchClientFactory.get_client("zhihuiya").get_patent_detail(normalized_pn)
    except Exception as exc:
        runtime.finish_trace(
            trace,
            tool_name="fetch_patent_detail",
            label="专利详情拉取失败",
            detail=str(exc),
            status="failed",
            output={"error": str(exc)},
        )
        raise
    result = _apply_patent_detail(runtime, normalized_pn, detail if isinstance(detail, dict) else {})
    runtime.finish_trace(
        trace,
        tool_name="fetch_patent_detail",
        label=(
            f"目标专利详情已读取：{normalized_pn}"
            if result.get("target_detail")
            else f"专利详情已更新：{normalized_pn}"
        ),
        output=result,
    )
    return result


@function_tool
def select_documents(ctx: RunContextWrapper[AiSearchRuntimeContext], document_ids: str, reason: str = "") -> Dict[str, Any]:
    """把候选文献标记为已选对比文献。document_ids 用逗号分隔。"""
    runtime = ctx.context
    ids = [item.strip() for item in str(document_ids or "").split(",") if item.strip()]
    trace = runtime.start_trace(
        tool_name="select_documents",
        label=f"选中文献 {len(ids)} 篇",
        input={"document_ids": ids, "reason": reason},
    )
    changed = 0
    for document_id in ids:
        if runtime.storage.update_ai_search_document(
            runtime.task_id,
            runtime.plan_version,
            document_id,
            stage="selected",
            agent_reason=_safe_text(reason) or "agent 选中为重点对比文献",
        ):
            changed += 1
    selected_count = len(runtime.selected_documents())
    runtime.storage.update_ai_search_run(runtime.task_id, runtime.run_id, selected_document_count=selected_count)
    runtime.update_meta(selected_document_count=selected_count)
    runtime.append_event("documents.updated", documents_payload(runtime))
    result = {"selected": changed, "selected_count": selected_count, "stop": _stop_status(runtime)}
    runtime.finish_trace(
        trace,
        tool_name="select_documents",
        label=f"已选中文献 {changed} 篇",
        output=result,
    )
    return result


@function_tool
def save_search_report(ctx: RunContextWrapper[AiSearchRuntimeContext], markdown: str) -> Dict[str, Any]:
    """保存当前检索阶段性报告。"""
    text = str(markdown or "").strip()
    if not text:
        return {"error": "报告内容不能为空。"}
    trace = ctx.context.start_trace(
        tool_name="save_search_report",
        label="保存检索报告",
        input={"markdown_chars": len(text), "preview": text[:500]},
    )
    ctx.context.storage.create_ai_search_message(
        {
            "message_id": uuid.uuid4().hex,
            "task_id": ctx.context.task_id,
            "role": "assistant",
            "kind": "chat",
            "content": text,
            "stream_status": "completed",
            "metadata": {"message_variant": "search_report", "render_mode": "markdown"},
        }
    )
    ctx.context.finish_trace(
        trace,
        tool_name="save_search_report",
        label="检索报告已保存",
        output={"saved": True, "markdown_chars": len(text)},
    )
    return {"saved": True}


def build_search_agent() -> Agent[AiSearchRuntimeContext]:
    if not settings.LLM_API_KEY:
        raise ValueError("AI 检索缺少必需的 LLM_API_KEY。")
    set_tracing_disabled(True)
    client_kwargs: Dict[str, Any] = {"api_key": settings.LLM_API_KEY}
    if str(settings.LLM_BASE_URL or "").strip():
        client_kwargs["base_url"] = settings.LLM_BASE_URL
    client = AsyncOpenAI(**client_kwargs)
    model_name = settings.LLM_MODEL_LARGE or settings.LLM_MODEL_DEFAULT
    return Agent(
        name="ai-search-agent",
        instructions=SYSTEM_PROMPT,
        model=OpenAIChatCompletionsModel(model=model_name, openai_client=client),
        model_settings=ModelSettings(temperature=0, parallel_tool_calls=True),
        tools=[
            read_workspace_context,
            read_candidate_summaries,
            set_stop_conditions,
            evaluate_stop_conditions,
            run_retrieval_agent,
            run_review_agent,
            run_detail_agent,
            select_documents,
            save_search_report,
        ],
    )


def build_agent_input(storage: Any, task_id: str, user_text: str) -> str:
    messages = storage.list_ai_search_messages(task_id)[-16:]
    history = "\n".join(
        f"{item.get('role')}: {item.get('content')}"
        for item in messages
        if str(item.get("kind") or "") in {"chat", "answer"} and str(item.get("content") or "").strip()
    )
    return f"会话历史：\n{history or '（无）'}\n\n用户最新输入：\n{user_text.strip()}"


async def run_search_agent(context: AiSearchRuntimeContext, user_text: str) -> str:
    agent_input = build_agent_input(context.storage, context.task_id, user_text)
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            agent = build_search_agent()
            result = await Runner.run(
                agent,
                agent_input,
                context=context,
                max_turns=12,
            )
            return str(result.final_output or "").strip()
        except Exception as exc:
            last_error = exc
            message = str(exc)
            try:
                if "finish_thinking" in locals():
                    finish_thinking("本轮执行遇到错误", {"error": message})
            except Exception:
                pass
            retryable_tool_call_id_error = (
                "ResponseFunctionToolCall" in message
                and "call_id" in message
                and "valid string" in message
            )
            if attempt == 0 and retryable_tool_call_id_error:
                trace = context.start_trace(
                    tool_name="agent_retry",
                    label="模型工具调用兼容性重试",
                    detail="provider returned empty tool call id",
                )
                context.finish_trace(
                    trace,
                    tool_name="agent_retry",
                    label="已重试模型工具调用",
                )
                continue
            raise
    raise last_error or RuntimeError("AI 检索运行失败。")


def _stream_text_delta(event: Any) -> str:
    if str(getattr(event, "type", "") or "") != "raw_response_event":
        return ""
    data = getattr(event, "data", None)
    event_type = str(getattr(data, "type", "") or "")
    if event_type not in {"response.output_text.delta", "response.text.delta"}:
        return ""
    return str(getattr(data, "delta", "") or "")


def _agent_event_name(event: Any) -> str:
    return str(getattr(event, "name", "") or "")


def _agent_name(value: Any) -> str:
    if value is None:
        return ""
    name = str(getattr(value, "name", "") or "").strip()
    if name:
        return name
    return str(value or "").strip()


def _run_item_agent_label(event: Any) -> str:
    name = _agent_event_name(event)
    item = getattr(event, "item", None)
    if name == "reasoning_item_created":
        return "已形成可解释分析摘要"
    if name == "tool_called":
        raw_name = str(getattr(getattr(item, "raw_item", None), "name", "") or getattr(item, "name", "") or "").strip()
        return f"准备调用工具：{raw_name}" if raw_name else "准备调用工具"
    return ""


async def _maybe_await(value: Any) -> None:
    if inspect.isawaitable(value):
        await value


async def run_search_agent_stream(
    context: AiSearchRuntimeContext,
    user_text: str,
    *,
    on_delta: Optional[Callable[[str], Awaitable[None] | None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> str:
    agent_input = build_agent_input(context.storage, context.task_id, user_text)
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            agent = build_search_agent()
            thinking_trace = context.start_trace(
                tool_name="agent_thinking",
                label="理解任务并规划下一步",
                detail="展示的是面向用户的执行摘要，不包含原始隐藏推理。",
                trace_type="thinking",
                actor_name="ai-search-agent",
                metadata={"visibility": "public_summary"},
            )
            thinking_open = True

            def finish_thinking(label: str, output: Optional[Dict[str, Any]] = None) -> None:
                nonlocal thinking_open
                if not thinking_open:
                    return
                context.finish_trace(
                    thinking_trace,
                    tool_name="agent_thinking",
                    label=label,
                    detail="已完成公开执行摘要；原始隐藏推理不会展示。",
                    trace_type="thinking",
                    actor_name="ai-search-agent",
                    output=output or {"visibility": "public_summary"},
                    metadata={"visibility": "public_summary"},
                )
                thinking_open = False

            result = Runner.run_streamed(
                agent,
                agent_input,
                context=context,
                max_turns=12,
            )
            async for event in result.stream_events():
                if should_cancel and should_cancel():
                    finish_thinking("用户已取消本轮执行")
                    result.cancel()
                    return ""
                if str(getattr(event, "type", "") or "") == "run_item_stream_event":
                    label = _run_item_agent_label(event)
                    if label:
                        if _agent_event_name(event) == "tool_called":
                            finish_thinking("已确定下一步执行动作", {"next_step": label})
                delta = _stream_text_delta(event)
                if delta and on_delta:
                    finish_thinking("已开始组织答复", {"next_step": "stream_answer"})
                    await _maybe_await(on_delta(delta))
            finish_thinking("本轮分析已完成")
            return str(result.final_output or "").strip()
        except Exception as exc:
            last_error = exc
            message = str(exc)
            retryable_tool_call_id_error = (
                "ResponseFunctionToolCall" in message
                and "call_id" in message
                and "valid string" in message
            )
            if attempt == 0 and retryable_tool_call_id_error:
                trace = context.start_trace(
                    tool_name="agent_retry",
                    label="模型工具调用兼容性重试",
                    detail="provider returned empty tool call id",
                )
                context.finish_trace(
                    trace,
                    tool_name="agent_retry",
                    label="已重试模型工具调用",
                )
                continue
            raise
    raise last_error or RuntimeError("AI 检索运行失败。")


def set_task_phase(storage: Any, task_id: str, phase: str, *, error_message: str = "") -> None:
    task = storage.get_task(task_id)
    status = phase_to_task_status(phase)
    storage.update_task(
        task_id,
        metadata=merge_ai_search_meta(task, current_phase=phase),
        status=status,
        progress=phase_progress(phase),
        current_step=phase_step(phase),
        error_message=error_message or None,
    )
