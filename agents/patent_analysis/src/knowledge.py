from typing import Dict
from loguru import logger
from agents.common.utils.llm import get_llm_service
from config import Settings
from agents.patent_analysis.src.context_selector import ContextSelector


class KnowledgeExtractor:
    def __init__(self, retrieval_session_id: str = ""):
        self.llm_service = get_llm_service()
        self.retrieval_session_id = str(retrieval_session_id or "").strip()

    def _construct_context(self, patent_data: Dict, max_chars: int = 5000) -> str:
        """
        基于检索裁剪构建上下文，专注于提取部件名称及其功能/位置。
        """
        biblio = patent_data.get("bibliographic_data", {}) if isinstance(patent_data, dict) else {}
        description = patent_data.get("description", {}) if isinstance(patent_data, dict) else {}

        raw_query = "\n".join(
            [
                "抽取附图标记对应的物理部件名称、功能、位置与连接关系",
                str(description.get("brief_description_of_drawings", "")).strip(),
                str(description.get("summary_of_invention", "")).strip()[:800],
                str(biblio.get("abstract", "")).strip()[:400],
            ]
        ).strip()

        fallback_text = "\n\n".join(
            [
                f"### 【摘要】\n{str(biblio.get('abstract', '')).strip()}",
                f"### 【附图说明】\n{str(description.get('brief_description_of_drawings', '')).strip()}",
                f"### 【具体实施方式】\n{str(description.get('detailed_description', '')).strip()[:max_chars]}",
            ]
        ).strip()

        selector = ContextSelector(
            patent_data=patent_data,
            llm_service=self.llm_service,
            retrieval_session_id=self.retrieval_session_id,
        )
        result = selector.select_context(
            task_intent="提取带附图标记的物理实体，并补充功能与关系描述",
            raw_query=raw_query,
            fallback_text=fallback_text,
            max_chars=max_chars,
            top_n=24,
            top_k=8,
            mode="session" if self.retrieval_session_id else "ephemeral",
            policy="always",
        )
        return result.context

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
