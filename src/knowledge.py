import json
from typing import Dict
from openai import OpenAI
from config import settings
from loguru import logger

class KnowledgeExtractor:
    def __init__(self, client: OpenAI):
        self.client = client

    def extract_entities(self, md_content: str) -> Dict:
        """从 Markdown 文本中提取组件实体关系"""
        logger.info("[Knowledge] Extracting entities from text...")
        
        system_prompt = """
**角色与任务：**
你是一个精通专利结构分析的专家。你的核心任务是从给定的专利文本中，系统性地识别并提取所有技术构件的“实体关系”，最终构建一个结构化的全局映射表。

**输出要求：**
1.  **格式：** 最终输出必须是一个**单一的、完整的 JSON 对象**。
2.  **JSON结构：** JSON 对象是一个字典，其 `key` 是构件的**编号**（字符串，如 `"1"`, `"701"`, `"8"`），其 `value` 是一个包含该构件详细信息的子字典。
3.  **子字典结构：** 每个构件的子字典应包含以下字段：
    *   `"name"`: (字符串) 该构件的标准中文名称。
    *   `"function"`: (字符串) 基于专利详细描述，用一句话精炼概括该构件的核心功能或作用。**请从“用于...”、“用以...”、“实现...”等描述性语句中提取，而非仅重复名称。**
    *   `"parent_system"`: (字符串 | null) 该构件所属的上一级子系统或装置的编号。如果该构件本身就是一个顶级系统或装置，或文本未明确其归属，则此字段为 `null`。
        *   *示例：* 清洁圈(707) 是 清洁装置(7) 的一部分，因此清洁圈(707)的 `"parent_system"` 为 `"7"`。
    *   `"components"`: (数组 | null) 如果该构件是一个由多个子部件组成的系统或装置，则此字段为一个字符串数组，列出其直接子部件的编号。如果是单一部件，则为 `null`。
        *   *示例：* 喷洒装置(8) 包含囊瓶(81)、喷管(82)等，因此喷洒装置(8)的 `"components"` 为 `["81", "82", ...]`。

**处理与分析规则：**
1.  **实体识别：** 扫描全文，识别所有括号`()`内的数字或数字字母组合标记，如`支撑座(1)`、`底座(701)`、`喷洒装置(8)`。这些是你要提取的“构件”。
2.  **关系推断：**
    *   **层级关系：** 仔细分析权利要求和具体实施方式的描述逻辑。当一个构件（如`清洁装置(7)`）被描述为“包括”或“设置有”另一些构件（如`底座(701)`、`电机(702)`）时，建立`parent_system`和`components`的关系。
    *   **功能提取：** 从对该构件的详细操作描述、目的性语句（如“用以...”、“用于...”、“从而...”引导的句子）中，总结其**功能**，而非其物理特征。
3.  **信息整合与去重：**
    *   同一编号在全文可能多次出现。请从**首次出现**或**定义最清晰**的地方提取其`name`。
    *   `function` 应从最详尽的描述段落中综合概括。
    *   确保每个编号只在顶级JSON中出现一次。
4.  **排除非构件标记：** 忽略专利号、分类号（如`(2006.01)`）、步骤编号（如`S1`）、参考文献标号等。

**输入与输出示例：**
*   **输入文本（片段）：** “...清洁装置(7)包括固定连接在液压伸缩杆一端的底座(701)，所述底座(701)的表面固定连接有电机(702)...用以对桥梁挠度检测时对桥梁底部污垢进行清除。”
*   **期望输出（部分）：**
```json
{{
  "7": {{
    "name": "清洁装置",
    "function": "对桥梁挠度检测时清除桥梁底部污垢",
    "parent_system": null,
    "components": ["701", "702", "703", "713", "707", "708"]
  }},
  "701": {{
    "name": "底座",
    "function": "作为清洁装置的安装基座，固定连接液压伸缩杆和电机",
    "parent_system": "7",
    "components": null
  }},
  "702": {{
    "name": "电机",
    "function": "提供动力，驱动支撑杆及清洁部件旋转",
    "parent_system": "7",
    "components": null
  }}
}}
```

**最终指令：**
请严格遵循上述规则，对用户提供的专利全文进行深度分析。你的目标是生成一个准确、完整、结构化的JSON映射表，清晰展现构件的核心信息与层级关系。直接输出这个JSON对象，不要有任何其他内容。
        """
        
        # 截断过长的文本，防止 Token 溢出 (可选，视模型上下文窗口而定)
        # 专利文本可能很长，建议只截取 "详细说明" 部分，或者分块处理。
        # 这里简化处理：直接发送前 30000 字符，通常包含大部分定义
        truncated_content = md_content[:30000] 

        try:
            response = self.client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": truncated_content}
                ],
                temperature=settings.LLM_TEMPERATURE,
                response_format={"type": "json_object"}
            )
            
            result_str = response.choices[0].message.content
            data = json.loads(result_str)
            logger.success(f"[Knowledge] Extracted {len(data)} entities.")
            return data
            
        except Exception as e:
            logger.error(f"[Knowledge] Extraction failed: {e}")
            return {}
