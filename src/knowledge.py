from typing import Dict
from loguru import logger
from src.utils.llm import get_llm_service
from config import Settings

class KnowledgeExtractor:
    def __init__(self):
        self.llm_service = get_llm_service()

    def _construct_context(self, patent_data: Dict, max_chars: int = 20000) -> str:
        """
        动态构建上下文，专注于提取部件名称及其功能/位置。
        """
        parts = []
        current_length = 0
        
        # 1. 摘要 (核心功能概览，Token占用极小，优先级高)
        abstract = patent_data.get("bibliographic_data", {}).get("abstract", "")
        if abstract:
            text = f"### 【摘要】(用于理解核心发明及其主要功能)\n{abstract}\n"
            parts.append(text)
            current_length += len(text)

        # 2. 附图说明 (ID-Name 字典，最关键，优先级高)
        brief_desc = patent_data.get("description", {}).get("brief_description_of_drawings", "")
        if brief_desc:
            text = f"### 【附图说明】(必须作为提取 编号-名称 的首要依据)\n{brief_desc}\n"
            parts.append(text)
            current_length += len(text)

        # 3. 具体实施方式 (功能与连接关系描述来源)
        detailed_desc = patent_data.get("description", {}).get("detailed_description", "")
        
        remaining_chars = max_chars - current_length
        if remaining_chars > 500 and detailed_desc:
            if remaining_chars < len(detailed_desc):
                # 截取剩余空间，但确保不截断得太离谱
                truncated_detail = detailed_desc[:remaining_chars]
                text = f"### 【具体实施方式】\n{truncated_detail}...\n(下文已截断)"
            else:
                text = f"### 【具体实施方式】\n{detailed_desc}\n"
            parts.append(text)
        
        return "\n".join(parts)

    def extract_entities(self, patent_data: Dict) -> Dict:
        """
        基于结构化的 patent_data 提取组件实体关系。
        """
        logger.info("[Knowledge] Constructing optimized context...")
        
        # 构建上下文
        refined_content = self._construct_context(patent_data)
        
        if not refined_content.strip():
            logger.warning("[Knowledge] Context is empty.")
            return {}

        system_prompt = """
**角色与任务：**
你是一个精通专利技术细节的AI助手。请根据提供的文本，构建一个“技术构件清单”。

**输入数据源优先级：**
1.  **【附图说明】**：这是“编号-名称”的绝对权威来源。文中若出现 `10-底座`，则编号 `10` 的名称必须是 `底座`。
2.  **【具体实施方式】**：这是提取组件“功能、位置、连接关系”的主要来源。

**输出格式要求：**
输出一个单一的 JSON 对象，Key 为**字符串格式的编号**，Value 为详细信息对象。

**Value 字段定义与提取规则（严格执行）：**
*   `"name"` (string): 构件的标准名称。优先使用【附图说明】中的命名。
*   `"function"` (string): **功能与位置的综合描述**。要求：
    *   **功能性**：描述它是用来做什么的（如“用于驱动滚轮旋转”）。
    *   **关系性**：简要描述它安装在哪里，或与谁连接（如“安装在底盘前端”、“位于滑块内部”）。
    *   **简洁性**：控制在 20-40 字之间。

**处理逻辑与约束：**
1.  **去噪**：忽略纯逻辑步骤（S1, S2）、时间（t1）、参数符号（a, b）。只提取物理实体部件。
2.  **去重**：同一编号只输出一次。
3.  **完整性**：提取文中出现的所有带有编号的实体构件。

**JSON 输出示例：**
```json
{
  "1": {
    "name": "桥梁检测机器人",
    "function": "整个装置的主体，用于沿桥面移动并进行裂纹扫描"
  },
  "10": {
    "name": "移动底盘",
    "function": "位于机器人底部，用于承载控制箱并提供移动动力"
  },
  "101": {
    "name": "驱动轮",
    "function": "安装在移动底盘两侧，通过电机驱动实现行走"
  }
}
```
"""

        try:
            logger.info("[Knowledge] Sending request to LLM...")
            data = self.llm_service.chat_completion_json(
                model=Settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": refined_content}
                ],
                temperature=0.1
            )

            logger.success(f"[Knowledge] Successfully extracted {len(data)} entities.")
            return data

        except Exception as e:
            logger.error(f"[Knowledge] Extraction failed: {e}")
            return {}