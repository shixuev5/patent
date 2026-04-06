"""AI 检索主 agent 定义。"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from deepagents import create_deep_agent
from deepagents.backends.state import StateBackend
from langgraph.types import interrupt

from backend.time_utils import utc_now_z

from agents.ai_search.src.checkpointer import AiSearchCheckpointSaver
from agents.ai_search.src.execution_state import normalize_execution_plan
from agents.ai_search.src.runtime import build_guard_middleware, extract_json_object, large_model
from agents.ai_search.src.screening import (
    DEFAULT_COARSE_CHUNK_SIZE,
    DEFAULT_KEY_PASSAGES_LIMIT,
    DEFAULT_SELECTED_LIMIT,
    DEFAULT_SHORTLIST_LIMIT,
    build_close_reader_prompt,
    build_feature_prompt,
    collect_key_terms,
    detail_to_text,
    fallback_passages,
    load_document_details,
    prepare_close_read_workspace,
)
from agents.ai_search.src.state import (
    PHASE_AWAITING_PLAN_CONFIRMATION,
    PHASE_AWAITING_USER_ANSWER,
    PHASE_COMPLETED,
    PHASE_DRAFTING_PLAN,
    PHASE_SEARCHING,
    build_plan_summary,
    get_ai_search_meta,
    merge_ai_search_meta,
    phase_progress,
    phase_step,
    phase_to_task_status,
)
from agents.ai_search.src.subagents.close_reader import build_close_reader_subagent
from agents.ai_search.src.subagents.coarse_screener import build_coarse_screener_subagent
from agents.ai_search.src.subagents.feature_comparer import build_feature_comparer_subagent
from agents.ai_search.src.subagents.query_executor import build_query_executor_subagent
from agents.ai_search.src.subagents.search_elements import (
    build_search_elements_subagent,
    normalize_search_elements_payload,
)


MAIN_AGENT_SYSTEM_PROMPT = """
你是 AI 检索主 agent。

你是唯一控制面。你要自己维护 todo、决定下一步动作，并在需要时调用 specialist 子 agent。不要把流程控制交给外部 Python 编排。

工作方式：
1. 收到需求后，先调用 `write_todos` 建立任务清单，再调用 `task` 使用 `search-elements` 整理检索要素。
2. 若信息不足，调用 `update_search_elements` 保存当前要素，再调用 `ask_user_question` 挂起等待。
3. 若信息足够，调用 `save_search_plan` 保存计划，并调用 `request_plan_confirmation`。
4. 计划确认后，进入执行阶段。你要自己决定何时：
   - 调用 `query-executor`
   - 读取候选池
   - 调用 `coarse-screener`
   - 准备精读 workspace
   - 调用 `close-reader`
   - 调用 `feature-comparer`
5. 执行阶段必须通过工具回写中间结果，不要假设结果已经持久化。
6. 回答保持简洁，不要输出 markdown 代码块，不要伪造工具结果。

你可调用的 specialist 子 agent：
- `search-elements`
- `query-executor`
- `coarse-screener`
- `close-reader`
- `feature-comparer`

执行阶段推荐模式：
- 先 `begin_execution`
- 读取 `get_execution_state`
- 用 `task` 调 `query-executor`
- 用 `save_execution_summary` 持久化摘要
- 用 `list_documents` 看候选池规模，决定是否继续检索或转粗筛
- 调 `coarse-screener` 后用 `apply_coarse_screening`
- 调 `prepare_close_read_batch` 准备全文与 workspace
- 调 `close-reader` 后用 `apply_close_read_results`
- 用 `build_feature_compare_input` + `feature-comparer` + `save_feature_table` 生成对比表
- 最后调用 `complete_execution`

`write_todos` payload_json:
- todos: 数组，每项包含 key、title、status，可选 details
- current_task: 当前任务 key，可为空

`save_execution_summary` payload_json:
- round_id
- lane_results
- new_unique_candidates
- deduped_hits
- candidate_pool_size
- needs_replan
- recommended_adjustments
- stop_signal

`apply_coarse_screening` payload_json:
- keep
- discard

`apply_close_read_results` payload_json:
- selected
- rejected
- key_passages
- selection_summary

