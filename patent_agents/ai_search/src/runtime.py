"""Conversational AI search runtime built on the OpenAI Agents SDK."""

from __future__ import annotations

import inspect
import json
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

输出要求：
- 面向用户只输出简洁自然语言。
- 不输出 JSON、工具名、trace、内部字段。
- 涉及文献判断时说明依据：标题、摘要、关键段落或权利要求命中点。
- 如果停止条件已满足，明确说明停止原因和当前结果是否足够。
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
        actor_name: str = "search-agent",
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
        actor_name: str = "search-agent",
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


def _upsert_candidates(ctx: AiSearchRuntimeContext, raw_items: List[Dict[str, Any]], *, source_label: str, query: str) -> Dict[str, Any]:
    existing = {
        str(item.get("canonical_id") or "").strip(): item
        for item in ctx.documents()
        if str(item.get("canonical_id") or "").strip()
    }
    records: List[Dict[str, Any]] = []
    new_count = 0
    now = utc_now_z()
    for item in raw_items:
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
    return {"stored": changed, "new": new_count, "total": len(ctx.documents())}


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
    """读取当前会话、停止条件、历史消息、候选文献和已选文献。"""
    runtime = ctx.context
    trace = runtime.start_trace(tool_name="read_workspace_context", label="读取会话上下文")
    messages = runtime.storage.list_ai_search_messages(runtime.task_id)[-12:]
    meta = runtime.meta()
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
        "candidate_documents": runtime.documents()[:30],
        "selected_documents": runtime.selected_documents(),
    }
    runtime.finish_trace(
        trace,
        tool_name="read_workspace_context",
        label="会话上下文已读取",
        output={
            "messages": len(result["recent_messages"]),
            "candidates": len(result["candidate_documents"]),
            "selected": len(result["selected_documents"]),
            "stop_policy": result["stop_policy"],
            "source": result["source"],
        },
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
        runtime.finish_trace(
            trace,
            tool_name="search_patents",
            label=f"智慧芽检索完成，新增 {int(stored.get('new') or 0)} 个候选",
            detail=f"召回 {len(retrieved)} 条，保留 {len(normalized)} 条，候选池 {int(stored.get('total') or 0)} 条",
            output={
                "retrieved_count": len(retrieved),
                "kept_count": len(normalized),
                "new_count": int(stored.get("new") or 0),
                "candidate_total": int(stored.get("total") or 0),
                "documents": _document_trace_preview(normalized),
                "stop": _stop_status(runtime),
            },
        )
        return {
            "query": query_text,
            "mode": mode,
            "retrieved_count": len(retrieved),
            "count": len(normalized),
            "documents": [
                {
                    "pn": item.get("pn"),
                    "title": item.get("title"),
                    "abstract": item.get("abstract"),
                    "publication_date": item.get("publication_date"),
                    "application_date": item.get("application_date"),
                }
                for item in normalized
            ],
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
        runtime.finish_trace(
            trace,
            tool_name="search_academic",
            label=f"论文检索完成，新增 {int(stored.get('new') or 0)} 个候选",
            detail=f"召回 {len(normalized)} 条，错误来源 {len(errors)} 个",
            output={
                "sources": selected_sources,
                "retrieved_count": len(normalized),
                "new_count": int(stored.get("new") or 0),
                "candidate_total": int(stored.get("total") or 0),
                "errors": errors,
                "documents": _document_trace_preview(normalized),
                "stop": _stop_status(runtime),
            },
        )
        return {"query": query_text, "sources": selected_sources, "count": len(normalized), "errors": errors, **stored, "stop": _stop_status(runtime)}
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


@function_tool
def fetch_patent_detail(ctx: RunContextWrapper[AiSearchRuntimeContext], pn: str) -> Dict[str, Any]:
    """按公开号拉取专利详情，并更新候选文献详情。"""
    runtime = ctx.context
    normalized_pn = _safe_text(pn).upper()
    if not normalized_pn:
        return {"error": "pn 不能为空。"}
    trace = runtime.start_trace(
        tool_name="fetch_patent_detail",
        label=f"拉取专利详情：{normalized_pn}",
        input={"pn": normalized_pn},
    )
    try:
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
    detail = detail if isinstance(detail, dict) else {}
    canonical_id = build_ai_search_canonical_id(source_type="patent", external_id=normalized_pn, pn=normalized_pn)
    basic_info = detail.get("basic_info") if isinstance(detail.get("basic_info"), dict) else {}
    record = _normalize_patent_item(
        {
            **detail,
            "pn": normalized_pn,
            "title": detail.get("title") or basic_info.get("title"),
            "abstract": detail.get("abstract") or basic_info.get("abstract"),
        }
    )
    document_id = stable_ai_search_document_id(runtime.task_id, runtime.plan_version, canonical_id)
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
    runtime.append_event("documents.updated", documents_payload(runtime))
    result = {
        "pn": normalized_pn,
        "title": record.get("title"),
        "abstract": record.get("abstract"),
        "claims_preview": _safe_text(detail.get("claims_text") or detail.get("claims"))[:2000],
        "description_preview": _safe_text(detail.get("description_text") or detail.get("description"))[:2000],
    }
    runtime.finish_trace(
        trace,
        tool_name="fetch_patent_detail",
        label=f"专利详情已更新：{normalized_pn}",
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
        name="patent-search-agent",
        instructions=SYSTEM_PROMPT,
        model=OpenAIChatCompletionsModel(model=model_name, openai_client=client),
        model_settings=ModelSettings(temperature=0, parallel_tool_calls=False),
        tools=[
            read_workspace_context,
            set_stop_conditions,
            evaluate_stop_conditions,
            search_patents,
            search_academic,
            fetch_patent_detail,
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
    if name == "handoff_requested":
        return "准备切换子 Agent"
    if name == "handoff_occured":
        target = _agent_name(getattr(item, "target_agent", None) or getattr(item, "agent", None))
        return f"已切换子 Agent：{target}" if target else "已切换子 Agent"
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
                actor_name="main-agent",
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
                    actor_name="main-agent",
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
                if str(getattr(event, "type", "") or "") == "agent_updated_stream_event":
                    agent_name = _agent_name(getattr(event, "new_agent", None))
                    trace = context.start_trace(
                        tool_name="agent_updated",
                        label=f"切换到 Agent：{agent_name}" if agent_name else "切换 Agent",
                        trace_type="agent",
                        actor_name="main-agent",
                        metadata={"agent": agent_name} if agent_name else None,
                    )
                    context.finish_trace(
                        trace,
                        tool_name="agent_updated",
                        label=f"已切换到 Agent：{agent_name}" if agent_name else "已切换 Agent",
                        trace_type="agent",
                        actor_name=agent_name or "main-agent",
                        output={"agent": agent_name} if agent_name else None,
                    )
                if str(getattr(event, "type", "") or "") == "run_item_stream_event":
                    label = _run_item_agent_label(event)
                    if label:
                        if _agent_event_name(event) in {"tool_called", "handoff_requested", "handoff_occured"}:
                            finish_thinking("已确定下一步执行动作", {"next_step": label})
                        if _agent_event_name(event) in {"handoff_requested", "handoff_occured", "reasoning_item_created"}:
                            trace = context.start_trace(
                                tool_name=_agent_event_name(event),
                                label=label,
                                trace_type="agent" if _agent_event_name(event) != "reasoning_item_created" else "thinking",
                                actor_name="main-agent",
                                metadata={"event": _agent_event_name(event), "visibility": "public_summary"},
                            )
                            context.finish_trace(
                                trace,
                                tool_name=_agent_event_name(event),
                                label=label,
                                trace_type="agent" if _agent_event_name(event) != "reasoning_item_created" else "thinking",
                                actor_name="main-agent",
                                output={"event": _agent_event_name(event), "visibility": "public_summary"},
                                metadata={"event": _agent_event_name(event), "visibility": "public_summary"},
                            )
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
