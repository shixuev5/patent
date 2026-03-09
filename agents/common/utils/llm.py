# src/llm.py
import base64
import json
import time
from typing import Optional, List, Dict, Any
from openai import OpenAI
from loguru import logger
from config import settings


class LLMService:
    """统一的 LLM 服务类，提供文本和视觉模型的调用接口"""

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        """
        初始化 LLM 服务。

        Args:
            api_key: 可选，指定的 API Key。如果不传，则使用 config.settings.LLM_API_KEY
            base_url: 可选，指定的 Base URL。如果不传，则使用 config.settings.LLM_BASE_URL
        """
        # 1. 确定配置 (支持实例级重写，用于独立实例化审查员 Agent)
        final_api_key = api_key or settings.LLM_API_KEY
        final_base_url = base_url or settings.LLM_BASE_URL

        # 2. 初始化文本模型客户端
        self.text_client = OpenAI(api_key=final_api_key, base_url=final_base_url)

        # 3. 初始化视觉模型客户端 (VLM 通常使用全局配置)
        self.vlm_client = OpenAI(
            api_key=settings.VLM_API_KEY, base_url=settings.VLM_BASE_URL
        )

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
    def _get_usage_summary(response: Any) -> Dict[str, Optional[int]]:
        usage = getattr(response, "usage", None)
        return {
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
            "reasoning_tokens": getattr(
                getattr(usage, "completion_tokens_details", None),
                "reasoning_tokens",
                None,
            ),
        }

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

        # 兼容部分模型将思考内容放在 reasoning 字段
        reasoning = getattr(message, "reasoning", None)
        if isinstance(reasoning, str):
            return reasoning.strip()
        return ""

    def chat_completion_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 65536,
        model: Optional[str] = None,
        thinking: bool = True,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        JSON 格式对话，自动解析返回的 JSON

        Args:
            messages: 对话消息列表
            temperature: 温度参数，默认 0.1 保持精确
            max_tokens: 最大 token 数，deepseek-chat 支持 8k，deepseek-reasoner 支持 64k
            model: 模型

        Returns:
            解析后的 JSON 字典
        """
        chosen_model = model or settings.LLM_MODEL
        log_payload = self._collect_prompt_summary(messages)
        log_payload.update(
            {
                "model": chosen_model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "thinking_enabled": thinking,
            }
        )
        logger.info(
            "[LLM] chat_completion_json request: "
            f"{json.dumps(log_payload, ensure_ascii=False)}"
        )

        start = time.perf_counter()
        try:
            if chosen_model == "kimi-k2.5":
                temperature = 1.0

            response = self.text_client.chat.completions.create(
                model=chosen_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                extra_body={"thinking": {"type": "enabled" if thinking else "disabled"}},
                timeout=timeout or settings.LLM_REQUEST_TIMEOUT_SECONDS,
            )

            content = response.choices[0].message.content

            content = content.replace("```json", "").replace("```", "").strip()
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            reasoning_text = self._extract_reasoning_text(response)
            usage_summary = self._get_usage_summary(response)
            response_payload = {
                "model": chosen_model,
                "elapsed_ms": elapsed_ms,
                "response_id": getattr(response, "id", None),
                "thinking_returned": bool(reasoning_text),
                "thinking_chars": len(reasoning_text),
                "output_chars": len(content),
                **usage_summary,
            }

            logger.info(
                "[LLM] chat_completion_json response: "
                f"{json.dumps(response_payload, ensure_ascii=False)}"
            )

            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"[LLM] Failed to parse JSON response: {e}")
            raise ValueError("Model output is not valid JSON")
        except Exception as e:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            logger.error(
                "[LLM] JSON completion failed: "
                f"{json.dumps({'model': chosen_model, 'elapsed_ms': elapsed_ms, 'error': str(e)}, ensure_ascii=False)}"
            )
            raise

    @staticmethod
    def _to_data_url(image_path: str) -> str:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")
        return f"data:image/jpeg;base64,{img_b64}"

    def analyze_image_with_thinking(
        self, image_path: str, system_prompt: str, user_prompt: str, temperature: float = 0.6
    ) -> str:
        """
        使用思考模式的视觉模型图片理解

        Args:
            image_path: 图片路径
            system_prompt: 静态指令（用于缓存）
            user_prompt: 动态上下文描述（结合图片）

        Returns:
            模型返回的分析结果
        """
        if not self.vlm_client:
            raise RuntimeError(
                "[LLM] Vision client not initialized. Please set VLM_API_KEY in environment"
            )

        model = settings.VLM_MODEL
        log_payload = {
            "model": model,
            "image_path": image_path,
            "system_prompt_chars": len(system_prompt or ""),
            "user_prompt_chars": len(user_prompt or ""),
            "thinking_enabled": True,
            "temperature": temperature,
        }
        logger.info(
            f"[LLM] analyze_image_with_thinking request: {json.dumps(log_payload, ensure_ascii=False)}"
        )
        start = time.perf_counter()

        try:
            img_url = self._to_data_url(image_path)

            temperature = 1.0 if model == "kimi-k2.5" else temperature

            # 调用视觉模型
            response = self.vlm_client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": img_url}},
                            {"type": "text", "text": user_prompt},
                        ],
                    },
                ],
                extra_body={"thinking": {"type": "enabled"}},
                temperature=temperature,
            )
            content = response.choices[0].message.content or ""
            reasoning_text = self._extract_reasoning_text(response)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            usage_summary = self._get_usage_summary(response)
            response_payload = {
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
                "[LLM] analyze_image_with_thinking response: "
                f"{json.dumps(response_payload, ensure_ascii=False)}"
            )
            return content
        except Exception as e:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            logger.error(
                "[LLM] Vision analysis with thinking failed: "
                f"{json.dumps({'model': model, 'image_path': image_path, 'elapsed_ms': elapsed_ms, 'error': str(e)}, ensure_ascii=False)}"
            )
            raise

    def analyze_images_json_with_thinking(
        self,
        image_paths: List[str],
        system_prompt: str,
        user_prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.2,
    ) -> Dict[str, Any]:
        """
        多图视觉分析，要求模型返回 JSON。

        Args:
            image_paths: 图片路径列表
            system_prompt: 静态系统指令
            user_prompt: 动态用户指令
            model: 可选模型名（为空则使用 settings.VLM_MODEL）
            temperature: 温度参数
        """
        if not self.vlm_client:
            raise RuntimeError(
                "[LLM] Vision client not initialized. Please set VLM_API_KEY in environment"
            )
        if not image_paths:
            raise ValueError("image_paths is empty")

        chosen_model = model or settings.VLM_MODEL
        log_payload = {
            "model": chosen_model,
            "image_count": len(image_paths),
            "image_paths": image_paths,
            "system_prompt_chars": len(system_prompt or ""),
            "user_prompt_chars": len(user_prompt or ""),
            "thinking_enabled": True,
            "temperature": temperature,
        }
        logger.info(
            f"[LLM] analyze_images_json_with_thinking request: {json.dumps(log_payload, ensure_ascii=False)}"
        )
        start = time.perf_counter()

        try:
            content: List[Dict[str, Any]] = []
            for image_path in image_paths:
                content.append(
                    {"type": "image_url", "image_url": {"url": self._to_data_url(image_path)}}
                )
            content.append({"type": "text", "text": user_prompt})

            final_temperature = 1.0 if chosen_model == "kimi-k2.5" else temperature

            response = self.vlm_client.chat.completions.create(
                model=chosen_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},
                ],
                extra_body={"thinking": {"type": "enabled"}},
                temperature=final_temperature,
            )

            raw_content = response.choices[0].message.content or ""
            reasoning_text = self._extract_reasoning_text(response)
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            usage_summary = self._get_usage_summary(response)
            response_payload = {
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
                "[LLM] analyze_images_json_with_thinking response: "
                f"{json.dumps(response_payload, ensure_ascii=False)}"
            )
            cleaned = str(raw_content).replace("```json", "").replace("```", "").strip()
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error(f"[LLM] Failed to parse vision JSON response: {e}")
            raise ValueError("Vision model output is not valid JSON")
        except Exception as e:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            logger.error(
                "[LLM] Multi-image vision analysis failed: "
                f"{json.dumps({'model': chosen_model, 'image_count': len(image_paths), 'elapsed_ms': elapsed_ms, 'error': str(e)}, ensure_ascii=False)}"
            )
            raise


# 单例实例，供全局使用
llm_service = LLMService()


def get_llm_service() -> LLMService:
    """获取 LLM 服务实例"""
    return llm_service
