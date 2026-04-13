"""WeChat IM runtime orchestration for bound private-chat users."""

from __future__ import annotations

import asyncio
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from loguru import logger

from agents.common.utils.llm import get_llm_service
from backend.ai_search.service import AiSearchService
from backend.models import (
    InternalWeChatInboundAttachment,
    InternalWeChatInboundMessageResponse,
    InternalWeChatOutboundMessage,
)
from backend.storage import (
    TaskStatus,
    TaskType,
    WeChatBinding,
    WeChatConversationSession,
    WeChatFlowSession,
    get_pipeline_manager,
)
from backend.storage.pipeline_adapter import PipelineTaskManager
from backend.time_utils import utc_now
from backend.utils import _cleanup_path
from config import settings


FLOW_ANALYSIS = TaskType.PATENT_ANALYSIS.value
FLOW_REVIEW = TaskType.AI_REVIEW.value
FLOW_REPLY = TaskType.AI_REPLY.value
ACTIVE_FLOW_TYPES = (FLOW_REPLY, FLOW_ANALYSIS, FLOW_REVIEW)
REPLY_ALLOWED_SUFFIXES = {".pdf", ".doc", ".docx"}
PATENT_NUMBER_PATTERN = re.compile(r"\b(?:CN|US|EP|WO|JP|KR)?\s?\d{6,}[A-Z0-9.\-/]*\b", re.IGNORECASE)
MAX_RECENT_TURNS = 8
TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}
YES_TOKENS = {"是", "好的", "好", "切换", "开始吧", "确认", "可以", "行", "yes", "ok"}
NO_TOKENS = {"否", "不用", "算了", "继续当前", "不要切换", "先不切换", "no"}
EXIT_SEARCH_TOKENS = ("退出检索", "暂停检索", "暂停这个检索", "回到普通对话", "离开检索")
CANCEL_FLOW_TOKENS = ("取消当前流程", "取消流程", "先别做了")
CANCEL_SEARCH_TOKENS = ("取消当前检索", "取消这个检索")
SEARCH_RESUME_TOKENS = ("继续检索", "继续刚才", "继续上次", "恢复检索", "接着检索")
SEARCH_CONTROL_TOKENS = {"确认计划", "继续检索", "按当前结果完成"}
CHITCHAT_TOKENS = {"你好", "您好", "谢谢", "多谢", "收到", "好的", "好", "ok", "OK", "嗯嗯"}


@dataclass
class ActiveContextRef:
    kind: str = "none"
    session_id: Optional[str] = None
    title: Optional[str] = None


@dataclass
class ConversationEffect:
    active_context: Optional[ActiveContextRef] = None
    clear_active_context: bool = False
    memory: Optional[Dict[str, Any]] = None


@dataclass
class ConversationResponse:
    messages: List[InternalWeChatOutboundMessage]
    session_type: Optional[str] = None
    flow_session_id: Optional[str] = None
    task_id: Optional[str] = None
    effect: Optional[ConversationEffect] = None


@dataclass
class SearchContextSwitchDecision:
    target_intent: str
    target_label: str
    text: str
    attachments: List[Dict[str, Any]]
    route: Dict[str, Any]


