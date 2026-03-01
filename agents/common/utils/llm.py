# src/llm.py
import base64
import json
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

    def chat_completion_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.1,
        max_tokens: int = 65536,
        model: Optional[str] = None,
        thinking: bool = True
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
        try:
            if model == 'kimi-k2.5': temperature = 1.0

            response = self.text_client.chat.completions.create(
                model=model or settings.LLM_MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
                extra_body={"thinking": {"type": "enabled" if thinking else "disabled"}}
            )

            content = response.choices[0].message.content

            content = content.replace("```json", "").replace("```", "").strip()

            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.error(f"[LLM] Failed to parse JSON response: {e}")
            raise ValueError("Model output is not valid JSON")
        except Exception as e:
            logger.error(f"[LLM] JSON completion failed: {e}")
            raise

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

        try:
            # 读取并编码图片
            with open(image_path, "rb") as f:
                img_b64 = base64.b64encode(f.read()).decode("utf-8")

            img_url = f"data:image/jpeg;base64,{img_b64}"

            model = settings.VLM_MODEL
            temperature = 1.0 if model == 'kimi-k2.5' else temperature

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
            return response.choices[0].message.content
        except Exception as e:
            logger.error(
                f"[LLM] Vision analysis with thinking failed for {image_path}: {e}"
            )
            raise


# 单例实例，供全局使用
llm_service = LLMService()


def get_llm_service() -> LLMService:
    """获取 LLM 服务实例"""
    return llm_service
