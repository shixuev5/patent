# src/llm.py
import base64
import json
from typing import Optional, List, Dict, Any
from openai import OpenAI
from loguru import logger
from config import settings

class LLMService:
    """统一的 LLM 服务类，提供文本和视觉模型的调用接口"""

    def __init__(self):
        # 1. 初始化通用文本模型客户端 (如 DeepSeek)
        self.text_client = OpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL
        )

        # 2. 初始化视觉模型客户端 (如 GLM-4V)
        self.vlm_client = OpenAI(
            api_key=settings.VLM_API_KEY,
            base_url=settings.VLM_BASE_URL
        )

    def chat_completion_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.1
    ) -> Dict[str, Any]:
        """
        JSON 格式对话，自动解析返回的 JSON

        Args:
            messages: 对话消息列表
            temperature: 温度参数，默认 0.1 保持精确

        Returns:
            解析后的 JSON 字典
        """
        try:
            response = self.text_client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=messages,
                temperature=temperature,
                response_format={"type": "json_object"}
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
        self,
        image_path: str,
        prompt: str
    ) -> str:
        """
        使用思考模式的视觉模型图片理解

        Args:
            image_path: 图片路径
            prompt: 分析提示词

        Returns:
            模型返回的分析结果
        """
        if not self.vlm_client:
            raise RuntimeError("[LLM] Vision client not initialized. Please set VLM_API_KEY in environment")

        try:
            # 读取并编码图片
            with open(image_path, 'rb') as f:
                img_b64 = base64.b64encode(f.read()).decode('utf-8')

            # 调用视觉模型
            response = self.vlm_client.chat.completions.create(
                model=settings.VLM_MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": img_b64}},
                            {"type": "text", "text": prompt},
                        ]
                    }
                ],
                extra_body={
                    "thinking": {"type": "enabled"}
                },
                temperature=0.6
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"[LLM] Vision analysis with thinking failed for {image_path}: {e}")
            raise


# 单例实例，供全局使用
llm_service = LLMService()

def get_llm_service() -> LLMService:
    """获取 LLM 服务实例"""
    return llm_service
