# src/llm.py
import base64
import json
import time
from typing import Optional, List, Dict, Any, Callable
from urllib.parse import urlsplit

from openai import OpenAI
from loguru import logger

from config import settings
from backend.system_logs import emit_system_log
from backend.task_usage_tracking import get_current_task_usage_context, record_llm_usage


class LLMService:
    """统一的 LLM 服务类，提供文本和视觉模型的调用接口"""

    _THINKING_BUDGET = 4096
    _MAX_RETRY_ATTEMPTS = 3
    _RETRY_BASE_DELAY_SECONDS = 1.0
    _RETRY_MAX_DELAY_SECONDS = 8.0
    _RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
    _RETRYABLE_ERROR_MARKERS = (
        "too many requests",
        "throttle",
        "throttl",
        "rate limit",
        "serviceunavailable",
        "service unavailable",
        "internalerror.algo",
        "capacity limits",
        "temporarily unavailable",
    )
    _EXPLICIT_CACHE_TASK_KINDS = {
        "vision_ocr_correction",
        "vision_single_figure_explain",
        "vision_multi_figure_synthesis",
    }

    _TASK_POLICY_MAP: Dict[str, Dict[str, Any]] = {
        "patent_structuring_extract": {"tier": "default", "thinking": True},
        "knowledge_extract": {"tier": "default", "thinking": True},
        "retrieval_query_planning": {"tier": "default", "thinking": True},
        "search_matrix_reasoning": {"tier": "large", "thinking": True},
        "semantic_query_rewrite": {"tier": "default", "thinking": True},
        "core_summary_generation": {"tier": "default", "thinking": True},
        "claim_feature_reasoning": {"tier": "large", "thinking": True},
        "technical_means_generation": {"tier": "large", "thinking": True},
        "technical_effect_verification": {"tier": "large", "thinking": True},
        "oar_dispute_extraction": {"tier": "large", "thinking": True},
        "oar_amendment_tracking": {"tier": "large", "thinking": True},
        "oar_support_basis_check": {"tier": "large", "thinking": True},
        "oar_evidence_verification": {"tier": "large", "thinking": True},
        "oar_common_knowledge_verification": {"tier": "large", "thinking": True},
        "oar_topup_search_verification": {"tier": "large", "thinking": True},
        "vision_ocr_correction": {"tier": "default", "thinking": True},
        "vision_single_figure_explain": {"tier": "default", "thinking": True},
        "vision_multi_figure_synthesis": {"tier": "large", "thinking": True},
    }

    _JSON_PARSE_ERROR = "Model output is not valid JSON"

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        """
        初始化 LLM 服务。

        Args:
            api_key: 可选，指定的 API Key。如果不传，则使用 config.settings.LLM_API_KEY
            base_url: 可选，指定的 Base URL。如果不传，则使用 config.settings.LLM_BASE_URL
        """
        final_api_key = api_key or settings.LLM_API_KEY
        final_base_url = base_url or settings.LLM_BASE_URL
        self._text_interface = self._build_interface_fields(final_base_url)
        self._vision_interface = self._build_interface_fields(settings.VLM_BASE_URL)

        # 文本模型客户端
        self.text_client = OpenAI(api_key=final_api_key, base_url=final_base_url)

        # 视觉模型客户端
        self.vlm_client = OpenAI(
            api_key=settings.VLM_API_KEY, base_url=settings.VLM_BASE_URL
        )

    @staticmethod
    def _build_interface_fields(base_url: Optional[str]) -> Dict[str, Optional[str]]:
        raw_base_url = str(base_url or "").strip()
        default_fields = {
            "method": "POST",
            "path": "/v1/chat/completions",
            "target_host": None,
        }
        if not raw_base_url:
            return default_fields

        try:
            parts = urlsplit(raw_base_url)
        except Exception:
            return default_fields

        host = str(parts.netloc or "").strip() or None
        path = str(parts.path or "").strip()
        normalized_path = f"/{path.strip('/')}" if path else ""
        if normalized_path.endswith("/chat/completions"):
            request_path = normalized_path
        else:
            request_path = f"{normalized_path or '/v1'}/chat/completions"
        return {
            "method": "POST",
            "path": request_path,
            "target_host": host,
        }

    @classmethod
    def _resolve_policy(cls, task_kind: str) -> Dict[str, Any]:
        key = str(task_kind or "").strip()
        if not key:
            raise ValueError("task_kind is required")

        policy = cls._TASK_POLICY_MAP.get(key)
        if not policy:
            allowed = ", ".join(sorted(cls._TASK_POLICY_MAP.keys()))
            raise ValueError(f"Unsupported task_kind: {key}. Allowed: {allowed}")
        return {"task_kind": key, **policy}

    @staticmethod
    def _resolve_text_model(tier: str) -> str:
        if tier == "default":
            model = str(settings.LLM_MODEL_DEFAULT or "").strip()
            env_name = "LLM_MODEL_DEFAULT"
        elif tier == "large":
            model = str(settings.LLM_MODEL_LARGE or "").strip()
            env_name = "LLM_MODEL_LARGE"
        else:
            raise ValueError(f"Unsupported text model tier: {tier}")

        if not model:
            raise RuntimeError(f"Missing environment variable: {env_name}")
        return model

    @staticmethod
    def _resolve_vision_model(tier: str) -> str:
        if tier == "default":
            model = str(settings.VLM_MODEL_DEFAULT or "").strip()
            env_name = "VLM_MODEL_DEFAULT"
        elif tier == "large":
            model = str(settings.VLM_MODEL_LARGE or "").strip()
            env_name = "VLM_MODEL_LARGE"
        else:
            raise ValueError(f"Unsupported vision model tier: {tier}")

        if not model:
            raise RuntimeError(f"Missing environment variable: {env_name}")
        return model

    @staticmethod
    def _message_content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if isinstance(item, dict):
                    item_type = item.get("type")
                    if item_type == "text":
                        parts.append(str(item.get("text", "")))
                    elif item_type == "image_url":
                        parts.append("[image_url]")
                    else:
                        parts.append(str(item))
                else:
                    parts.append(str(item))
            return "\n".join([p for p in parts if p])
        return str(content)

    def _collect_prompt_summary(
        self, messages: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        system_parts: List[str] = []
        user_parts: List[str] = []
        for message in messages:
            role = str(message.get("role", ""))
            content = self._message_content_to_text(message.get("content", ""))
            if role == "system":
                system_parts.append(content)
            elif role == "user":
                user_parts.append(content)

        system_prompt = "\n\n".join(system_parts).strip()
        user_prompt = "\n\n".join(user_parts).strip()
        return {
            "system_prompt_chars": len(system_prompt),
            "user_prompt_chars": len(user_prompt),
        }

    @staticmethod
    def _current_task_log_context() -> Dict[str, Optional[str]]:
        context = get_current_task_usage_context()
        return {
            "owner_id": context.get("owner_id"),
            "task_id": context.get("task_id"),
            "task_type": context.get("task_type"),
        }

    @staticmethod
    def _get_usage_summary(response: Any) -> Dict[str, Optional[int]]:
        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)
        if prompt_tokens is None:
            prompt_tokens = getattr(usage, "input_tokens", None)
        if completion_tokens is None:
            completion_tokens = getattr(usage, "output_tokens", None)
        if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
            total_tokens = int(prompt_tokens) + int(completion_tokens)
        prompt_details = getattr(usage, "prompt_tokens_details", None)
        input_details = getattr(usage, "input_tokens_details", None)
        cached_tokens = LLMService._get_usage_detail_int(prompt_details, "cached_tokens")
        if cached_tokens is None:
            cached_tokens = LLMService._get_usage_detail_int(input_details, "cached_tokens")
        cache_creation_input_tokens = LLMService._get_usage_detail_int(
            prompt_details, "cache_creation_input_tokens"
        )
        if cache_creation_input_tokens is None:
            cache_creation_input_tokens = LLMService._get_usage_detail_int(
                input_details, "cache_creation_input_tokens"
            )
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "reasoning_tokens": getattr(
                getattr(usage, "completion_tokens_details", None),
                "reasoning_tokens",
                None,
            ),
            "cached_tokens": cached_tokens,
            "cache_creation_input_tokens": cache_creation_input_tokens,
        }

    @staticmethod
    def _get_usage_detail_int(container: Any, key: str) -> Optional[int]:
        if container is None:
            return None
        if isinstance(container, dict):
            value = container.get(key)
        else:
            value = getattr(container, key, None)
        if value is None:
            return None
        try:
            return int(value)
        except Exception:
            return None

    @staticmethod
    def _report_usage(model: str, usage_summary: Dict[str, Optional[int]]) -> None:
        try:
            record_llm_usage(
                model=model,
                prompt_tokens=int(usage_summary.get("prompt_tokens") or 0),
                completion_tokens=int(usage_summary.get("completion_tokens") or 0),
                total_tokens=int(usage_summary.get("total_tokens") or 0),
                reasoning_tokens=int(usage_summary.get("reasoning_tokens") or 0),
            )
        except Exception as exc:
            logger.debug(f"[LLM] 上报用量已跳过：{exc}")

    @staticmethod
    def _extract_reasoning_text(response: Any) -> str:
        try:
            message = response.choices[0].message
        except Exception:
            return ""

        reasoning_content = getattr(message, "reasoning_content", None)
        if isinstance(reasoning_content, str):
            return reasoning_content.strip()
        if isinstance(reasoning_content, list):
            chunks: List[str] = []
            for item in reasoning_content:
                if isinstance(item, str):
                    chunks.append(item)
                elif isinstance(item, dict):
                    if item.get("type") == "text":
                        chunks.append(str(item.get("text", "")))
                    else:
                        chunks.append(str(item))
                else:
                    chunks.append(str(item))
            return "\n".join([c for c in chunks if c]).strip()

        reasoning = getattr(message, "reasoning", None)
        if isinstance(reasoning, str):
            return reasoning.strip()
        return ""

    @classmethod
    def _build_thinking_extra_body(cls, thinking: bool) -> Dict[str, Any]:
        if not thinking:
            return {"enable_thinking": False}
        return {
            "enable_thinking": True,
            "thinking_budget": cls._THINKING_BUDGET,
        }

    @classmethod
    def _extract_status_code(cls, exc: Exception) -> Optional[int]:
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int):
            return status_code

        response = getattr(exc, "response", None)
        response_status_code = getattr(response, "status_code", None)
        if isinstance(response_status_code, int):
            return response_status_code
        return None

    @classmethod
    def _is_retryable_error(cls, exc: Exception) -> bool:
        status_code = cls._extract_status_code(exc)
        if status_code in cls._RETRYABLE_STATUS_CODES:
            return True

        message = str(exc or "").lower()
        if not message:
            return False
        return any(marker in message for marker in cls._RETRYABLE_ERROR_MARKERS)

    @classmethod
    def _retry_delay_seconds(cls, attempt: int) -> float:
        delay = cls._RETRY_BASE_DELAY_SECONDS * (2 ** max(0, attempt - 1))
        return min(cls._RETRY_MAX_DELAY_SECONDS, delay)

    def _call_with_retry(
        self,
        *,
        fn: Callable[[], Any],
        model: str,
        task_kind: str,
        event_name_prefix: str,
        task_context: Dict[str, Optional[str]],
        interface_fields: Dict[str, Optional[str]],
    ) -> Any:
        max_attempts = max(1, int(self._MAX_RETRY_ATTEMPTS))
        for attempt in range(1, max_attempts + 1):
            try:
                return fn()
            except Exception as exc:
                if not self._is_retryable_error(exc) or attempt >= max_attempts:
                    raise

                status_code = self._extract_status_code(exc)
                delay_seconds = self._retry_delay_seconds(attempt)
                logger.warning(
                    "[LLM] 可重试错误，准备重试："
                    f"{json.dumps({'task_kind': task_kind, 'model': model, 'attempt': attempt, 'max_attempts': max_attempts, 'delay_seconds': delay_seconds, 'status_code': status_code, 'error': str(exc)}, ensure_ascii=False)}"
                )
                emit_system_log(
                    category="llm_call",
                    event_name=f"{event_name_prefix}_retry",
                    level="WARNING",
                    owner_id=task_context.get("owner_id"),
                    task_id=task_context.get("task_id"),
                    task_type=task_context.get("task_type"),
                    method=interface_fields.get("method"),
                    path=interface_fields.get("path"),
                    provider="llm",
                    target_host=interface_fields.get("target_host"),
                    success=False,
                    duration_ms=0,
                    message=f"检测到可重试错误，{delay_seconds:.1f}s 后重试",
                    payload={
                        "request": {
                            "task_kind": task_kind,
                            "model": model,
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                        },
                        "error": {
                            "type": type(exc).__name__,
                            "status_code": status_code,
                            "message": str(exc),
                        },
                    },
                )
                time.sleep(delay_seconds)

    @classmethod
    def _should_enable_explicit_cache(cls, task_kind: str) -> bool:
        return str(task_kind or "").strip() in cls._EXPLICIT_CACHE_TASK_KINDS

    @staticmethod
    def _build_cached_system_content(
        system_prompt: str, *, enable_explicit_cache: bool
    ) -> Any:
        if not enable_explicit_cache:
            return system_prompt
        return [
            {
                "type": "text",
                "text": str(system_prompt or ""),
                "cache_control": {"type": "ephemeral"},
            }
        ]

    @classmethod
    def _is_explicit_cache_unsupported_error(cls, exc: Exception) -> bool:
        message = str(exc or "").lower()
        if not message or "cache" not in message:
            return False
        markers = (
            "cache_control",
            "cache control",
            "ephemeral",
            "unsupported",
            "unknown parameter",
            "invalid parameter",
            "invalid_request_error",
            "badrequest",
            "bad request",
        )
        return any(marker in message for marker in markers)

    @staticmethod
    def _strip_messages_cache_control(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        stripped_messages: List[Dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                stripped_messages.append(message)
                continue
            cloned_message: Dict[str, Any] = dict(message)
            content = cloned_message.get("content")
            if isinstance(content, list):
                new_content: List[Any] = []
                for item in content:
                    if isinstance(item, dict) and "cache_control" in item:
                        cloned_item = dict(item)
                        cloned_item.pop("cache_control", None)
                        new_content.append(cloned_item)
                    else:
                        new_content.append(item)
                cloned_message["content"] = new_content
            stripped_messages.append(cloned_message)
        return stripped_messages

    def _invoke_vision_with_cache_fallback(
        self,
        *,
        request_kwargs: Dict[str, Any],
        model: str,
        task_kind: str,
        event_name_prefix: str,
        task_context: Dict[str, Optional[str]],
        explicit_cache_enabled: bool,
        fallback_event_name: str,
        fallback_log_message: str,
    ) -> Any:
        try:
            return self._call_with_retry(
                fn=lambda: self.vlm_client.chat.completions.create(**request_kwargs),
                model=model,
                task_kind=task_kind,
                event_name_prefix=event_name_prefix,
                task_context=task_context,
                interface_fields=self._vision_interface,
            )
        except Exception as exc:
            if not (
                explicit_cache_enabled
                and self._is_explicit_cache_unsupported_error(exc)
            ):
                raise
            fallback_messages = self._strip_messages_cache_control(
                request_kwargs.get("messages") or []
            )
            logger.warning(
                f"[LLM] {fallback_log_message}："
                f"{json.dumps({'task_kind': task_kind, 'model': model, 'error': str(exc)}, ensure_ascii=False)}"
            )
            emit_system_log(
                category="llm_call",
                event_name=fallback_event_name,
                level="WARNING",
                owner_id=task_context.get("owner_id"),
                task_id=task_context.get("task_id"),
                task_type=task_context.get("task_type"),
                method=self._vision_interface.get("method"),
                path=self._vision_interface.get("path"),
                provider="llm",
                target_host=self._vision_interface.get("target_host"),
                success=True,
                duration_ms=0,
                message="显式缓存参数不可用，已降级重试",
                payload={
                    "request": {
                        "task_kind": task_kind,
                        "model": model,
                    },
                    "error": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    },
                },
            )
            fallback_kwargs = dict(request_kwargs)
            fallback_kwargs["messages"] = fallback_messages
            return self._call_with_retry(
                fn=lambda: self.vlm_client.chat.completions.create(**fallback_kwargs),
                model=model,
                task_kind=task_kind,
                event_name_prefix=event_name_prefix,
                task_context=task_context,
                interface_fields=self._vision_interface,
            )

    def invoke_text_json(
        self,
        messages: List[Dict[str, str]],
        *,
        task_kind: str,
        temperature: float = 0.1,
        max_tokens: int = 65536,
        model_override: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        policy = self._resolve_policy(task_kind)
        chosen_model = str(model_override or "").strip() or self._resolve_text_model(
            policy["tier"]
        )

        try:
            return self._invoke_text_json_once(
                messages=messages,
                chosen_model=chosen_model,
                thinking=bool(policy["thinking"]),
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                policy=policy,
            )
        except ValueError as exc:
            if str(exc) != self._JSON_PARSE_ERROR or bool(policy["thinking"]):
                raise
            logger.warning(
                f"[LLM] chat_completion_json 在关闭思考时解析失败，正在改为开启思考后重试。"
                f"task_kind={policy['task_kind']}, model={chosen_model}"
            )
            return self._invoke_text_json_once(
                messages=messages,
                chosen_model=chosen_model,
                thinking=True,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                policy=policy,
            )

    def _invoke_text_json_once(
        self,
        *,
        messages: List[Dict[str, str]],
        chosen_model: str,
        thinking: bool,
        temperature: float,
        max_tokens: int,
        timeout: Optional[float],
        policy: Dict[str, Any],
    ) -> Dict[str, Any]:
        log_payload = self._collect_prompt_summary(messages)
        task_context = self._current_task_log_context()
        log_payload.update(
            {
                "task_kind": policy["task_kind"],
                "tier": policy["tier"],
                "model": chosen_model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "thinking_enabled": thinking,
            }
        )
        logger.info(
            "[LLM] chat_completion_json 请求："
            f"{json.dumps(log_payload, ensure_ascii=False)}"
        )

        start = time.perf_counter()
        task_context = self._current_task_log_context()
        try:
            response = self._call_with_retry(
                fn=lambda: self.text_client.chat.completions.create(
                    model=chosen_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                    extra_body=self._build_thinking_extra_body(thinking),
                    timeout=timeout or settings.LLM_REQUEST_TIMEOUT_SECONDS,
                ),
                model=chosen_model,
                task_kind=policy["task_kind"],
                event_name_prefix="chat_completion_json",
                task_context=task_context,
                interface_fields=self._text_interface,
            )

            content = response.choices[0].message.content
            content = str(content).replace("```json", "").replace("```", "").strip()
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            reasoning_text = self._extract_reasoning_text(response)
            usage_summary = self._get_usage_summary(response)
            response_payload = {
                "task_kind": policy["task_kind"],
                "tier": policy["tier"],
                "model": chosen_model,
                "elapsed_ms": elapsed_ms,
                "response_id": getattr(response, "id", None),
                "thinking_returned": bool(reasoning_text),
                "thinking_chars": len(reasoning_text),
                "output_chars": len(content),
                **usage_summary,
            }

            logger.info(
                "[LLM] chat_completion_json 响应："
                f"{json.dumps(response_payload, ensure_ascii=False)}"
            )
            self._report_usage(chosen_model, usage_summary)
            emit_system_log(
                category="llm_call",
                event_name="chat_completion_json",
                level="INFO",
                owner_id=task_context.get("owner_id"),
                task_id=task_context.get("task_id"),
                task_type=task_context.get("task_type"),
                method=self._text_interface.get("method"),
                path=self._text_interface.get("path"),
                provider="llm",
                target_host=self._text_interface.get("target_host"),
                success=True,
                duration_ms=elapsed_ms,
                message="文本模型 JSON 调用成功",
                payload={
                    "request": {
                        "task_kind": policy["task_kind"],
                        "tier": policy["tier"],
                        "model": chosen_model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "thinking": thinking,
                    },
                    "response": {
                        **response_payload,
                        "content": content,
                        "reasoning_text": reasoning_text,
                    },
                },
            )

            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"[LLM] JSON 响应解析失败：{e}")
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            emit_system_log(
                category="llm_call",
                event_name="chat_completion_json_parse_error",
                level="ERROR",
                owner_id=task_context.get("owner_id"),
                task_id=task_context.get("task_id"),
                task_type=task_context.get("task_type"),
                method=self._text_interface.get("method"),
                path=self._text_interface.get("path"),
                provider="llm",
                target_host=self._text_interface.get("target_host"),
                success=False,
                duration_ms=elapsed_ms,
                message=str(e),
                payload={
                    "request": {
                        "task_kind": policy["task_kind"],
                        "tier": policy["tier"],
                        "model": chosen_model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "thinking": thinking,
                    },
                },
            )
            raise ValueError(self._JSON_PARSE_ERROR)
        except Exception as e:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            logger.error(
                "[LLM] JSON 补全调用失败："
                f"{json.dumps({'model': chosen_model, 'elapsed_ms': elapsed_ms, 'error': str(e)}, ensure_ascii=False)}"
            )
            emit_system_log(
                category="llm_call",
                event_name="chat_completion_json_error",
                level="ERROR",
                owner_id=task_context.get("owner_id"),
                task_id=task_context.get("task_id"),
                task_type=task_context.get("task_type"),
                method=self._text_interface.get("method"),
                path=self._text_interface.get("path"),
                provider="llm",
                target_host=self._text_interface.get("target_host"),
                success=False,
                duration_ms=elapsed_ms,
                message=str(e),
                payload={
                    "request": {
                        "task_kind": policy["task_kind"],
                        "tier": policy["tier"],
                        "model": chosen_model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "thinking": thinking,
                    },
                    "error": {
                        "type": type(e).__name__,
                        "message": str(e),
                    },
                },
            )
            raise

    @staticmethod
    def _to_data_url(image_path: str) -> str:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")
        return f"data:image/jpeg;base64,{img_b64}"

    def invoke_vision_image(
        self,
        image_path: str,
        system_prompt: str,
        user_prompt: str,
        *,
        task_kind: str,
        temperature: float = 0.6,
        model_override: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> str:
        if not self.vlm_client:
            raise RuntimeError(
                "[LLM] Vision client not initialized. Please set VLM_API_KEY in environment"
            )

        policy = self._resolve_policy(task_kind)
        model = str(model_override or "").strip() or self._resolve_vision_model(policy["tier"])
        thinking = bool(policy["thinking"])
        explicit_cache_enabled = self._should_enable_explicit_cache(policy["task_kind"])

        log_payload = {
            "task_kind": policy["task_kind"],
            "tier": policy["tier"],
            "model": model,
            "image_path": image_path,
            "system_prompt_chars": len(system_prompt or ""),
            "user_prompt_chars": len(user_prompt or ""),
            "thinking_enabled": thinking,
            "explicit_cache_enabled": explicit_cache_enabled,
            "temperature": temperature,
        }
        logger.info(
            f"[LLM] analyze_image_with_thinking 请求：{json.dumps(log_payload, ensure_ascii=False)}"
        )
        start = time.perf_counter()
        task_context = self._current_task_log_context()

        try:
            img_url = self._to_data_url(image_path)
            messages: List[Dict[str, Any]] = [
                {
                    "role": "system",
                    "content": self._build_cached_system_content(
                        system_prompt, enable_explicit_cache=explicit_cache_enabled
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": img_url}},
                        {"type": "text", "text": user_prompt},
                    ],
                },
            ]
            request_kwargs = {
                "model": model,
                "messages": messages,
                "extra_body": self._build_thinking_extra_body(thinking),
                "temperature": temperature,
                "timeout": timeout or settings.LLM_REQUEST_TIMEOUT_SECONDS,
            }
            response = self._invoke_vision_with_cache_fallback(
                request_kwargs=request_kwargs,
                model=model,
                task_kind=policy["task_kind"],
                event_name_prefix="analyze_image_with_thinking",
                task_context=task_context,
                explicit_cache_enabled=explicit_cache_enabled,
                fallback_event_name="analyze_image_with_thinking_cache_fallback",
                fallback_log_message="显式缓存参数不可用，自动降级为普通视觉调用",
            )
            content = response.choices[0].message.content or ""
            reasoning_text = self._extract_reasoning_text(response)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            usage_summary = self._get_usage_summary(response)
            response_payload = {
                "task_kind": policy["task_kind"],
                "tier": policy["tier"],
                "model": model,
                "image_path": image_path,
                "elapsed_ms": elapsed_ms,
                "response_id": getattr(response, "id", None),
                "thinking_returned": bool(reasoning_text),
                "thinking_chars": len(reasoning_text),
                "output_chars": len(str(content)),
                **usage_summary,
            }

            logger.info(
                "[LLM] analyze_image_with_thinking 响应："
                f"{json.dumps(response_payload, ensure_ascii=False)}"
            )
            self._report_usage(model, usage_summary)
            emit_system_log(
                category="llm_call",
                event_name="analyze_image_with_thinking",
                level="INFO",
                owner_id=task_context.get("owner_id"),
                task_id=task_context.get("task_id"),
                task_type=task_context.get("task_type"),
                method=self._vision_interface.get("method"),
                path=self._vision_interface.get("path"),
                provider="llm",
                target_host=self._vision_interface.get("target_host"),
                success=True,
                duration_ms=elapsed_ms,
                message="单图视觉分析调用成功",
                payload={
                    "request": {
                        "task_kind": policy["task_kind"],
                        "tier": policy["tier"],
                        "model": model,
                        "image_path": image_path,
                        "system_prompt": system_prompt,
                        "user_prompt": user_prompt,
                        "temperature": temperature,
                        "thinking": thinking,
                        "explicit_cache_enabled": explicit_cache_enabled,
                    },
                    "response": {
                        **response_payload,
                        "content": content,
                        "reasoning_text": reasoning_text,
                    },
                },
            )
            return str(content)
        except Exception as e:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            logger.error(
                "[LLM] 视觉分析（含思考）失败："
                f"{json.dumps({'model': model, 'image_path': image_path, 'elapsed_ms': elapsed_ms, 'error': str(e)}, ensure_ascii=False)}"
            )
            emit_system_log(
                category="llm_call",
                event_name="analyze_image_with_thinking_error",
                level="ERROR",
                owner_id=task_context.get("owner_id"),
                task_id=task_context.get("task_id"),
                task_type=task_context.get("task_type"),
                method=self._vision_interface.get("method"),
                path=self._vision_interface.get("path"),
                provider="llm",
                target_host=self._vision_interface.get("target_host"),
                success=False,
                duration_ms=elapsed_ms,
                message=str(e),
                payload={
                    "request": {
                        "task_kind": policy["task_kind"],
                        "tier": policy["tier"],
                        "model": model,
                        "image_path": image_path,
                        "system_prompt": system_prompt,
                        "user_prompt": user_prompt,
                        "temperature": temperature,
                        "thinking": thinking,
                        "explicit_cache_enabled": explicit_cache_enabled,
                    },
                    "error": {
                        "type": type(e).__name__,
                        "message": str(e),
                    },
                },
            )
            raise

    def invoke_vision_images(
        self,
        image_paths: List[str],
        system_prompt: str,
        user_prompt: str,
        *,
        task_kind: str,
        temperature: float = 0.2,
        model_override: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> str:
        if not self.vlm_client:
            raise RuntimeError(
                "[LLM] Vision client not initialized. Please set VLM_API_KEY in environment"
            )
        if not image_paths:
            raise ValueError("image_paths is empty")

        policy = self._resolve_policy(task_kind)
        chosen_model = str(model_override or "").strip() or self._resolve_vision_model(
            policy["tier"]
        )
        thinking = bool(policy["thinking"])
        explicit_cache_enabled = self._should_enable_explicit_cache(policy["task_kind"])

        log_payload = {
            "task_kind": policy["task_kind"],
            "tier": policy["tier"],
            "model": chosen_model,
            "image_count": len(image_paths),
            "image_paths": image_paths,
            "system_prompt_chars": len(system_prompt or ""),
            "user_prompt_chars": len(user_prompt or ""),
            "thinking_enabled": thinking,
            "explicit_cache_enabled": explicit_cache_enabled,
            "temperature": temperature,
        }
        logger.info(
            f"[LLM] invoke_vision_images 请求：{json.dumps(log_payload, ensure_ascii=False)}"
        )
        start = time.perf_counter()
        task_context = self._current_task_log_context()

        try:
            content: List[Dict[str, Any]] = []
            for image_path in image_paths:
                content.append(
                    {"type": "image_url", "image_url": {"url": self._to_data_url(image_path)}}
                )
            content.append({"type": "text", "text": user_prompt})
            messages: List[Dict[str, Any]] = [
                {
                    "role": "system",
                    "content": self._build_cached_system_content(
                        system_prompt, enable_explicit_cache=explicit_cache_enabled
                    ),
                },
                {"role": "user", "content": content},
            ]
            request_kwargs = {
                "model": chosen_model,
                "messages": messages,
                "extra_body": self._build_thinking_extra_body(thinking),
                "temperature": temperature,
                "timeout": timeout or settings.LLM_REQUEST_TIMEOUT_SECONDS,
            }
            response = self._invoke_vision_with_cache_fallback(
                request_kwargs=request_kwargs,
                model=chosen_model,
                task_kind=policy["task_kind"],
                event_name_prefix="invoke_vision_images",
                task_context=task_context,
                explicit_cache_enabled=explicit_cache_enabled,
                fallback_event_name="invoke_vision_images_cache_fallback",
                fallback_log_message="显式缓存参数不可用，自动降级为普通多图视觉调用",
            )

            raw_content = response.choices[0].message.content or ""
            reasoning_text = self._extract_reasoning_text(response)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            usage_summary = self._get_usage_summary(response)
            response_payload = {
                "task_kind": policy["task_kind"],
                "tier": policy["tier"],
                "model": chosen_model,
                "image_count": len(image_paths),
                "elapsed_ms": elapsed_ms,
                "response_id": getattr(response, "id", None),
                "thinking_returned": bool(reasoning_text),
                "thinking_chars": len(reasoning_text),
                "output_chars": len(str(raw_content)),
                **usage_summary,
            }
            logger.info(
                "[LLM] invoke_vision_images 响应："
                f"{json.dumps(response_payload, ensure_ascii=False)}"
            )
            self._report_usage(chosen_model, usage_summary)
            emit_system_log(
                category="llm_call",
                event_name="invoke_vision_images",
                level="INFO",
                owner_id=task_context.get("owner_id"),
                task_id=task_context.get("task_id"),
                task_type=task_context.get("task_type"),
                method=self._vision_interface.get("method"),
                path=self._vision_interface.get("path"),
                provider="llm",
                target_host=self._vision_interface.get("target_host"),
                success=True,
                duration_ms=elapsed_ms,
                message="多图视觉分析文本调用成功",
                payload={
                    "request": {
                        "task_kind": policy["task_kind"],
                        "tier": policy["tier"],
                        "model": chosen_model,
                        "image_paths": image_paths,
                        "system_prompt": system_prompt,
                        "user_prompt": user_prompt,
                        "temperature": temperature,
                        "thinking": thinking,
                        "explicit_cache_enabled": explicit_cache_enabled,
                    },
                    "response": {
                        **response_payload,
                        "content": str(raw_content),
                        "reasoning_text": reasoning_text,
                    },
                },
            )
            return str(raw_content)
        except Exception as e:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            logger.error(
                "[LLM] 多图视觉分析文本调用失败："
                f"{json.dumps({'model': chosen_model, 'image_count': len(image_paths), 'elapsed_ms': elapsed_ms, 'error': str(e)}, ensure_ascii=False)}"
            )
            emit_system_log(
                category="llm_call",
                event_name="invoke_vision_images_error",
                level="ERROR",
                owner_id=task_context.get("owner_id"),
                task_id=task_context.get("task_id"),
                task_type=task_context.get("task_type"),
                method=self._vision_interface.get("method"),
                path=self._vision_interface.get("path"),
                provider="llm",
                target_host=self._vision_interface.get("target_host"),
                success=False,
                duration_ms=elapsed_ms,
                message=str(e),
                payload={
                    "request": {
                        "task_kind": policy["task_kind"],
                        "tier": policy["tier"],
                        "model": chosen_model,
                        "image_paths": image_paths,
                        "system_prompt": system_prompt,
                        "user_prompt": user_prompt,
                        "temperature": temperature,
                        "thinking": thinking,
                        "explicit_cache_enabled": explicit_cache_enabled,
                    },
                    "error": {
                        "type": type(e).__name__,
                        "message": str(e),
                    },
                },
            )
            raise

# 单例实例，供全局使用
llm_service = LLMService()


def get_llm_service() -> LLMService:
    """获取 LLM 服务实例"""
    return llm_service
