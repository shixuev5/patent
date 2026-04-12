"""WeChat IM runtime orchestration for bound private-chat users."""

from __future__ import annotations

import asyncio
import re
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from agents.common.utils.llm import get_llm_service
from backend.ai_search.service import AiSearchService
from backend.models import (
    InternalWeChatInboundAttachment,
    InternalWeChatInboundMessageResponse,
    InternalWeChatOutboundMessage,
)
from backend.storage import TaskType, WeChatBinding, WeChatFlowSession, get_pipeline_manager
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
        self.storage = getattr(resolved_task_manager, "storage", storage)
        self.ai_search_service = ai_search_service or AiSearchService()
        self.ai_search_service.task_manager = self.task_manager
        self.llm_service = get_llm_service()

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
        normalized_text = str(text or "").strip()
        normalized_attachments = [item for item in (attachments or []) if str(item.storedPath or "").strip()]

        if normalized_text == "/cancel":
            flow = self._get_active_flow(binding.owner_id)
            if flow:
                self.storage.resolve_wechat_flow_session(binding.owner_id, flow.flow_type, status="cancelled")
                return self._response(
                    binding,
                    session_type=flow.flow_type,
                    messages=[self._text("已取消当前微信任务收集流程。")],
                )
            return self._response(binding, messages=[self._text("当前没有进行中的微信任务流程。")])

        active_flow = self._get_active_flow(binding.owner_id)
        if active_flow:
            return await self._handle_flow_message(binding, active_flow, normalized_text, normalized_attachments)

        if normalized_text in {"/analysis new", "/review new", "/reply new"}:
            return self._start_flow(binding, normalized_text)

        routed = await self._handle_intent_routed_message(binding, normalized_text, normalized_attachments)
        if routed is not None:
            return routed

        return await self._handle_ai_search(binding, normalized_text, normalized_attachments)

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

    def _response(
        self,
        binding: WeChatBinding,
        *,
        session_type: Optional[str] = None,
        flow_session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        messages: Optional[List[InternalWeChatOutboundMessage]] = None,
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

    def _get_active_flow(self, owner_id: str) -> Optional[WeChatFlowSession]:
        for flow_type in ACTIVE_FLOW_TYPES:
            flow = self.storage.get_active_wechat_flow_session(owner_id, flow_type)
            if flow and str(flow.status or "").strip() == "active":
                return flow
        return None

    def _close_all_active_flows(self, owner_id: str, *, status: str = "cancelled") -> None:
        for flow_type in ACTIVE_FLOW_TYPES:
            self.storage.resolve_wechat_flow_session(owner_id, flow_type, status=status)

    def _start_flow(self, binding: WeChatBinding, command_text: str) -> InternalWeChatInboundMessageResponse:
        self._close_all_active_flows(binding.owner_id, status="superseded")
        expires_at = utc_now() + timedelta(hours=12)
        if command_text == "/analysis new":
            flow = self.storage.upsert_wechat_flow_session(
                binding.owner_id,
                FLOW_ANALYSIS,
                current_step="await_patent_input",
                draft_payload={},
                expires_at=expires_at,
            )
            return self._response(
                binding,
                session_type=FLOW_ANALYSIS,
                flow_session_id=flow.flow_session_id,
                messages=[self._text("已开始 AI 分析任务创建。请发送专利号，或上传 1 份 PDF 文件。")],
            )
        if command_text == "/review new":
            flow = self.storage.upsert_wechat_flow_session(
                binding.owner_id,
                FLOW_REVIEW,
                current_step="await_patent_input",
                draft_payload={},
                expires_at=expires_at,
            )
            return self._response(
                binding,
                session_type=FLOW_REVIEW,
                flow_session_id=flow.flow_session_id,
                messages=[self._text("已开始 AI 审查任务创建。请发送专利号，或上传 1 份 PDF 文件。")],
            )
        flow = self.storage.upsert_wechat_flow_session(
            binding.owner_id,
            FLOW_REPLY,
            current_step="await_office_action",
            draft_payload={"comparison_docs": []},
            expires_at=expires_at,
        )
        return self._response(
            binding,
            session_type=FLOW_REPLY,
            flow_session_id=flow.flow_session_id,
            messages=[
                self._text(
                    "已开始 AI 答复任务创建。\n"
                    "第 1 步：请上传审查意见通知书。发送 `/cancel` 可取消当前流程。"
                )
            ],
        )

    async def _handle_flow_message(
        self,
        binding: WeChatBinding,
        flow: WeChatFlowSession,
        text: str,
        attachments: List[InternalWeChatInboundAttachment],
    ) -> InternalWeChatInboundMessageResponse:
        if flow.flow_type in {FLOW_ANALYSIS, FLOW_REVIEW}:
            return await self._handle_patent_task_flow(binding, flow, text, attachments)
        if flow.flow_type == FLOW_REPLY:
            return await self._handle_reply_flow(binding, flow, text, attachments)
        raise HTTPException(status_code=400, detail="不支持的微信流程类型。")

    async def _handle_patent_task_flow(
        self,
        binding: WeChatBinding,
        flow: WeChatFlowSession,
        text: str,
        attachments: List[InternalWeChatInboundAttachment],
    ) -> InternalWeChatInboundMessageResponse:
        if not text and not attachments:
            return self._response(
                binding,
                session_type=flow.flow_type,
                flow_session_id=flow.flow_session_id,
                messages=[self._text("请发送专利号，或上传 1 份 PDF 文件。")],
            )
        if len(attachments) > 1:
            return self._response(
                binding,
                session_type=flow.flow_type,
                flow_session_id=flow.flow_session_id,
                messages=[self._text("当前只支持上传 1 份 PDF 文件。")],
            )

        task = None
        created_from_file = bool(attachments)
        try:
            task = self._create_patent_or_review_task(binding.owner_id, flow.flow_type, text=text, attachment=attachments[0] if attachments else None)
            self.storage.resolve_wechat_flow_session(binding.owner_id, flow.flow_type, status="completed")
            prompt = (
                f"已创建{'AI 分析' if flow.flow_type == FLOW_ANALYSIS else 'AI 审查'}任务：{task.id}\n"
                f"{'已接收 PDF 文件并开始处理。' if created_from_file else '已按专利号开始处理。'}\n"
                "结果完成后会主动推送到当前微信。"
            )
            return self._response(
                binding,
                session_type=flow.flow_type,
                task_id=task.id,
                messages=[self._text(prompt)],
            )
        except HTTPException as exc:
            if task:
                self.task_manager.fail_task(task.id, f"微信创建任务失败：{exc.detail}")
            return self._response(
                binding,
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
    ) -> InternalWeChatInboundMessageResponse:
        draft = dict(flow.draft_payload or {})
        step = str(flow.current_step or "").strip() or "await_office_action"

        if step == "await_office_action":
            if not attachments:
                return self._response(binding, session_type=FLOW_REPLY, flow_session_id=flow.flow_session_id, messages=[self._text("请先上传审查意见通知书。")])
            draft["office_action"] = self._single_attachment(attachments, "审查意见通知书")
            updated = self.storage.upsert_wechat_flow_session(binding.owner_id, FLOW_REPLY, current_step="await_response", draft_payload=draft, expires_at=utc_now() + timedelta(hours=12))
            return self._response(binding, session_type=FLOW_REPLY, flow_session_id=updated.flow_session_id, messages=[self._text("已收到审查意见通知书。\n第 2 步：请上传意见陈述书。")])

        if step == "await_response":
            if not attachments:
                return self._response(binding, session_type=FLOW_REPLY, flow_session_id=flow.flow_session_id, messages=[self._text("请上传意见陈述书。")])
            draft["response"] = self._single_attachment(attachments, "意见陈述书")
            updated = self.storage.upsert_wechat_flow_session(binding.owner_id, FLOW_REPLY, current_step="await_previous_claims", draft_payload=draft, expires_at=utc_now() + timedelta(hours=12))
            return self._response(binding, session_type=FLOW_REPLY, flow_session_id=updated.flow_session_id, messages=[self._text("已收到意见陈述书。\n第 3 步：请上传上一版权利要求书，或回复“跳过”。")])

        if step == "await_previous_claims":
            if text == "跳过":
                draft["previous_claims"] = None
            elif attachments:
                draft["previous_claims"] = self._single_attachment(attachments, "上一版权利要求书")
            else:
                return self._response(binding, session_type=FLOW_REPLY, flow_session_id=flow.flow_session_id, messages=[self._text("请上传上一版权利要求书，或回复“跳过”。")])
            updated = self.storage.upsert_wechat_flow_session(binding.owner_id, FLOW_REPLY, current_step="await_current_claims", draft_payload=draft, expires_at=utc_now() + timedelta(hours=12))
            return self._response(binding, session_type=FLOW_REPLY, flow_session_id=updated.flow_session_id, messages=[self._text("第 4 步：请上传当前版权利要求书，或回复“跳过”。")])

        if step == "await_current_claims":
            if text == "跳过":
                draft["current_claims"] = None
            elif attachments:
                draft["current_claims"] = self._single_attachment(attachments, "当前版权利要求书")
            else:
                return self._response(binding, session_type=FLOW_REPLY, flow_session_id=flow.flow_session_id, messages=[self._text("请上传当前版权利要求书，或回复“跳过”。")])
            updated = self.storage.upsert_wechat_flow_session(binding.owner_id, FLOW_REPLY, current_step="await_comparison_docs", draft_payload=draft, expires_at=utc_now() + timedelta(hours=12))
            return self._response(binding, session_type=FLOW_REPLY, flow_session_id=updated.flow_session_id, messages=[self._text("第 5 步：请逐份上传对比文件。上传结束后回复“完成对比文件”。")])

        if step == "await_comparison_docs":
            comparison_docs = list(draft.get("comparison_docs") or [])
            if attachments:
                comparison_docs.extend([self._attachment_to_dict(item, "对比文件") for item in attachments])
                draft["comparison_docs"] = comparison_docs
                updated = self.storage.upsert_wechat_flow_session(binding.owner_id, FLOW_REPLY, current_step="await_comparison_docs", draft_payload=draft, expires_at=utc_now() + timedelta(hours=12))
                return self._response(
                    binding,
                    session_type=FLOW_REPLY,
                    flow_session_id=updated.flow_session_id,
                    messages=[self._text(f"已收到 {len(attachments)} 份对比文件，当前共 {len(comparison_docs)} 份。继续上传，或回复“完成对比文件”。")],
                )
            if text != "完成对比文件":
                return self._response(binding, session_type=FLOW_REPLY, flow_session_id=flow.flow_session_id, messages=[self._text("请继续上传对比文件，结束时回复“完成对比文件”。")])
            updated = self.storage.upsert_wechat_flow_session(binding.owner_id, FLOW_REPLY, current_step="await_reply_start", draft_payload=draft, expires_at=utc_now() + timedelta(hours=12))
            return self._response(binding, session_type=FLOW_REPLY, flow_session_id=updated.flow_session_id, messages=[self._text("第 6 步：材料已收齐。回复“开始答复”即可创建任务。")])

        if step == "await_reply_start":
            if text != "开始答复":
                return self._response(binding, session_type=FLOW_REPLY, flow_session_id=flow.flow_session_id, messages=[self._text("请回复“开始答复”以创建 AI 答复任务。")])
            task = None
            try:
                task = self._create_ai_reply_task(binding.owner_id, draft)
                self.storage.resolve_wechat_flow_session(binding.owner_id, FLOW_REPLY, status="completed")
                return self._response(
                    binding,
                    session_type=FLOW_REPLY,
                    task_id=task.id,
                    messages=[self._text(f"已创建 AI 答复任务：{task.id}\n结果完成后会主动推送到当前微信。")],
                )
            except HTTPException as exc:
                if task:
                    self.task_manager.fail_task(task.id, f"微信创建 AI 答复任务失败：{exc.detail}")
                return self._response(binding, session_type=FLOW_REPLY, flow_session_id=flow.flow_session_id, messages=[self._text(str(exc.detail))])

        return self._response(binding, session_type=FLOW_REPLY, flow_session_id=flow.flow_session_id, messages=[self._text("当前微信流程状态异常，请回复 `/cancel` 后重试。")])

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

    async def _handle_intent_routed_message(
        self,
        binding: WeChatBinding,
        text: str,
        attachments: List[InternalWeChatInboundAttachment],
    ) -> Optional[InternalWeChatInboundMessageResponse]:
        if not text and not attachments:
            return None
        route = self._classify_intent(text=text, attachments=attachments)
        intent = str(route.get("intent") or "").strip()
        confidence = float(route.get("confidence") or 0.0)
        if intent in {"unknown", ""}:
            if attachments:
                return self._response(
                    binding,
                    messages=[self._text("已收到文件。请说明你要做什么，例如“分析这个专利”或“审查这个专利”，也可以直接发送 /analysis new 或 /review new。")],
                )
            return None
        if bool(route.get("requires_confirmation")) or confidence < 0.62:
            return self._response(
                binding,
                messages=[self._text("我还不能确定你的意图。你是要：AI 检索、AI 分析、AI 审查，还是 AI 答复？\n也可以直接发送 /analysis new、/review new、/reply new。")],
            )

        extracted = route.get("extracted") if isinstance(route.get("extracted"), dict) else {}
        patent_number = self._normalize_patent_number_candidate(extracted.get("patent_number") or text)
        if intent == FLOW_ANALYSIS:
            if attachments or patent_number:
                task = self._create_patent_or_review_task(binding.owner_id, FLOW_ANALYSIS, text=patent_number or "", attachment=attachments[0] if attachments else None)
                return self._response(
                    binding,
                    session_type=FLOW_ANALYSIS,
                    task_id=task.id,
                    messages=[self._text(f"已根据你的消息创建 AI 分析任务：{task.id}\n结果完成后会主动推送到当前微信。")],
                )
            return self._start_flow(binding, "/analysis new")
        if intent == FLOW_REVIEW:
            if attachments or patent_number:
                task = self._create_patent_or_review_task(binding.owner_id, FLOW_REVIEW, text=patent_number or "", attachment=attachments[0] if attachments else None)
                return self._response(
                    binding,
                    session_type=FLOW_REVIEW,
                    task_id=task.id,
                    messages=[self._text(f"已根据你的消息创建 AI 审查任务：{task.id}\n结果完成后会主动推送到当前微信。")],
                )
            return self._start_flow(binding, "/review new")
        if intent == FLOW_REPLY:
            return self._start_flow(binding, "/reply new")
        if intent == TaskType.AI_SEARCH.value:
            return None
        return None

    def _normalize_patent_number_candidate(self, value: Any) -> Optional[str]:
        text = str(value or "").strip().upper()
        if not text:
            return None
        direct_match = PATENT_NUMBER_PATTERN.search(text)
        if direct_match:
            return direct_match.group(0).replace(" ", "")
        return None

    def _classify_intent(self, *, text: str, attachments: List[InternalWeChatInboundAttachment]) -> Dict[str, Any]:
        normalized_text = str(text or "").strip()
        if not normalized_text:
            if attachments:
                return {"intent": "unknown", "confidence": 0.4, "requires_confirmation": True, "extracted": {}}
            return {"intent": TaskType.AI_SEARCH.value, "confidence": 0.0, "requires_confirmation": False, "extracted": {}}

        lower = normalized_text.lower()
        if any(token in lower for token in ["检索", "找专利", "现有技术", "prior art", "search"]):
            return {
                "intent": TaskType.AI_SEARCH.value,
                "confidence": 0.9,
                "requires_confirmation": False,
                "extracted": {"patent_number": self._normalize_patent_number_candidate(normalized_text)},
            }

        try:
            response = self.llm_service.invoke_text_json(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是微信任务入口的意图识别器。"
                            "只输出 JSON，不要输出解释。"
                            "可选 intent: ai_search, patent_analysis, ai_review, ai_reply, unknown。"
                            "如果用户只是普通检索、查现有技术、找对比文献，intent=ai_search。"
                            "如果用户要分析专利报告，intent=patent_analysis。"
                            "如果用户要专利审查/审查报告，intent=ai_review。"
                            "如果用户要答复审查意见、意见陈述、OA reply，intent=ai_reply。"
                            "若不确定，intent=unknown 且 requires_confirmation=true。"
                            "返回字段: intent, confidence(0-1), requires_confirmation(boolean), extracted{patent_number|null}。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"用户消息: {normalized_text}\n"
                            f"是否带附件: {'yes' if attachments else 'no'}\n"
                            "请返回 JSON。"
                        ),
                    },
                ],
                task_kind="wechat_intent_routing",
                temperature=0.0,
                max_tokens=256,
            )
            intent = str(response.get("intent") or "unknown").strip()
            if intent not in {TaskType.AI_SEARCH.value, FLOW_ANALYSIS, FLOW_REVIEW, FLOW_REPLY, "unknown"}:
                intent = "unknown"
            confidence = response.get("confidence")
            try:
                confidence_value = max(0.0, min(1.0, float(confidence)))
            except Exception:
                confidence_value = 0.0
            extracted = response.get("extracted") if isinstance(response.get("extracted"), dict) else {}
            patent_number = self._normalize_patent_number_candidate(extracted.get("patent_number") or normalized_text)
            return {
                "intent": intent,
                "confidence": confidence_value,
                "requires_confirmation": bool(response.get("requires_confirmation")),
                "extracted": {"patent_number": patent_number},
            }
        except Exception:
            if any(token in normalized_text for token in ["答复", "审查意见", "OA", "office action"]):
                return {"intent": FLOW_REPLY, "confidence": 0.75, "requires_confirmation": False, "extracted": {"patent_number": None}}
            if any(token in normalized_text for token in ["审查", "review"]):
                return {
                    "intent": FLOW_REVIEW,
                    "confidence": 0.72,
                    "requires_confirmation": False,
                    "extracted": {"patent_number": self._normalize_patent_number_candidate(normalized_text)},
                }
            if any(token in normalized_text for token in ["分析", "analysis"]):
                return {
                    "intent": FLOW_ANALYSIS,
                    "confidence": 0.72,
                    "requires_confirmation": False,
                    "extracted": {"patent_number": self._normalize_patent_number_candidate(normalized_text)},
                }
            return {"intent": TaskType.AI_SEARCH.value, "confidence": 0.55, "requires_confirmation": False, "extracted": {"patent_number": None}}

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

    async def _handle_ai_search(
        self,
        binding: WeChatBinding,
        text: str,
        attachments: List[InternalWeChatInboundAttachment],
    ) -> InternalWeChatInboundMessageResponse:
        if attachments:
            return self._response(binding, session_type=TaskType.AI_SEARCH.value, messages=[self._text("当前版本暂不支持在微信检索会话中直接上传文件，请先发送文本需求。")])
        if not text:
            return self._response(binding, session_type=TaskType.AI_SEARCH.value, messages=[self._text("请直接发送检索需求，或使用 `/analysis new`、`/review new`、`/reply new`。")])

        session_id = await self._resolve_ai_search_session(binding.owner_id)
        snapshot = self.ai_search_service.get_snapshot(session_id, binding.owner_id)
        pending_action = snapshot.conversation.get("pendingAction") if isinstance(snapshot.conversation, dict) else None
        action_type = str((pending_action or {}).get("actionType") or "").strip()
        try:
            if text == "确认计划":
                plan_version = int((pending_action or {}).get("planVersion") or snapshot.plan.get("currentPlan", {}).get("planVersion") or snapshot.run.get("planVersion") or 0)
                if plan_version <= 0:
                    raise HTTPException(status_code=409, detail="当前没有可确认的检索计划。")
                await self._drain(self.ai_search_service.stream_plan_confirmation(session_id, binding.owner_id, plan_version))
            elif text == "继续检索":
                await self._drain(self.ai_search_service.stream_decision_continue(session_id, binding.owner_id))
            elif text == "按当前结果完成":
                await self._drain(self.ai_search_service.stream_decision_complete(session_id, binding.owner_id))
            elif text.startswith("选择 "):
                await self._handle_selection_text(session_id, binding.owner_id, text)
            elif action_type == "question":
                question_id = str((pending_action or {}).get("questionId") or (pending_action or {}).get("question_id") or "").strip()
                if not question_id:
                    raise HTTPException(status_code=409, detail="当前追问缺少 questionId，无法继续。")
                await self._drain(self.ai_search_service.stream_answer(session_id, binding.owner_id, question_id, text))
            else:
                await self._drain(self.ai_search_service.stream_message(session_id, binding.owner_id, text))
        except HTTPException as exc:
            return self._response(binding, session_type=TaskType.AI_SEARCH.value, task_id=session_id, messages=[self._text(self._detail_to_text(exc.detail))])

        final_snapshot = self.ai_search_service.get_snapshot(session_id, binding.owner_id)
        final_snapshot = await self._auto_select_small_candidate_set(session_id, binding.owner_id, final_snapshot)
        return self._response(
            binding,
            session_type=TaskType.AI_SEARCH.value,
            task_id=session_id,
            messages=self._build_ai_search_messages(final_snapshot),
        )

    async def _resolve_ai_search_session(self, owner_id: str) -> str:
        sessions = self.ai_search_service.list_sessions(owner_id).items
        if sessions:
            ordered = sorted(
                sessions,
                key=lambda item: str(item.updatedAt or item.createdAt or ""),
                reverse=True,
            )
            latest = ordered[0]
            if str(latest.phase or "").strip() not in {"completed", "failed", "cancelled"}:
                return latest.sessionId
        created = self.ai_search_service.create_session(owner_id)
        return created.sessionId

    async def _drain(self, stream: Any) -> None:
        async for _chunk in stream:
            continue

    async def _handle_selection_text(self, session_id: str, owner_id: str, text: str) -> None:
        snapshot = self.ai_search_service.get_snapshot(session_id, owner_id)
        candidates = snapshot.retrieval.get("documents", {}).get("candidates", []) if isinstance(snapshot.retrieval, dict) else []
        indexes = [
            int(item)
            for item in text.replace("选择", "", 1).strip().split()
            if str(item).isdigit() and int(item) > 0
        ]
        selected_ids: List[str] = []
        for index in indexes:
            if 1 <= index <= len(candidates):
                document_id = str(candidates[index - 1].get("documentId") or candidates[index - 1].get("document_id") or "").strip()
                if document_id:
                    selected_ids.append(document_id)
        if not selected_ids:
            raise HTTPException(status_code=400, detail="未识别到有效的候选文献编号。")
        patched = self.ai_search_service.patch_selected_documents(session_id, owner_id, int(snapshot.run.get("planVersion") or snapshot.plan.get("currentPlan", {}).get("planVersion") or 0), selected_ids, [])
        plan_version = int(patched.run.get("planVersion") or patched.plan.get("currentPlan", {}).get("planVersion") or 0)
        if plan_version <= 0:
            raise HTTPException(status_code=409, detail="当前没有有效计划版本，无法生成特征对比。")
        await self._drain(self.ai_search_service.stream_feature_comparison(session_id, owner_id, plan_version))

    async def _auto_select_small_candidate_set(self, session_id: str, owner_id: str, snapshot: Any) -> Any:
        retrieval = snapshot.retrieval if isinstance(snapshot.retrieval, dict) else {}
        documents = retrieval.get("documents") if isinstance(retrieval.get("documents"), dict) else {}
        candidates = documents.get("candidates") if isinstance(documents.get("candidates"), list) else []
        selected = documents.get("selected") if isinstance(documents.get("selected"), list) else []
        if selected or not candidates or len(candidates) > 3:
            return snapshot
        plan_version = int(snapshot.run.get("planVersion") or snapshot.plan.get("currentPlan", {}).get("planVersion") or 0)
        if plan_version <= 0:
            return snapshot
        selected_ids = [
            str(item.get("documentId") or item.get("document_id") or "").strip()
            for item in candidates
            if str(item.get("documentId") or item.get("document_id") or "").strip()
        ]
        if not selected_ids:
            return snapshot
        self.ai_search_service.patch_selected_documents(session_id, owner_id, plan_version, selected_ids, [])
        await self._drain(self.ai_search_service.stream_feature_comparison(session_id, owner_id, plan_version))
        return self.ai_search_service.get_snapshot(session_id, owner_id)

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
            messages.append(self._text("如确认当前检索计划，请回复“确认计划”。"))
        elif action_type == "human_decision":
            documents = snapshot.retrieval.get("documents", {}) if isinstance(snapshot.retrieval, dict) else {}
            candidates = documents.get("candidates") if isinstance(documents.get("candidates"), list) else []
            if candidates:
                lines = ["候选文献如下，请回复“选择 1 3 5”这类编号指令："]
                for index, item in enumerate(candidates, start=1):
                    title = str(item.get("title") or item.get("pn") or item.get("documentId") or item.get("document_id") or f"文献 {index}").strip()
                    lines.append(f"{index}. {title}")
                messages.append(self._text("\n".join(lines)))
            messages.append(self._text("如需继续扩展检索，请回复“继续检索”；如按当前结果结束，请回复“按当前结果完成”。"))

        artifacts = snapshot.artifacts if isinstance(snapshot.artifacts, dict) else {}
        download_url = str(artifacts.get("downloadUrl") or "").strip()
        if download_url and str(snapshot.run.get("status") or "").strip() == "completed":
            messages.append(self._text(f"检索结果已生成，可下载：{download_url}"))

        return messages or [self._text("已处理微信检索消息。")]

    def _detail_to_text(self, detail: Any) -> str:
        if isinstance(detail, dict):
            return str(detail.get("message") or detail.get("detail") or "请求失败").strip() or "请求失败"
        return str(detail or "请求失败").strip() or "请求失败"
