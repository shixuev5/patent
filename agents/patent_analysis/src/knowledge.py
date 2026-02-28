from typing import Dict
from loguru import logger
from agents.patent_analysis.src.utils.llm import get_llm_service
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
        brief_desc = patent_data.get("description", {}).get(
            "brief_description_of_drawings", ""
        )
        if brief_desc:
            text = (
                f"### 【附图说明】(必须作为提取 编号-名称 的首要依据)\n{brief_desc}\n"
            )
            parts.append(text)
            current_length += len(text)

        # 3. 具体实施方式 (功能与连接关系描述来源)
        detailed_desc = patent_data.get("description", {}).get(
            "detailed_description", ""
        )

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
        user_content = self._construct_context(patent_data)

        if not user_content.strip():
            logger.warning("[Knowledge] Context is empty.")
            return {}

        system_prompt = """
**角色与任务：**
你是一个精通专利技术细节的AI助手。请根据提供的文本，构建一个“技术构件清单”。

**核心规则（必须严格遵守）**
1.  **提取目标**：仅提取带有**附图标记（Reference Numerals）**的**物理实体/硬件结构**（如“10-底座”、“20-传感器”）。
2.  **绝对排除**：
    *   **严禁**提取方法步骤（如 S1, S10, 步骤1）。
    *   **严禁**提取列表序号（如文中写 "1. 第一种情况"，这里的 1 不是部件编号）。
    *   **严禁**提取纯参数符号（如 t0, L1）。
3.  **空结果原则**：如果文中**找不到**指向物理部件的数字标记，或者这是一篇**纯方法/流程类专利**，必须直接返回空对象 `{}`。

**输入数据源优先级：**
1.  **【附图说明】**：这是“编号-名称”的绝对权威来源。文中若出现 `10-底座`，则编号 `10` 的名称必须是 `底座`。
2.  **【具体实施方式】**：这是提取组件“功能、位置、连接关系”的主要来源。

**输出格式：**
1.  返回一个标准的 **JSON Object**。
2.  **严禁**包含任何 Markdown 标记（如 ```json）。
3.  **严禁**包含任何解释性文字，只返回 JSON 数据本身。

**JSON 结构定义**
Key：字符串格式的编号（去除括号，如 "10"）。
Value 字段定义与提取规则（严格执行）：
*   `"name"` (string): 构件的标准名称。优先使用【附图说明】中的命名。
*   `"function"` (string): **功能与位置的综合描述**。要求：
    *   **功能性**：描述它是用来做什么的（如“用于驱动滚轮旋转”）。
    *   **关系性**：简要描述它安装在哪里，或与谁连接（如“安装在底盘前端”、“位于滑块内部”）。
    *   **简洁性**：控制在 20-40 字之间。


**Few-Shot Examples (参考示例)**

Input:
[摘要]...本发明涉及一种桥梁检测车...
[附图说明] 1-车体；2-机械臂。
[具体实施方式] 车体1上安装有机械臂2...

Output:
{
  "1": { "name": "车体", "function": "装置主体，用于搭载机械臂并沿桥面移动" },
  "2": { "name": "机械臂", "function": "安装在车体上方，用于延伸至桥底进行探测" }
}

Input:
[摘要]...本发明提出一种数据处理方法...
[具体实施方式] 步骤S1：获取数据...步骤S2：分析数据...

Output:
{}
"""

        try:
            logger.info("[Knowledge] Sending request to LLM...")
            data = self.llm_service.chat_completion_json(
                model=Settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.0,
            )

            if not data:
                data = {}

            logger.success(f"[Knowledge] Successfully extracted {len(data)} entities.")
            return data

        except Exception as e:
            logger.error(f"[Knowledge] Extraction failed: {e}")
            return {}