`save_feature_table` payload_json:
- table_rows
- summary_markdown
- overall_findings
""".strip()


def build_main_agent(storage: Any, task_id: str):
    checkpointer = AiSearchCheckpointSaver(storage)

    def update_task_phase(phase: str, **ai_search_updates: Any) -> None:
        task = storage.get_task(task_id)
        metadata = merge_ai_search_meta(task, current_phase=phase, **ai_search_updates)
        storage.update_task(
            task_id,
            metadata=metadata,
            status=phase_to_task_status(phase),
            progress=phase_progress(phase),
            current_step=phase_step(phase),
        )

    def update_todos(task_key: str, status: str, *, current_task: str | None = None) -> None:
        task = storage.get_task(task_id)
        meta = get_ai_search_meta(task)
        todos = meta.get("todos") if isinstance(meta.get("todos"), list) else []
        updated: List[Dict[str, Any]] = []
        for item in todos:
            if not isinstance(item, dict):
                continue
            next_item = dict(item)
            if str(next_item.get("key") or "") == task_key:
                next_item["status"] = status
            updated.append(next_item)
        storage.update_task(task_id, metadata=merge_ai_search_meta(task, todos=updated, current_task=current_task))

    def find_message_by_question_id(question_id: str) -> Optional[Dict[str, Any]]:
        for item in reversed(storage.list_ai_search_messages(task_id)):
            if str(item.get("question_id") or "") == str(question_id):
                return item
        return None

    def active_plan_version() -> int:
        task = storage.get_task(task_id)
        meta = get_ai_search_meta(task)
        version = int(meta.get("active_plan_version") or 0)
        if version > 0:
            return version
        latest = storage.get_ai_search_plan(task_id)
        return int(latest.get("plan_version") or 0) if latest else 0

    def current_search_elements(plan_version: Optional[int] = None) -> Dict[str, Any]:
        version = int(plan_version or active_plan_version() or 0)
        if version <= 0:
            return {}
        plan = storage.get_ai_search_plan(task_id, version) or {}
        return plan.get("search_elements_json") if isinstance(plan.get("search_elements_json"), dict) else {}

    def read_todos() -> str:
        """读取当前任务清单。"""
        task = storage.get_task(task_id)
        meta = get_ai_search_meta(task)
        todos = meta.get("todos") if isinstance(meta.get("todos"), list) else []
        return json.dumps({"todos": todos, "current_task": meta.get("current_task")}, ensure_ascii=False)

    def write_todos(payload_json: str) -> str:
        """写入当前任务清单。"""
        payload = extract_json_object(payload_json)
        raw_todos = payload.get("todos") if isinstance(payload.get("todos"), list) else []
        todos: List[Dict[str, Any]] = []
        for item in raw_todos:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            title = str(item.get("title") or "").strip()
            if not key or not title:
                continue
            todos.append(
                {
                    "key": key,
                    "title": title,
                    "status": str(item.get("status") or "pending").strip() or "pending",
                    "details": str(item.get("details") or "").strip(),
                }
            )
        update_task_phase(PHASE_DRAFTING_PLAN, todos=todos, current_task=str(payload.get("current_task") or "").strip() or None)
        return "todos updated"

    def update_search_elements(payload_json: str) -> str:
        """保存结构化检索要素。"""
        payload = normalize_search_elements_payload(extract_json_object(payload_json))
        storage.create_ai_search_message(
            {
                "message_id": uuid.uuid4().hex,
                "task_id": task_id,
                "role": "assistant",
                "kind": "search_elements_update",
                "content": str(payload.get("clarification_summary") or payload.get("objective") or "").strip() or None,
                "stream_status": "completed",
                "metadata": payload,
            }
        )
        update_task_phase(PHASE_DRAFTING_PLAN)
        return "search elements updated"

    def save_search_plan(payload_json: str) -> str:
        """持久化检索计划草案。"""
        payload = extract_json_object(payload_json)
        search_elements_snapshot = normalize_search_elements_payload(payload.get("search_elements_snapshot") or {})
        normalized_plan = normalize_execution_plan(
            {**payload, "search_elements_snapshot": search_elements_snapshot},
            search_elements_snapshot,
        )
        latest_plan = storage.get_ai_search_plan(task_id)
        if latest_plan:
            storage.update_ai_search_plan(task_id, int(latest_plan["plan_version"]), status="superseded", superseded_at=utc_now_z())
        plan_version = storage.get_next_ai_search_plan_version(task_id)
        storage.create_ai_search_plan(
            {
                "task_id": task_id,
                "plan_version": plan_version,
                "status": "draft",
                "objective": payload.get("objective") or search_elements_snapshot.get("objective"),
                "search_elements_json": search_elements_snapshot,
                "plan_json": {"plan_version": plan_version, "status": "draft", **normalized_plan},
            }
        )
        update_task_phase(PHASE_DRAFTING_PLAN, active_plan_version=plan_version, pending_confirmation_plan_version=None)
        return json.dumps({"plan_version": plan_version}, ensure_ascii=False)

    def ask_user_question(prompt: str, reason: str, expected_answer_shape: str) -> str:
        """创建追问并挂起。"""
        task = storage.get_task(task_id)
        meta = get_ai_search_meta(task)
        question_id = str(meta.get("pending_question_id") or "").strip()
        payload: Dict[str, Any]
        if question_id:
            existing = find_message_by_question_id(question_id)
            metadata = existing.get("metadata") if existing else None
            payload = metadata if isinstance(metadata, dict) else {}
            if not payload:
                payload = {
                    "question_id": question_id,
                    "prompt": prompt,
                    "reason": reason,
                    "expected_answer_shape": expected_answer_shape,
                }
        else:
            question_id = uuid.uuid4().hex[:12]
            payload = {
                "question_id": question_id,
                "prompt": prompt,
                "reason": reason,
                "expected_answer_shape": expected_answer_shape,
            }
            storage.create_ai_search_message(
                {
                    "message_id": uuid.uuid4().hex,
                    "task_id": task_id,
                    "role": "assistant",
                    "kind": "question",
                    "content": prompt,
                    "stream_status": "completed",
                    "question_id": question_id,
                    "metadata": payload,
                }
            )
            update_task_phase(PHASE_AWAITING_USER_ANSWER, pending_question_id=question_id)
        answer = interrupt(payload)
        update_task_phase(PHASE_DRAFTING_PLAN, pending_question_id=None)
        return str(answer or "").strip()

    def request_plan_confirmation(plan_version: int, plan_summary: str, confirmation_label: str = "确认检索计划") -> str:
        """请求用户确认计划。"""
        task = storage.get_task(task_id)
        meta = get_ai_search_meta(task)
        pending_plan_version = meta.get("pending_confirmation_plan_version")
        payload = {
            "plan_version": int(plan_version),
            "plan_summary": plan_summary,
            "confirmation_label": confirmation_label,
        }
        if int(pending_plan_version or 0) != int(plan_version):
            storage.update_ai_search_plan(task_id, int(plan_version), status="awaiting_confirmation")
            storage.create_ai_search_message(
                {
                    "message_id": uuid.uuid4().hex,
                    "task_id": task_id,
                    "plan_version": int(plan_version),
                    "role": "assistant",
                    "kind": "plan_confirmation",
                    "content": plan_summary,
                    "stream_status": "completed",
                    "metadata": payload,
                }
            )
            update_task_phase(PHASE_AWAITING_PLAN_CONFIRMATION, pending_confirmation_plan_version=int(plan_version))
        confirmation = interrupt(payload)
        confirmed = False
        if isinstance(confirmation, dict):
            confirmed = bool(confirmation.get("confirmed"))
        elif isinstance(confirmation, str):
            confirmed = confirmation.strip().lower() in {"true", "yes", "confirmed", "ok"}
        if confirmed:
            storage.update_ai_search_plan(task_id, int(plan_version), status="confirmed", confirmed_at=utc_now_z())
            summary = build_plan_summary(storage.get_ai_search_plan(task_id, int(plan_version)))
            update_task_phase(
                PHASE_DRAFTING_PLAN,
                pending_confirmation_plan_version=None,
                current_task="execute_search",
                todos=[
                    {"key": "execute_search", "title": "执行检索召回", "status": "pending", "details": ""},
                    {"key": "coarse_screen", "title": "粗筛候选文献", "status": "pending", "details": ""},
                    {"key": "close_read", "title": "精读并提取证据", "status": "pending", "details": ""},
                    {"key": "generate_feature_table", "title": "生成特征对比表", "status": "pending", "details": json.dumps(summary, ensure_ascii=False)},
                ],
            )
        else:
            update_task_phase(PHASE_DRAFTING_PLAN, pending_confirmation_plan_version=None)
        return "confirmed" if confirmed else "not_confirmed"

    def begin_execution(plan_version: int = 0) -> str:
        """标记执行阶段开始。"""
        version = int(plan_version or active_plan_version() or 0)
        if version <= 0:
            return "missing_plan"
        update_task_phase(PHASE_SEARCHING, active_plan_version=version, current_task="execute_search")
        update_todos("execute_search", "in_progress", current_task="execute_search")
        return json.dumps({"plan_version": version}, ensure_ascii=False)

    def get_execution_state(plan_version: int = 0) -> str:
        """读取当前执行态。"""
        version = int(plan_version or active_plan_version() or 0)
        plan = storage.get_ai_search_plan(task_id, version) or {}
        meta = get_ai_search_meta(storage.get_task(task_id))
        return json.dumps(
            {
                "plan_version": version,
                "phase": str(meta.get("current_phase") or ""),
                "todos": meta.get("todos") or [],
                "current_task": meta.get("current_task"),
                "plan": plan.get("plan_json") if isinstance(plan.get("plan_json"), dict) else {},
                "search_elements": plan.get("search_elements_json") if isinstance(plan.get("search_elements_json"), dict) else {},
                "candidate_count": len(storage.list_ai_search_documents(task_id, version)) if version > 0 else 0,
                "selected_count": len(storage.list_ai_search_documents(task_id, version, stages=["selected"])) if version > 0 else 0,
            },
            ensure_ascii=False,
        )

    def save_execution_summary(payload_json: str, plan_version: int = 0) -> str:
        """保存 query-executor 返回的本轮摘要。"""
        version = int(plan_version or active_plan_version() or 0)
        payload = extract_json_object(payload_json)
        storage.create_ai_search_message(
            {
                "message_id": uuid.uuid4().hex,
                "task_id": task_id,
                "plan_version": version or None,
                "role": "assistant",
                "kind": "execution_summary",
                "content": json.dumps(payload, ensure_ascii=False),
                "stream_status": "completed",
                "metadata": payload,
            }
        )
        return "execution summary saved"

    def list_documents(plan_version: int = 0, stage: str = "") -> str:
        """列出候选/shortlisted/selected 文献。"""
        version = int(plan_version or active_plan_version() or 0)
        stages = [stage] if stage.strip() else None
        docs = storage.list_ai_search_documents(task_id, version, stages=stages)
        return json.dumps({"count": len(docs), "items": docs}, ensure_ascii=False)

    def apply_coarse_screening(payload_json: str, plan_version: int = 0) -> str:
        """把 coarse-screener 结果回写到文献池。"""
        version = int(plan_version or active_plan_version() or 0)
        payload = extract_json_object(payload_json)
        keep_ids = {str(item).strip() for item in (payload.get("keep") or []) if str(item).strip()}
        discard_ids = {str(item).strip() for item in (payload.get("discard") or []) if str(item).strip()}
        current_records = storage.list_ai_search_documents(task_id, version)
        applied = {"kept": 0, "discarded": 0}
        for item in current_records:
            if str(item.get("coarse_status") or "pending") != "pending":
                continue
            if str(item.get("stage") or "") not in {"candidate", ""}:
                continue
            document_id = str(item.get("document_id") or "")
            if document_id in keep_ids:
                storage.update_ai_search_document(
                    task_id,
                    version,
                    document_id,
                    stage="shortlisted",
                    coarse_status="kept",
                    coarse_reason="粗筛保留",
                    coarse_screened_at=utc_now_z(),
                )
                applied["kept"] += 1
            elif document_id in discard_ids:
                storage.update_ai_search_document(
                    task_id,
                    version,
                    document_id,
                    stage="rejected",
                    coarse_status="discarded",
                    coarse_reason="粗筛排除",
                    coarse_screened_at=utc_now_z(),
                )
                applied["discarded"] += 1
        update_todos("coarse_screen", "completed", current_task="close_read")
        return json.dumps(applied, ensure_ascii=False)

    def prepare_close_read_batch(plan_version: int = 0, limit: int = DEFAULT_SHORTLIST_LIMIT) -> str:
        """准备精读批次，包含详情和全文文件路径。"""
        version = int(plan_version or active_plan_version() or 0)
        records = storage.list_ai_search_documents(task_id, version)
        pending = [
            item
            for item in records
            if str(item.get("coarse_status") or "") == "kept" and str(item.get("close_read_status") or "pending") == "pending"
        ][: max(int(limit or DEFAULT_SHORTLIST_LIMIT), 1)]
        details: List[Dict[str, Any]] = []
        for item in pending:
            detail = load_document_details(str(item.get("pn") or "").strip().upper())
            merged = {**item, **detail}
            details.append(merged)
            storage.update_ai_search_document(task_id, version, item["document_id"], detail_fingerprint=detail.get("detail_fingerprint"))
        task = storage.get_task(task_id)
        workspace_root = Path(str(getattr(task, "output_dir", "") or Path.cwd() / ".ai_search" / task_id))
        workspace_dir = workspace_root / "close_read" / f"plan_{version}"
        file_map = prepare_close_read_workspace(workspace_dir, details)
        update_todos("close_read", "in_progress", current_task="close_read")
        return json.dumps(
            {
                "plan_version": version,
                "search_elements": current_search_elements(version),
                "documents": [
                    {
                        "document_id": item["document_id"],
                        "pn": item["pn"],
                        "title": item.get("title") or "",
                        "abstract": item.get("abstract") or "",
                        "claims": item.get("claims") or "",
                        "description": item.get("description") or "",
                        "fulltext_path": file_map.get(str(item.get("pn") or "").strip().upper()),
                    }
                    for item in details
                ],
                "prompt": build_close_reader_prompt(current_search_elements(version), details, file_map),
            },
            ensure_ascii=False,
        )

    def apply_close_read_results(payload_json: str, plan_version: int = 0) -> str:
        """把 close-reader 结果回写到文献池。"""
        version = int(plan_version or active_plan_version() or 0)
        payload = extract_json_object(payload_json)
        selected_ids = {str(item).strip() for item in (payload.get("selected") or []) if str(item).strip()}
        rejected_ids = {str(item).strip() for item in (payload.get("rejected") or []) if str(item).strip()}
        passages_by_doc: Dict[str, List[Dict[str, Any]]] = {}
        for item in payload.get("key_passages") or []:
            if not isinstance(item, dict):
                continue
            document_id = str(item.get("document_id") or "").strip()
            if not document_id:
                continue
            passages_by_doc.setdefault(document_id, []).append(
                {
                    "passage": str(item.get("passage") or "")[:400],
                    "reason": str(item.get("reason") or "").strip(),
                    "location": item.get("location"),
                }
            )
        terms = collect_key_terms(current_search_elements(version))
        current_records = storage.list_ai_search_documents(task_id, version)
        selected_count = 0
        for item in current_records:
            document_id = str(item.get("document_id") or "")
            if str(item.get("coarse_status") or "") != "kept":
                continue
            if str(item.get("close_read_status") or "pending") != "pending":
                continue
            passages = passages_by_doc.get(document_id)
            if not passages:
                detail = load_document_details(str(item.get("pn") or "").strip().upper())
                passages = [
                    {**passage, "document_id": document_id}
                    for passage in fallback_passages(detail_to_text(detail), terms)[:DEFAULT_KEY_PASSAGES_LIMIT]
                ]
            if document_id in selected_ids:
                storage.update_ai_search_document(
                    task_id,
                    version,
                    document_id,
                    stage="selected",
                    key_passages_json=passages,
                    agent_reason="纳入对比文件",
                    close_read_status="selected",
                    close_read_reason="精读后纳入对比文件",
                    close_read_at=utc_now_z(),
                )
                selected_count += 1
            elif document_id in rejected_ids:
                storage.update_ai_search_document(
                    task_id,
                    version,
                    document_id,
                    stage="rejected",
                    key_passages_json=passages,
                    agent_reason="精读后排除",
                    close_read_status="rejected",
                    close_read_reason="精读后排除",
                    close_read_at=utc_now_z(),
                )
        update_todos("close_read", "completed", current_task="generate_feature_table")
        return json.dumps({"selected_count": selected_count}, ensure_ascii=False)

    def build_feature_compare_input(plan_version: int = 0) -> str:
        """构造特征对比输入。"""
        version = int(plan_version or active_plan_version() or 0)
        selected_documents = storage.list_ai_search_documents(task_id, version, stages=["selected"])[:DEFAULT_SELECTED_LIMIT]
        update_todos("generate_feature_table", "in_progress", current_task="generate_feature_table")
        return json.dumps(
            {
                "plan_version": version,
                "selected_documents": selected_documents,
                "prompt": build_feature_prompt(current_search_elements(version), selected_documents),
            },
            ensure_ascii=False,
        )

    def save_feature_table(payload_json: str, plan_version: int = 0) -> str:
        """保存 feature-comparer 结果。"""
        version = int(plan_version or active_plan_version() or 0)
        payload = extract_json_object(payload_json)
        feature_table_id = uuid.uuid4().hex
        storage.create_ai_search_feature_table(
            {
                "feature_table_id": feature_table_id,
                "task_id": task_id,
                "plan_version": version,
                "status": "completed",
                "table_json": payload.get("table_rows") or [],
                "summary_markdown": payload.get("summary_markdown") or "",
            }
        )
        findings = str(payload.get("overall_findings") or "特征对比表已生成。").strip()
        if findings:
            storage.create_ai_search_message(
                {
                    "message_id": uuid.uuid4().hex,
                    "task_id": task_id,
                    "plan_version": version,
                    "role": "assistant",
                    "kind": "chat",
                    "content": findings,
                    "stream_status": "completed",
                    "metadata": {},
                }
            )
        update_task_phase(PHASE_COMPLETED, active_plan_version=version, current_feature_table_id=feature_table_id)
        update_todos("generate_feature_table", "completed", current_task=None)
        return json.dumps({"feature_table_id": feature_table_id}, ensure_ascii=False)

    def complete_execution(summary: str = "", plan_version: int = 0) -> str:
        """结束执行阶段并更新汇总状态。"""
        version = int(plan_version or active_plan_version() or 0)
        selected_count = len(storage.list_ai_search_documents(task_id, version, stages=["selected"])) if version > 0 else 0
        if summary.strip():
            storage.create_ai_search_message(
                {
                    "message_id": uuid.uuid4().hex,
                    "task_id": task_id,
                    "plan_version": version or None,
                    "role": "assistant",
                    "kind": "chat",
                    "content": summary.strip(),
                    "stream_status": "completed",
                    "metadata": {},
                }
            )
        update_task_phase(
            PHASE_COMPLETED,
            active_plan_version=version or None,
            selected_document_count=selected_count,
            current_task=None,
        )
        return json.dumps({"selected_count": selected_count}, ensure_ascii=False)

    return create_deep_agent(
        model=large_model(),
        tools=[
            read_todos,
            write_todos,
            update_search_elements,
            save_search_plan,
            ask_user_question,
            request_plan_confirmation,
            begin_execution,
            get_execution_state,
            save_execution_summary,
            list_documents,
            apply_coarse_screening,
            prepare_close_read_batch,
            apply_close_read_results,
            build_feature_compare_input,
            save_feature_table,
            complete_execution,
        ],
        system_prompt=MAIN_AGENT_SYSTEM_PROMPT,
        middleware=[build_guard_middleware("main-agent")],
        subagents=[
            build_search_elements_subagent(),
            build_query_executor_subagent(storage, task_id),
            build_coarse_screener_subagent(),
            build_close_reader_subagent(),
            build_feature_comparer_subagent(),
        ],
        checkpointer=checkpointer,
        backend=StateBackend,
        name=f"ai-search-main-agent-{task_id}",
    )