class WeChatRuntimeService:
    def __init__(
        self,
        *,
        storage: Any = None,
        task_manager: Any = None,
        ai_search_service: Optional[AiSearchService] = None,
    ) -> None:
        resolved_task_manager = task_manager or get_pipeline_manager(storage)
        if not isinstance(resolved_task_manager, PipelineTaskManager) and storage is not None:
            resolved_task_manager = PipelineTaskManager(storage)
        self.task_manager = resolved_task_manager
        self._storage = storage if storage is not None else (
            resolved_task_manager.storage if isinstance(resolved_task_manager, PipelineTaskManager) else None
        )
        self.ai_search_service = ai_search_service or AiSearchService()
        self.ai_search_service.task_manager = self.task_manager
        self.llm_service = get_llm_service()

    @property
    def storage(self):
        if self._storage is None:
            self._storage = getattr(self.task_manager, "storage")
        return self._storage

    async def handle_inbound_message(
        self,
        *,
        bot_account_id: str,
        wechat_peer_id: str,
        wechat_peer_name: Optional[str] = None,
        text: Optional[str] = None,
        attachments: Optional[List[InternalWeChatInboundAttachment]] = None,
    ) -> InternalWeChatInboundMessageResponse:
        binding = self._require_binding(bot_account_id, wechat_peer_id)
        self._touch_binding_inbound(binding, wechat_peer_name=wechat_peer_name)
        conversation = self._get_or_create_conversation(binding)
        normalized_text = str(text or "").strip()
        normalized_attachments = [item for item in (attachments or []) if str(item.storedPath or "").strip()]
        self._append_recent_turn(conversation, role="user", text=normalized_text, attachments=normalized_attachments)
        self._save_conversation(conversation, last_inbound=True)

        response = await self._handle_pending_conversation_action(
            binding,
            conversation,
            normalized_text,
            normalized_attachments,
        )
        if response is None:
            if normalized_text == "/cancel":
                response = self.cancel_active_workflow(binding, conversation)
            else:
                active_flow = self._get_active_flow(binding.owner_id)
                active_context = self._active_context(conversation)
                if active_flow:
                    response = await self._handle_active_workflow(binding, conversation, active_flow, normalized_text, normalized_attachments)
                elif active_context.kind == "ai_search" and active_context.session_id:
                    response = await self._handle_active_search_context(binding, conversation, active_context, normalized_text, normalized_attachments)
                else:
                    response = await self._handle_general_message(binding, conversation, normalized_text, normalized_attachments)
        return self._finalize_response(binding, conversation, response)

    def _require_binding(self, bot_account_id: str, wechat_peer_id: str) -> WeChatBinding:
        binding = self.storage.get_wechat_binding_by_peer(str(bot_account_id or "").strip(), str(wechat_peer_id or "").strip())
        if not binding or str(binding.status or "").strip() != "active":
            raise HTTPException(status_code=404, detail="未找到已绑定的平台账号。")
        return binding

    def _touch_binding_inbound(self, binding: WeChatBinding, *, wechat_peer_name: Optional[str]) -> None:
        updates: Dict[str, Any] = {
            "last_inbound_at": utc_now(),
            "updated_at": utc_now(),
        }
        normalized_name = str(wechat_peer_name or "").strip()
        if normalized_name:
            updates["wechat_peer_name"] = normalized_name
        self.storage.update_wechat_binding(binding.binding_id, **updates)

    def _touch_binding_outbound(self, binding: WeChatBinding) -> None:
        self.storage.update_wechat_binding(binding.binding_id, last_outbound_at=utc_now(), updated_at=utc_now())

    def _response(
        self,
        binding: WeChatBinding,
        *,
        session_type: Optional[str] = None,
        flow_session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        messages: Optional[List[InternalWeChatOutboundMessage]] = None,
        effect: Optional[ConversationEffect] = None,
    ) -> InternalWeChatInboundMessageResponse:
        return InternalWeChatInboundMessageResponse(
            ownerId=binding.owner_id,
            bindingId=binding.binding_id,
            sessionType=session_type,
            flowSessionId=flow_session_id,
            taskId=task_id,
            messages=messages or [self._text("已收到消息。")],
        )

    def _text(self, content: str) -> InternalWeChatOutboundMessage:
        return InternalWeChatOutboundMessage(type="text", text=str(content or "").strip())

    def _intent_guidance_text(self) -> str:
        return (
            "请直接说你的目标，我可以处理：AI 检索、专利分析、专利审查、审查意见答复。\n"
            "示例：帮我检索固态电池隔膜相关专利 / 分析专利 CN117347385A / 帮我审查这个专利 / 我要答复审查意见。"
        )

    def _search_context_hint_text(self, title: str | None = None) -> str:
        if title:
            return f"当前仍在检索《{title}》。继续补充条件即可；如需离开，请回复“退出检索”。"
        return "当前仍在检索上下文中。继续补充条件即可；如需离开，请回复“退出检索”。"

    def _ai_search_retry_text(self) -> str:
        return "处理检索消息时出现异常，请稍后重试。你也可以重新描述检索目标、核心技术和约束条件。"

    def _get_or_create_conversation(self, binding: WeChatBinding) -> WeChatConversationSession:
        current = self.storage.get_wechat_conversation_session(binding.binding_id)
        if current:
            return current
        created = WeChatConversationSession(
            conversation_id=f"wcs-{uuid.uuid4().hex[:12]}",
            owner_id=binding.owner_id,
            binding_id=binding.binding_id,
            status="active",
        )
        return self.storage.upsert_wechat_conversation_session(created)

    def _save_conversation(self, conversation: WeChatConversationSession, *, last_inbound: bool = False, last_outbound: bool = False) -> WeChatConversationSession:
        now_dt = utc_now()
        conversation.updated_at = now_dt
        if last_inbound:
            conversation.last_inbound_at = now_dt
        if last_outbound:
            conversation.last_outbound_at = now_dt
        return self.storage.upsert_wechat_conversation_session(conversation)

    def _active_context(self, conversation: WeChatConversationSession) -> ActiveContextRef:
        return ActiveContextRef(
            kind=str(conversation.active_context_kind or "none").strip() or "none",
            session_id=str(conversation.active_context_session_id or "").strip() or None,
            title=str(conversation.active_context_title or "").strip() or None,
        )

    def _set_active_context(self, conversation: WeChatConversationSession, *, kind: str, session_id: Optional[str], title: Optional[str]) -> None:
        conversation.active_context_kind = str(kind or "none").strip() or "none"
        conversation.active_context_session_id = str(session_id or "").strip() or None
        conversation.active_context_title = str(title or "").strip() or None

    def _clear_active_context(self, conversation: WeChatConversationSession) -> None:
        self._set_active_context(conversation, kind="none", session_id=None, title=None)

    def _conversation_memory(self, conversation: WeChatConversationSession) -> Dict[str, Any]:
        memory = conversation.memory if isinstance(conversation.memory, dict) else {}
        conversation.memory = memory
        return memory

    def _pending_conversation_action(self, conversation: WeChatConversationSession) -> Optional[Dict[str, Any]]:
        memory = self._conversation_memory(conversation)
        action = memory.get("pending_action")
        return dict(action) if isinstance(action, dict) else None

    def _set_pending_conversation_action(self, conversation: WeChatConversationSession, action: Dict[str, Any]) -> None:
        memory = self._conversation_memory(conversation)
        memory["pending_action"] = action

    def _clear_pending_conversation_action(self, conversation: WeChatConversationSession) -> None:
        memory = self._conversation_memory(conversation)
        memory.pop("pending_action", None)

    def _append_recent_turn(
        self,
        conversation: WeChatConversationSession,
        *,
        role: str,
        text: str = "",
        attachments: Optional[List[InternalWeChatInboundAttachment]] = None,
        messages: Optional[List[InternalWeChatOutboundMessage]] = None,
    ) -> None:
        memory = self._conversation_memory(conversation)
        turns = list(memory.get("recent_turns") or [])
        payload: Dict[str, Any] = {"role": role}
        if text:
            payload["text"] = text
        if attachments:
            payload["attachments"] = [
                {
                    "filename": str(item.filename or "").strip(),
                    "storedPath": str(item.storedPath or "").strip(),
                    "contentType": str(item.contentType or "").strip() or None,
                }
                for item in attachments
                if str(item.storedPath or "").strip()
            ]
        if messages:
            payload["messages"] = [
                {
                    "type": msg.type,
                    "text": msg.text,
                    "fileName": msg.fileName,
                    "downloadPath": msg.downloadPath,
                }
                for msg in messages
            ]
        turns.append(payload)
        memory["recent_turns"] = turns[-MAX_RECENT_TURNS:]

    def _finalize_response(
        self,
        binding: WeChatBinding,
        conversation: WeChatConversationSession,
        response: ConversationResponse,
    ) -> InternalWeChatInboundMessageResponse:
        effect = response.effect or ConversationEffect()
        if effect.clear_active_context:
            self._clear_active_context(conversation)
        if effect.active_context is not None:
            self._set_active_context(
                conversation,
                kind=effect.active_context.kind,
                session_id=effect.active_context.session_id,
                title=effect.active_context.title,
            )
        if effect.memory is not None:
            conversation.memory = effect.memory
        self._append_recent_turn(conversation, role="assistant", messages=response.messages)
        self._save_conversation(conversation, last_outbound=True)
        self._touch_binding_outbound(binding)
        return InternalWeChatInboundMessageResponse(
            ownerId=binding.owner_id,
            bindingId=binding.binding_id,
            sessionType=response.session_type,
            flowSessionId=response.flow_session_id,
            taskId=response.task_id,
            messages=response.messages,
        )

    async def _handle_pending_conversation_action(
        self,
        binding: WeChatBinding,
        conversation: WeChatConversationSession,
        text: str,
        attachments: List[InternalWeChatInboundAttachment],
    ) -> Optional[ConversationResponse]:
        pending = self._pending_conversation_action(conversation)
        if not pending:
            return None
        pending_type = str(pending.get("type") or "").strip()
        if pending_type == "switch_context":
            if self._is_affirmative(text):
                self._clear_pending_conversation_action(conversation)
                current_context = self._active_context(conversation)
                if current_context.kind == "ai_search":
                    self.pause_active_search_context(conversation)
                else:
                    self._cancel_all_active_flows(binding.owner_id, status="superseded")
                    self._clear_active_context(conversation)
                route = pending.get("route") if isinstance(pending.get("route"), dict) else {}
                stored_attachments = self._attachments_from_payload(pending.get("attachments"))
                return await self._execute_routed_intent(
                    binding,
                    conversation,
                    route=route,
                    text=str(pending.get("text") or ""),
                    attachments=stored_attachments,
                )
            if self._is_negative(text):
                self._clear_pending_conversation_action(conversation)
                current_context = self._active_context(conversation)
                label = "当前流程" if current_context.kind == "guided_workflow" else "当前检索"
                return ConversationResponse(messages=[self._text(f"已保留{label}。")])
            return ConversationResponse(messages=[self._text("当前有未完成的上下文切换。回复“切换”或“继续当前”即可。")])
        if pending_type == "choose_search_session":
            selected = self._resolve_session_selection(pending, text)
            if not selected:
                return ConversationResponse(messages=[self._text("请按编号选择要继续的检索，例如“1”或“选择 1”。")])
            self._clear_pending_conversation_action(conversation)
            snapshot = self.ai_search_service.get_snapshot(selected["session_id"], binding.owner_id)
            title = str(selected.get("title") or snapshot.session.title or "").strip() or None
            return ConversationResponse(
                session_type=TaskType.AI_SEARCH.value,
                task_id=selected["session_id"],
                messages=self._build_ai_search_messages(snapshot),
                effect=ConversationEffect(active_context=ActiveContextRef(kind="ai_search", session_id=selected["session_id"], title=title)),
            )
        return None

    def _attachments_from_payload(self, payload: Any) -> List[InternalWeChatInboundAttachment]:
        items = payload if isinstance(payload, list) else []
        attachments: List[InternalWeChatInboundAttachment] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            stored_path = str(item.get("storedPath") or "").strip()
            if not stored_path:
                continue
            attachments.append(
                InternalWeChatInboundAttachment(
                    filename=str(item.get("filename") or Path(stored_path).name).strip() or Path(stored_path).name,
                    storedPath=stored_path,
                    contentType=str(item.get("contentType") or "").strip() or None,
                )
            )
        return attachments

    def _resolve_session_selection(self, pending: Dict[str, Any], text: str) -> Optional[Dict[str, Any]]:
        options = pending.get("options") if isinstance(pending.get("options"), list) else []
        numbers = [int(part) for part in re.findall(r"\d+", str(text or "")) if int(part) > 0]
        if numbers:
            index = numbers[0]
            if 1 <= index <= len(options):
                option = options[index - 1]
                if isinstance(option, dict) and str(option.get("session_id") or "").strip():
                    return option
        normalized = str(text or "").strip()
        for option in options:
            if not isinstance(option, dict):
                continue
            title = str(option.get("title") or "").strip()
            if title and title in normalized:
                return option
        return None

    async def _handle_general_message(
        self,
        binding: WeChatBinding,
        conversation: WeChatConversationSession,
        text: str,
        attachments: List[InternalWeChatInboundAttachment],
    ) -> ConversationResponse:
        if not text and not attachments:
            return ConversationResponse(messages=[self._text(self._intent_guidance_text())])
        if text in SEARCH_CONTROL_TOKENS or text.startswith("选择 ") or text.startswith("送审 "):
            if text == "继续检索" and self.list_recent_ai_search_sessions(binding.owner_id):
                return await self.start_or_resume_ai_search(binding, conversation, text=text, attachments=attachments)
            return ConversationResponse(messages=[self._text(self._intent_guidance_text())])
        route = self._route_general_intent(text=text, attachments=attachments)
        return await self._execute_routed_intent(binding, conversation, route=route, text=text, attachments=attachments)

    async def _execute_routed_intent(
        self,
        binding: WeChatBinding,
        conversation: WeChatConversationSession,
        *,
        route: Dict[str, Any],
        text: str,
        attachments: List[InternalWeChatInboundAttachment],
    ) -> ConversationResponse:
        intent = str(route.get("intent") or "").strip()
        confidence = float(route.get("confidence") or 0.0)
        if intent in {"unknown", ""} or bool(route.get("requires_confirmation")) or confidence < 0.55:
            return ConversationResponse(messages=[self._text(self._intent_guidance_text())])
        extracted = route.get("extracted") if isinstance(route.get("extracted"), dict) else {}
        patent_number = self._normalize_patent_number_candidate(extracted.get("patent_number") or text)
        if intent == "chitchat":
            return ConversationResponse(messages=[self._text(self._intent_guidance_text())])
        if intent == TaskType.AI_SEARCH.value:
            return await self.start_or_resume_ai_search(binding, conversation, text=text, attachments=attachments)
        if intent == FLOW_ANALYSIS:
            if attachments or patent_number:
                return self.start_patent_analysis(binding, text=patent_number or "", attachment=attachments[0] if attachments else None)
            return self.begin_guided_flow(binding, conversation, FLOW_ANALYSIS)
        if intent == FLOW_REVIEW:
            if attachments or patent_number:
                return self.start_ai_review(binding, text=patent_number or "", attachment=attachments[0] if attachments else None)
            return self.begin_guided_flow(binding, conversation, FLOW_REVIEW)
        if intent == FLOW_REPLY:
            return self.begin_ai_reply_workflow(binding, conversation)
        if intent == "cancel_or_pause":
            return self.cancel_active_workflow(binding, conversation)
        return ConversationResponse(messages=[self._text(self._intent_guidance_text())])

    async def _handle_active_workflow(
        self,
        binding: WeChatBinding,
        conversation: WeChatConversationSession,
        flow: WeChatFlowSession,
        text: str,
        attachments: List[InternalWeChatInboundAttachment],
    ) -> ConversationResponse:
        if self._is_flow_cancel(text):
            self.storage.resolve_wechat_flow_session(binding.owner_id, flow.flow_type, status="cancelled")
            return ConversationResponse(
                session_type=flow.flow_type,
                messages=[self._text("已取消当前微信任务收集流程。")],
                effect=ConversationEffect(clear_active_context=True),
            )
        route = self._route_general_intent(text=text, attachments=attachments)
        intent = str(route.get("intent") or "").strip()
        if intent in {TaskType.AI_SEARCH.value, FLOW_ANALYSIS, FLOW_REVIEW, FLOW_REPLY} and intent != flow.flow_type:
            self._set_pending_conversation_action(
                conversation,
                {
                    "type": "switch_context",
                    "target_intent": intent,
                    "target_label": self._intent_display_label(intent),
                    "text": text,
                    "attachments": self._serialize_attachments(attachments),
                    "route": route,
                },
            )
            return ConversationResponse(
                session_type=flow.flow_type,
                flow_session_id=flow.flow_session_id,
                messages=[self._text(f"当前正在进行{self._intent_display_label(flow.flow_type)}材料收集。回复“切换”可转到{self._intent_display_label(intent)}，回复“继续当前”保留当前流程。")],
            )
        if flow.flow_type in {FLOW_ANALYSIS, FLOW_REVIEW}:
            return await self._handle_patent_task_flow(binding, flow, text, attachments)
        if flow.flow_type == FLOW_REPLY:
            return await self._handle_reply_flow(binding, flow, text, attachments)
        raise HTTPException(status_code=400, detail="不支持的微信流程类型。")

    async def _handle_active_search_context(
        self,
        binding: WeChatBinding,
        conversation: WeChatConversationSession,
        active_context: ActiveContextRef,
        text: str,
        attachments: List[InternalWeChatInboundAttachment],
    ) -> ConversationResponse:
        session_id = str(active_context.session_id or "").strip()
        if not session_id:
            self._clear_active_context(conversation)
            return await self._handle_general_message(binding, conversation, text, attachments)
        if self._is_search_exit(text):
            self.pause_active_search_context(conversation)
            return ConversationResponse(messages=[self._text("已退出当前检索上下文，检索会话仍然保留。")], effect=ConversationEffect(clear_active_context=True))
        if self._is_search_cancel(text):
            self.cancel_current_search(binding.owner_id, session_id)
            return ConversationResponse(messages=[self._text("已取消当前检索会话。")], effect=ConversationEffect(clear_active_context=True))
        route = self._route_general_intent(text=text, attachments=attachments)
        intent = str(route.get("intent") or "").strip()
        if intent in {FLOW_ANALYSIS, FLOW_REVIEW, FLOW_REPLY}:
            self._set_pending_conversation_action(
                conversation,
                {
                    "type": "switch_context",
                    "target_intent": intent,
                    "target_label": self._intent_display_label(intent),
                    "text": text,
                    "attachments": self._serialize_attachments(attachments),
                    "route": route,
                },
            )
            return ConversationResponse(messages=[self._text(f"当前在检索《{active_context.title or session_id}》。回复“切换”可转到{self._intent_display_label(intent)}，回复“继续当前”保留当前检索。")])
        if intent == "chitchat":
            return ConversationResponse(messages=[self._text(self._search_context_hint_text(active_context.title))])
        return await self.send_ai_search_message(binding, conversation, session_id=session_id, text=text, attachments=attachments, route=route)

    def start_patent_analysis(
        self,
        binding: WeChatBinding,
        *,
        text: str,
        attachment: Optional[InternalWeChatInboundAttachment],
    ) -> ConversationResponse:
        task = self._create_patent_or_review_task(binding.owner_id, FLOW_ANALYSIS, text=text, attachment=attachment)
        return ConversationResponse(
            session_type=FLOW_ANALYSIS,
            task_id=task.id,
            messages=[self._text(f"已创建 AI 分析任务：{task.id}\n结果完成后会主动推送到当前微信。")],
        )

    def start_ai_review(
        self,
        binding: WeChatBinding,
        *,
        text: str,
        attachment: Optional[InternalWeChatInboundAttachment],
    ) -> ConversationResponse:
        task = self._create_patent_or_review_task(binding.owner_id, FLOW_REVIEW, text=text, attachment=attachment)
        return ConversationResponse(
            session_type=FLOW_REVIEW,
            task_id=task.id,
            messages=[self._text(f"已创建 AI 审查任务：{task.id}\n结果完成后会主动推送到当前微信。")],
        )

    def begin_guided_flow(self, binding: WeChatBinding, conversation: WeChatConversationSession, flow_type: str) -> ConversationResponse:
        self._cancel_all_active_flows(binding.owner_id, status="superseded")
        expires_at = utc_now() + timedelta(hours=12)
        if flow_type == FLOW_ANALYSIS:
            flow = self.storage.upsert_wechat_flow_session(binding.owner_id, FLOW_ANALYSIS, current_step="await_patent_input", draft_payload={}, expires_at=expires_at)
            return ConversationResponse(
                session_type=FLOW_ANALYSIS,
                flow_session_id=flow.flow_session_id,
                messages=[self._text("已开始 AI 分析任务创建。请发送专利号，或上传 1 份 PDF 文件。")],
                effect=ConversationEffect(active_context=ActiveContextRef(kind="guided_workflow", session_id=flow.flow_session_id, title="AI 分析")),
            )
        flow = self.storage.upsert_wechat_flow_session(binding.owner_id, FLOW_REVIEW, current_step="await_patent_input", draft_payload={}, expires_at=expires_at)
        return ConversationResponse(
            session_type=FLOW_REVIEW,
            flow_session_id=flow.flow_session_id,
            messages=[self._text("已开始 AI 审查任务创建。请发送专利号，或上传 1 份 PDF 文件。")],
            effect=ConversationEffect(active_context=ActiveContextRef(kind="guided_workflow", session_id=flow.flow_session_id, title="AI 审查")),
        )

    def begin_ai_reply_workflow(self, binding: WeChatBinding, conversation: WeChatConversationSession) -> ConversationResponse:
        self._cancel_all_active_flows(binding.owner_id, status="superseded")
        flow = self.storage.upsert_wechat_flow_session(
            binding.owner_id,
            FLOW_REPLY,
            current_step="await_office_action",
            draft_payload={"comparison_docs": []},
            expires_at=utc_now() + timedelta(hours=12),
        )
        return ConversationResponse(
            session_type=FLOW_REPLY,
            flow_session_id=flow.flow_session_id,
            messages=[self._text("已开始 AI 答复任务创建。\n第 1 步：请上传审查意见通知书。发送“取消当前流程”可取消。")],
            effect=ConversationEffect(active_context=ActiveContextRef(kind="guided_workflow", session_id=flow.flow_session_id, title="AI 答复")),
        )

    def cancel_active_workflow(self, binding: WeChatBinding, conversation: WeChatConversationSession) -> ConversationResponse:
        active_flow = self._get_active_flow(binding.owner_id)
        if active_flow:
            self.storage.resolve_wechat_flow_session(binding.owner_id, active_flow.flow_type, status="cancelled")
            return ConversationResponse(
                session_type=active_flow.flow_type,
                messages=[self._text("已取消当前微信任务收集流程。")],
                effect=ConversationEffect(clear_active_context=True),
            )
        active_context = self._active_context(conversation)
        if active_context.kind == "ai_search" and active_context.session_id:
            self.cancel_current_search(binding.owner_id, active_context.session_id)
            return ConversationResponse(
                session_type=TaskType.AI_SEARCH.value,
                messages=[self._text("已取消当前检索会话。")],
                effect=ConversationEffect(clear_active_context=True),
            )
        return ConversationResponse(messages=[self._text("当前没有进行中的微信流程或检索上下文。")])

    def pause_active_search_context(self, conversation: WeChatConversationSession) -> None:
        self._clear_active_context(conversation)

    def cancel_current_search(self, owner_id: str, session_id: str) -> None:
        task = self.storage.get_task(session_id)
        if not task or str(task.owner_id or "") != str(owner_id or "") or str(task.task_type or "") != TaskType.AI_SEARCH.value:
            raise HTTPException(status_code=404, detail="AI 检索会话不存在。")
        metadata = dict(getattr(task, "metadata", {}) if isinstance(getattr(task, "metadata", {}), dict) else {})
        ai_search_meta = dict(metadata.get("ai_search") or {})
        ai_search_meta["current_phase"] = "cancelled"
        metadata["ai_search"] = ai_search_meta
        self.storage.update_task(
            session_id,
            status=TaskStatus.CANCELLED,
            current_step="已取消",
            metadata=metadata,
            updated_at=utc_now(),
        )

    async def start_or_resume_ai_search(
        self,
        binding: WeChatBinding,
        conversation: WeChatConversationSession,
        *,
        text: str,
        attachments: List[InternalWeChatInboundAttachment],
    ) -> ConversationResponse:
        if attachments:
            return ConversationResponse(messages=[self._text("微信检索当前只支持文本需求，请先发送检索目标与约束条件。")])
        sessions = self.list_recent_ai_search_sessions(binding.owner_id)
        if self._looks_like_resume_request(text):
            if len(sessions) > 1:
                self._set_pending_conversation_action(
                    conversation,
                    {
                        "type": "choose_search_session",
                        "options": sessions[:3],
                    },
                )
                lines = ["你有多个未完成检索，请回复编号继续："]
                for index, item in enumerate(sessions[:3], start=1):
                    lines.append(f"{index}. {item['title']}")
                return ConversationResponse(messages=[self._text("\n".join(lines))])
            if len(sessions) == 1:
                session_id = sessions[0]["session_id"]
                snapshot = self.ai_search_service.get_snapshot(session_id, binding.owner_id)
                return ConversationResponse(
                    session_type=TaskType.AI_SEARCH.value,
                    task_id=session_id,
                    messages=self._build_ai_search_messages(snapshot),
                    effect=ConversationEffect(active_context=ActiveContextRef(kind="ai_search", session_id=session_id, title=sessions[0]["title"])),
                )
        created = self.ai_search_service.create_session(binding.owner_id)
        response = await self.send_ai_search_message(
            binding,
            conversation,
            session_id=created.sessionId,
            text=text,
            attachments=[],
            route={"intent": TaskType.AI_SEARCH.value, "confidence": 1.0},
        )
        response.effect = response.effect or ConversationEffect()
        response.effect.active_context = ActiveContextRef(kind="ai_search", session_id=created.sessionId, title=self._session_title(created.sessionId, binding.owner_id))
        return response

    async def send_ai_search_message(
        self,
        binding: WeChatBinding,
        conversation: WeChatConversationSession,
        *,
        session_id: str,
        text: str,
        attachments: List[InternalWeChatInboundAttachment],
        route: Dict[str, Any],
    ) -> ConversationResponse:
        if attachments:
            return ConversationResponse(messages=[self._text("微信检索当前只支持文本需求，请先发送文本。")])
        try:
            snapshot = self.ai_search_service.get_snapshot(session_id, binding.owner_id)
            pending_action = snapshot.conversation.get("pendingAction") if isinstance(snapshot.conversation, dict) else None
            action_type = str((pending_action or {}).get("actionType") or "").strip()
            normalized_text = str(text or "").strip()
            if normalized_text in SEARCH_CONTROL_TOKENS:
                if normalized_text == "确认计划":
                    plan_version = int((pending_action or {}).get("planVersion") or snapshot.plan.get("currentPlan", {}).get("planVersion") or snapshot.run.get("planVersion") or 0)
                    if plan_version <= 0:
                        raise HTTPException(status_code=409, detail="当前没有可确认的检索计划。")
                    await self.confirm_ai_search_plan(session_id, binding.owner_id, plan_version)
                elif normalized_text == "继续检索":
                    await self.continue_ai_search(session_id, binding.owner_id)
                else:
                    await self.complete_ai_search(session_id, binding.owner_id)
            elif normalized_text.startswith("选择 ") or normalized_text.startswith("送审 "):
                await self.select_ai_search_candidates(session_id, binding.owner_id, normalized_text)
            elif action_type == "question":
                question_id = str((pending_action or {}).get("questionId") or (pending_action or {}).get("question_id") or "").strip()
                if not question_id:
                    raise HTTPException(status_code=409, detail="当前追问缺少 questionId。")
                await self.answer_ai_search_question(session_id, binding.owner_id, question_id, normalized_text)
            elif action_type == "plan_confirmation" and normalized_text and not self._is_affirmative(normalized_text):
                await self.send_freeform_ai_search_message(session_id, binding.owner_id, normalized_text)
            else:
                await self.send_freeform_ai_search_message(session_id, binding.owner_id, normalized_text)
        except HTTPException as exc:
            return ConversationResponse(
                session_type=TaskType.AI_SEARCH.value,
                task_id=session_id,
                messages=[self._text(self._detail_to_text(exc.detail))],
                effect=ConversationEffect(active_context=ActiveContextRef(kind="ai_search", session_id=session_id, title=self._session_title(session_id, binding.owner_id))),
            )
        except Exception:
            logger.bind(task_id=session_id, task_type_label=TaskType.AI_SEARCH.value, stage="wechat_runtime").exception(
                "微信 AI 检索处理失败 owner_id={} binding_id={} peer_id={} text={!r}",
                binding.owner_id,
                binding.binding_id,
                binding.wechat_peer_id,
                text,
            )
            return ConversationResponse(
                session_type=TaskType.AI_SEARCH.value,
                task_id=session_id,
                messages=[self._text(self._ai_search_retry_text())],
                effect=ConversationEffect(active_context=ActiveContextRef(kind="ai_search", session_id=session_id, title=self._session_title(session_id, binding.owner_id))),
            )
        final_snapshot = self.ai_search_service.get_snapshot(session_id, binding.owner_id)
        return ConversationResponse(
            session_type=TaskType.AI_SEARCH.value,
            task_id=session_id,
            messages=self._build_ai_search_messages(final_snapshot),
            effect=ConversationEffect(active_context=ActiveContextRef(kind="ai_search", session_id=session_id, title=self._session_title(session_id, binding.owner_id))),
        )

    async def send_freeform_ai_search_message(self, session_id: str, owner_id: str, content: str) -> None:
        if not str(content or "").strip():
            return
        await self._drain(self.ai_search_service.stream_message(session_id, owner_id, content))

    async def answer_ai_search_question(self, session_id: str, owner_id: str, question_id: str, answer: str) -> None:
        await self._drain(self.ai_search_service.stream_answer(session_id, owner_id, question_id, answer))

    async def confirm_ai_search_plan(self, session_id: str, owner_id: str, plan_version: int) -> None:
        await self._drain(self.ai_search_service.stream_plan_confirmation(session_id, owner_id, plan_version))

    async def continue_ai_search(self, session_id: str, owner_id: str) -> None:
        await self._drain(self.ai_search_service.stream_decision_continue(session_id, owner_id))

    async def complete_ai_search(self, session_id: str, owner_id: str) -> None:
        await self._drain(self.ai_search_service.stream_decision_complete(session_id, owner_id))

    async def select_ai_search_candidates(self, session_id: str, owner_id: str, text: str) -> None:
        snapshot = self.ai_search_service.get_snapshot(session_id, owner_id)
        candidates = snapshot.retrieval.get("documents", {}).get("candidates", []) if isinstance(snapshot.retrieval, dict) else []
        indexes = [int(item) for item in re.findall(r"\d+", text) if int(item) > 0]
        selected_ids: List[str] = []
        for index in indexes:
            if 1 <= index <= len(candidates):
                document = candidates[index - 1]
                document_id = str(document.get("documentId") or document.get("document_id") or "").strip()
                if document_id:
                    selected_ids.append(document_id)
        if not selected_ids:
            raise HTTPException(status_code=400, detail="未识别到有效的候选文献编号。")
        plan_version = int(snapshot.run.get("planVersion") or snapshot.plan.get("currentPlan", {}).get("planVersion") or 0)
        if plan_version <= 0:
            raise HTTPException(status_code=409, detail="当前没有有效计划版本，无法继续。")
        self.ai_search_service.patch_selected_documents(session_id, owner_id, plan_version, selected_ids, [])
        await self._drain(self.ai_search_service.stream_feature_comparison(session_id, owner_id, plan_version))

    def list_recent_ai_search_sessions(self, owner_id: str) -> List[Dict[str, str]]:
        sessions = self.ai_search_service.list_sessions(owner_id).items
        items: List[Dict[str, str]] = []
        for item in sorted(sessions, key=lambda entry: str(entry.updatedAt or entry.createdAt or ""), reverse=True):
            status = str(item.status or "").strip()
            phase = str(item.phase or "").strip()
            if status in TERMINAL_TASK_STATUSES or phase in TERMINAL_TASK_STATUSES:
                continue
            items.append(
                {
                    "session_id": item.sessionId,
                    "title": str(item.title or item.sessionId).strip(),
                }
            )
        return items

    async def _handle_patent_task_flow(
        self,
        binding: WeChatBinding,
        flow: WeChatFlowSession,
        text: str,
        attachments: List[InternalWeChatInboundAttachment],
    ) -> ConversationResponse:
        if not text and not attachments:
            return ConversationResponse(
                session_type=flow.flow_type,
                flow_session_id=flow.flow_session_id,
                messages=[self._text("请发送专利号，或上传 1 份 PDF 文件。")],
            )
        if len(attachments) > 1:
            return ConversationResponse(
                session_type=flow.flow_type,
                flow_session_id=flow.flow_session_id,
                messages=[self._text("当前只支持上传 1 份 PDF 文件。")],
            )

        task = None
        try:
            task = self._create_patent_or_review_task(binding.owner_id, flow.flow_type, text=text, attachment=attachments[0] if attachments else None)
            self.storage.resolve_wechat_flow_session(binding.owner_id, flow.flow_type, status="completed")
            prompt = (
                f"已创建{'AI 分析' if flow.flow_type == FLOW_ANALYSIS else 'AI 审查'}任务：{task.id}\n"
                f"{'已接收 PDF 文件并开始处理。' if attachments else '已按专利号开始处理。'}\n"
                "结果完成后会主动推送到当前微信。"
            )
            return ConversationResponse(
                session_type=flow.flow_type,
                task_id=task.id,
                messages=[self._text(prompt)],
                effect=ConversationEffect(clear_active_context=True),
            )
        except HTTPException as exc:
            if task:
                self.task_manager.fail_task(task.id, f"微信创建任务失败：{exc.detail}")
            return ConversationResponse(
                session_type=flow.flow_type,
                flow_session_id=flow.flow_session_id,
                messages=[self._text(str(exc.detail))],
            )

    async def _handle_reply_flow(
        self,
        binding: WeChatBinding,
        flow: WeChatFlowSession,
        text: str,
        attachments: List[InternalWeChatInboundAttachment],
    ) -> ConversationResponse:
        draft = dict(flow.draft_payload or {})
        step = str(flow.current_step or "").strip() or "await_office_action"
        if step == "await_office_action":
            if not attachments:
                return ConversationResponse(session_type=FLOW_REPLY, flow_session_id=flow.flow_session_id, messages=[self._text("请先上传审查意见通知书。")])
            draft["office_action"] = self._single_attachment(attachments, "审查意见通知书")
            updated = self.storage.upsert_wechat_flow_session(binding.owner_id, FLOW_REPLY, current_step="await_response", draft_payload=draft, expires_at=utc_now() + timedelta(hours=12))
            return ConversationResponse(session_type=FLOW_REPLY, flow_session_id=updated.flow_session_id, messages=[self._text("已收到审查意见通知书。\n第 2 步：请上传意见陈述书。")])
        if step == "await_response":
            if not attachments:
                return ConversationResponse(session_type=FLOW_REPLY, flow_session_id=flow.flow_session_id, messages=[self._text("请上传意见陈述书。")])
            draft["response"] = self._single_attachment(attachments, "意见陈述书")
            updated = self.storage.upsert_wechat_flow_session(binding.owner_id, FLOW_REPLY, current_step="await_previous_claims", draft_payload=draft, expires_at=utc_now() + timedelta(hours=12))
            return ConversationResponse(session_type=FLOW_REPLY, flow_session_id=updated.flow_session_id, messages=[self._text("已收到意见陈述书。\n第 3 步：请上传上一版权利要求书，或回复“跳过”。")])
        if step == "await_previous_claims":
            if text == "跳过":
                draft["previous_claims"] = None
            elif attachments:
                draft["previous_claims"] = self._single_attachment(attachments, "上一版权利要求书")
            else:
                return ConversationResponse(session_type=FLOW_REPLY, flow_session_id=flow.flow_session_id, messages=[self._text("请上传上一版权利要求书，或回复“跳过”。")])
            updated = self.storage.upsert_wechat_flow_session(binding.owner_id, FLOW_REPLY, current_step="await_current_claims", draft_payload=draft, expires_at=utc_now() + timedelta(hours=12))
            return ConversationResponse(session_type=FLOW_REPLY, flow_session_id=updated.flow_session_id, messages=[self._text("第 4 步：请上传当前版权利要求书，或回复“跳过”。")])
        if step == "await_current_claims":
            if text == "跳过":
                draft["current_claims"] = None
            elif attachments:
                draft["current_claims"] = self._single_attachment(attachments, "当前版权利要求书")
            else:
                return ConversationResponse(session_type=FLOW_REPLY, flow_session_id=flow.flow_session_id, messages=[self._text("请上传当前版权利要求书，或回复“跳过”。")])
            updated = self.storage.upsert_wechat_flow_session(binding.owner_id, FLOW_REPLY, current_step="await_comparison_docs", draft_payload=draft, expires_at=utc_now() + timedelta(hours=12))
            return ConversationResponse(session_type=FLOW_REPLY, flow_session_id=updated.flow_session_id, messages=[self._text("第 5 步：请逐份上传对比文件。上传结束后回复“完成对比文件”。")])
        if step == "await_comparison_docs":
            comparison_docs = list(draft.get("comparison_docs") or [])
            if attachments:
                comparison_docs.extend([self._attachment_to_dict(item, "对比文件") for item in attachments])
                draft["comparison_docs"] = comparison_docs
                updated = self.storage.upsert_wechat_flow_session(binding.owner_id, FLOW_REPLY, current_step="await_comparison_docs", draft_payload=draft, expires_at=utc_now() + timedelta(hours=12))
                return ConversationResponse(
                    session_type=FLOW_REPLY,
                    flow_session_id=updated.flow_session_id,
                    messages=[self._text(f"已收到 {len(attachments)} 份对比文件，当前共 {len(comparison_docs)} 份。继续上传，或回复“完成对比文件”。")],
                )
            if text != "完成对比文件":
                return ConversationResponse(session_type=FLOW_REPLY, flow_session_id=flow.flow_session_id, messages=[self._text("请继续上传对比文件，结束时回复“完成对比文件”。")])
            updated = self.storage.upsert_wechat_flow_session(binding.owner_id, FLOW_REPLY, current_step="await_reply_start", draft_payload=draft, expires_at=utc_now() + timedelta(hours=12))
            return ConversationResponse(session_type=FLOW_REPLY, flow_session_id=updated.flow_session_id, messages=[self._text("第 6 步：材料已收齐。回复“开始答复”即可创建任务。")])
        if step == "await_reply_start":
            if text != "开始答复":
                return ConversationResponse(session_type=FLOW_REPLY, flow_session_id=flow.flow_session_id, messages=[self._text("请回复“开始答复”以创建 AI 答复任务。")])
            task = None
            try:
                task = self._create_ai_reply_task(binding.owner_id, draft)
                self.storage.resolve_wechat_flow_session(binding.owner_id, FLOW_REPLY, status="completed")
                return ConversationResponse(
                    session_type=FLOW_REPLY,
                    task_id=task.id,
                    messages=[self._text(f"已创建 AI 答复任务：{task.id}\n结果完成后会主动推送到当前微信。")],
                    effect=ConversationEffect(clear_active_context=True),
                )
            except HTTPException as exc:
                if task:
                    self.task_manager.fail_task(task.id, f"微信创建 AI 答复任务失败：{exc.detail}")
                return ConversationResponse(session_type=FLOW_REPLY, flow_session_id=flow.flow_session_id, messages=[self._text(str(exc.detail))])
        return ConversationResponse(session_type=FLOW_REPLY, flow_session_id=flow.flow_session_id, messages=[self._text("当前流程状态异常，请回复“取消当前流程”后重试。")])

    def _single_attachment(self, attachments: List[InternalWeChatInboundAttachment], label: str) -> Dict[str, Any]:
        if len(attachments) != 1:
            raise HTTPException(status_code=400, detail=f"{label}当前只支持上传 1 份文件。")
        return self._attachment_to_dict(attachments[0], label)

    def _attachment_to_dict(self, attachment: InternalWeChatInboundAttachment, label: str) -> Dict[str, Any]:
        stored_path = str(attachment.storedPath or "").strip()
        if not stored_path:
            raise HTTPException(status_code=400, detail=f"{label}文件路径无效。")
        path = Path(stored_path)
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=400, detail=f"{label}文件不存在。")
        return {
            "filename": str(attachment.filename or path.name).strip() or path.name,
            "stored_path": str(path),
            "content_type": str(attachment.contentType or "").strip() or None,
        }

    def _route_general_intent(self, *, text: str, attachments: List[InternalWeChatInboundAttachment]) -> Dict[str, Any]:
        normalized_text = str(text or "").strip()
        if not normalized_text:
            if attachments:
                return {"intent": "unknown", "confidence": 0.4, "requires_confirmation": True, "extracted": {}}
            return {"intent": "chitchat", "confidence": 0.2, "requires_confirmation": False, "extracted": {}}
        if normalized_text in CHITCHAT_TOKENS:
            return {"intent": "chitchat", "confidence": 0.95, "requires_confirmation": False, "extracted": {}}
        if self._is_search_cancel(normalized_text) or self._is_search_exit(normalized_text) or self._is_flow_cancel(normalized_text):
            return {"intent": "cancel_or_pause", "confidence": 0.98, "requires_confirmation": False, "extracted": {}}

        lower = normalized_text.lower()
        heuristics = [
            (TaskType.AI_SEARCH.value, ["检索", "现有技术", "prior art", "找专利", "检索一下", "查新"]),
            (FLOW_REPLY, ["答复", "审查意见", "oa", "office action"]),
            (FLOW_REVIEW, ["审查", "review"]),
            (FLOW_ANALYSIS, ["分析", "analysis", "拆解"]),
        ]
        for intent, tokens in heuristics:
            if any(token in lower for token in tokens):
                return {
                    "intent": intent,
                    "confidence": 0.9,
                    "requires_confirmation": False,
                    "extracted": {"patent_number": self._normalize_patent_number_candidate(normalized_text)},
                }
        try:
            response = self.llm_service.invoke_text_json(
                [
                    {
                        "role": "system",
                        "content": f"""你是微信专利助手的消息路由器。你只能输出 JSON。

把用户输入路由为以下 intent 之一：
1. {TaskType.AI_SEARCH.value}: 专利检索、找对比文献、查现有技术、继续检索
2. {FLOW_ANALYSIS}: 专利分析、技术方案解读
3. {FLOW_REVIEW}: 专利审查、找撰写问题、质量评估
4. {FLOW_REPLY}: 审查意见答复、OA 答复、意见陈述书
5. cancel_or_pause: 退出/暂停/取消当前上下文
6. chitchat: 寒暄、感谢、收到
7. unknown: 信息不足，无法安全判断

规则：
- 单个附件或单个专利号但无明确动作时，优先 unknown。
- 低把握时返回 requires_confirmation=true。
- 仅当文本中出现明确专利号时提取 patent_number，否则为 null。

输出格式：
{{
  "intent": "<{TaskType.AI_SEARCH.value}|{FLOW_ANALYSIS}|{FLOW_REVIEW}|{FLOW_REPLY}|cancel_or_pause|chitchat|unknown>",
  "confidence": <0到1浮点数>,
  "requires_confirmation": <boolean>,
  "extracted": {{"patent_number": "<string或null>"}}
}}""",
                    },
                    {
                        "role": "user",
                        "content": f'输入文本: "{normalized_text}"\n是否带附件: {"true" if attachments else "false"}\n仅返回 JSON。',
                    },
                ],
                task_kind="wechat_intent_routing_v1",
                temperature=0.0,
                max_tokens=256,
            )
            intent = str(response.get("intent") or "unknown").strip()
            if intent not in {TaskType.AI_SEARCH.value, FLOW_ANALYSIS, FLOW_REVIEW, FLOW_REPLY, "cancel_or_pause", "chitchat", "unknown"}:
                intent = "unknown"
            try:
                confidence = max(0.0, min(1.0, float(response.get("confidence") or 0.0)))
            except Exception:
                confidence = 0.0
            extracted = response.get("extracted") if isinstance(response.get("extracted"), dict) else {}
            patent_number = self._normalize_patent_number_candidate(extracted.get("patent_number") or normalized_text)
            return {
                "intent": intent,
                "confidence": confidence,
                "requires_confirmation": bool(response.get("requires_confirmation")),
                "extracted": {"patent_number": patent_number},
            }
        except Exception:
            return {"intent": "unknown", "confidence": 0.0, "requires_confirmation": True, "extracted": {}}

    def _normalize_patent_number_candidate(self, value: Any) -> Optional[str]:
        text = str(value or "").strip().upper()
        if not text:
            return None
        direct_match = PATENT_NUMBER_PATTERN.search(text)
        if direct_match:
            return direct_match.group(0).replace(" ", "")
        return None

    def _session_title(self, session_id: str, owner_id: str) -> Optional[str]:
        try:
            snapshot = self.ai_search_service.get_snapshot(session_id, owner_id)
            return str(snapshot.session.title or session_id).strip()
        except Exception:
            return session_id

    def _intent_display_label(self, intent: str) -> str:
        mapping = {
            TaskType.AI_SEARCH.value: "AI 检索",
            FLOW_ANALYSIS: "AI 分析",
            FLOW_REVIEW: "AI 审查",
            FLOW_REPLY: "AI 答复",
        }
        return mapping.get(str(intent or "").strip(), str(intent or "").strip() or "新任务")

    def _serialize_attachments(self, attachments: List[InternalWeChatInboundAttachment]) -> List[Dict[str, Any]]:
        return [
            {
                "filename": str(item.filename or "").strip(),
                "storedPath": str(item.storedPath or "").strip(),
                "contentType": str(item.contentType or "").strip() or None,
            }
            for item in attachments
            if str(item.storedPath or "").strip()
        ]

    def _is_affirmative(self, text: str) -> bool:
        normalized = str(text or "").strip().lower()
        return normalized in {token.lower() for token in YES_TOKENS}

    def _is_negative(self, text: str) -> bool:
        normalized = str(text or "").strip().lower()
        return normalized in {token.lower() for token in NO_TOKENS}

    def _is_flow_cancel(self, text: str) -> bool:
        normalized = str(text or "").strip()
        return normalized == "/cancel" or normalized in CANCEL_FLOW_TOKENS

    def _is_search_exit(self, text: str) -> bool:
        normalized = str(text or "").strip()
        return normalized in EXIT_SEARCH_TOKENS

    def _is_search_cancel(self, text: str) -> bool:
        normalized = str(text or "").strip()
        return normalized in CANCEL_SEARCH_TOKENS

    def _looks_like_resume_request(self, text: str) -> bool:
        normalized = str(text or "").strip()
        return any(token in normalized for token in SEARCH_RESUME_TOKENS)

    def _get_active_flow(self, owner_id: str) -> Optional[WeChatFlowSession]:
        for flow_type in ACTIVE_FLOW_TYPES:
            flow = self.storage.get_active_wechat_flow_session(owner_id, flow_type)
            if flow and str(flow.status or "").strip() == "active":
                return flow
        return None

    def _cancel_all_active_flows(self, owner_id: str, *, status: str = "cancelled") -> None:
        for flow_type in ACTIVE_FLOW_TYPES:
            self.storage.resolve_wechat_flow_session(owner_id, flow_type, status=status)

    def _validate_local_file_suffix(self, filename: str, allowed: set[str], label: str) -> None:
        suffix = Path(str(filename or "")).suffix.lower()
        if suffix not in allowed:
            allowed_text = "/".join(sorted(allowed))
            raise HTTPException(status_code=400, detail=f"{label}仅支持 {allowed_text} 格式。")

    def _copy_attachment_to_task(self, *, task_id: str, source_path: str, subdir: str, prefix: str, original_name: str) -> str:
        source = Path(str(source_path or "").strip())
        if not source.exists() or not source.is_file():
            raise HTTPException(status_code=400, detail=f"微信上传文件不存在：{source}")
        target_dir = settings.UPLOAD_DIR / task_id / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(original_name or source.name).name
        target = target_dir / f"{prefix}_{safe_name}"
        shutil.copy2(source, target)
        return str(target)

    def _create_patent_or_review_task(
        self,
        owner_id: str,
        task_type: str,
        *,
        text: str,
        attachment: Optional[InternalWeChatInboundAttachment],
    ) -> Any:
        patent_number = str(text or "").strip().upper() or None
        if not patent_number and not attachment:
            raise HTTPException(status_code=400, detail="必须提供专利号或上传 PDF 文件。")
        if patent_number and attachment:
            raise HTTPException(status_code=400, detail="请二选一：发送专利号，或上传 PDF 文件。")

        task = self.task_manager.create_task(
            owner_id=owner_id,
            task_type=task_type,
            pn=patent_number,
            title=patent_number or Path(str(attachment.filename if attachment else "")).stem or ("AI 审查任务" if task_type == FLOW_REVIEW else "AI 分析任务"),
        )
        metadata: Dict[str, Any] = {"task_type": task_type, "input_files": []}
        upload_file_path: Optional[str] = None
        saved_paths: List[str] = []
        try:
            if attachment:
                self._validate_local_file_suffix(attachment.filename, {".pdf"}, "专利文档")
                upload_file_path = self._copy_attachment_to_task(
                    task_id=task.id,
                    source_path=attachment.storedPath,
                    subdir="patent",
                    prefix="source",
                    original_name=attachment.filename,
                )
                saved_paths.append(upload_file_path)
                from backend.routes.tasks import _compute_file_sha256, _enqueue_pipeline_task

                sha256 = _compute_file_sha256(upload_file_path)
                metadata["input_files"].append(
                    {
                        "file_type": "patent_pdf",
                        "original_name": attachment.filename,
                        "stored_path": upload_file_path,
                        "sha256": sha256,
                    }
                )
                self.storage.update_task(task.id, metadata=metadata)
                _enqueue_pipeline_task(task, upload_file_path=upload_file_path, input_sha256=sha256)
            else:
                from backend.routes.tasks import _enqueue_pipeline_task

                self.storage.update_task(task.id, metadata=metadata)
                _enqueue_pipeline_task(task, upload_file_path=None, input_sha256=None)
            return task
        except HTTPException:
            for saved in saved_paths:
                _cleanup_path(saved)
            raise
        except Exception as exc:
            for saved in saved_paths:
                _cleanup_path(saved)
            raise HTTPException(status_code=500, detail=f"创建任务失败：{exc}") from exc

    def _create_ai_reply_task(self, owner_id: str, draft: Dict[str, Any]) -> Any:
        office_action = draft.get("office_action")
        response = draft.get("response")
        if not isinstance(office_action, dict) or not isinstance(response, dict):
            raise HTTPException(status_code=400, detail="AI 答复任务缺少必需材料。")

        task = self.task_manager.create_task(
            owner_id=owner_id,
            task_type=FLOW_REPLY,
            pn=None,
            title=Path(str(office_action.get("filename") or "AI 答复任务")).stem or "AI 答复任务",
        )
        input_files: List[Dict[str, str]] = []
        saved_paths: List[str] = []

        def copy_item(item: Dict[str, Any], file_type: str, subdir_prefix: str) -> Dict[str, str]:
            filename = str(item.get("filename") or "").strip()
            self._validate_local_file_suffix(filename, REPLY_ALLOWED_SUFFIXES, file_type)
            stored_path = self._copy_attachment_to_task(
                task_id=task.id,
                source_path=str(item.get("stored_path") or ""),
                subdir="office_action",
                prefix=subdir_prefix,
                original_name=filename,
            )
            saved_paths.append(stored_path)
            return {
                "file_type": file_type,
                "original_name": filename or Path(stored_path).name,
                "stored_path": stored_path,
            }

        try:
            input_files.append(copy_item(office_action, "office_action", "office_action"))
            input_files.append(copy_item(response, "response", "response"))
            if isinstance(draft.get("previous_claims"), dict):
                input_files.append(copy_item(draft["previous_claims"], "claims_previous", "claims_previous"))
            if isinstance(draft.get("current_claims"), dict):
                input_files.append(copy_item(draft["current_claims"], "claims_current", "claims_current"))
            for index, item in enumerate(list(draft.get("comparison_docs") or []), start=1):
                if isinstance(item, dict):
                    input_files.append(copy_item(item, "comparison_doc", f"comparison_{index}"))

            self.storage.update_task(task.id, metadata={"task_type": FLOW_REPLY, "input_files": input_files})
            from backend.routes.tasks import _enqueue_pipeline_task

            _enqueue_pipeline_task(task, input_files=input_files)
            return task
        except HTTPException:
            for saved in saved_paths:
                _cleanup_path(saved)
            raise
        except Exception as exc:
            for saved in saved_paths:
                _cleanup_path(saved)
            raise HTTPException(status_code=500, detail=f"创建 AI 答复任务失败：{exc}") from exc

    async def _drain(self, stream: Any) -> None:
        async for _chunk in stream:
            continue

    def _build_ai_search_messages(self, snapshot: Any) -> List[InternalWeChatOutboundMessage]:
        messages: List[InternalWeChatOutboundMessage] = []
        conversation = snapshot.conversation if isinstance(snapshot.conversation, dict) else {}
        visible_messages = conversation.get("messages") if isinstance(conversation.get("messages"), list) else []
        latest_assistant = ""
        for item in reversed(visible_messages):
            if str(item.get("role") or "").strip() == "assistant" and str(item.get("content") or "").strip():
                latest_assistant = str(item.get("content") or "").strip()
                break
        if latest_assistant:
            messages.append(self._text(latest_assistant))

        pending_action = conversation.get("pendingAction") if isinstance(conversation.get("pendingAction"), dict) else None
        action_type = str((pending_action or {}).get("actionType") or "").strip()
        if action_type == "question":
            prompt = str((pending_action or {}).get("prompt") or latest_assistant or "").strip()
            hint = "请直接回复补充信息。"
            messages.append(self._text(f"{prompt}\n{hint}" if prompt and prompt != latest_assistant else hint))
        elif action_type == "plan_confirmation":
            messages.append(self._text("如确认当前检索计划，请回复“确认计划”；如果要补充修改，直接回复你的意见即可。"))
        elif action_type == "human_decision":
            documents = snapshot.retrieval.get("documents", {}) if isinstance(snapshot.retrieval, dict) else {}
            candidates = documents.get("candidates") if isinstance(documents.get("candidates"), list) else []
            if candidates:
                lines = ["候选文献如下，请回复“选择 1 3”这类编号指令："]
                for index, item in enumerate(candidates, start=1):
                    title = str(item.get("title") or item.get("pn") or item.get("documentId") or item.get("document_id") or f"文献 {index}").strip()
                    lines.append(f"{index}. {title}")
                messages.append(self._text("\n".join(lines)))
            messages.append(self._text("如需继续扩展检索，请回复“继续检索”；如按当前结果结束，请回复“按当前结果完成”。"))

        artifacts = snapshot.artifacts if isinstance(snapshot.artifacts, dict) else {}
        download_url = str(artifacts.get("downloadUrl") or "").strip()
        if download_url and str(snapshot.run.get("status") or "").strip() == "completed":
            messages.append(self._text(f"检索结果已生成，可下载：{download_url}"))

        if not messages:
            messages.append(self._text("请直接描述检索目标、核心技术和约束条件。"))
        return messages

    def _detail_to_text(self, detail: Any) -> str:
        if isinstance(detail, dict):
            return str(detail.get("message") or detail.get("detail") or "请求失败").strip() or "请求失败"
        return str(detail or "请求失败").strip() or "请求失败"
