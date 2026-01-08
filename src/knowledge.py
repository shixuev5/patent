from typing import Dict
from loguru import logger
from src.llm import get_llm_service

class KnowledgeExtractor:
    def __init__(self):
        self.llm_service = get_llm_service()

    def _construct_context(self, patent_data: Dict, max_chars: int = 20000) -> str:
        """
        动态构建上下文，优先保留结构定义，剩余空间留给功能描述。
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

        # 3. 权利要求 (层级结构骨架，优先级中)
        # 往往权利要求很长，我们只取前 N 项通常能覆盖主要结构，或者全部取用
        claims = patent_data.get("claims", [])
        claims_text = []
        for c in claims:
            c_type = "独立" if c.get("claim_type") == "independent" else "从属"
            claims_text.append(f"权利要求{c.get('claim_number')} [{c_type}]: {c.get('claim_text')}")
        
        claims_full_str = "\n".join(claims_text)
        text = f"### 【权利要求书】(必须作为提取 parent_system 层级关系的依据)\n{claims_full_str}\n"
        parts.append(text)
        current_length += len(text)

        # 4. 具体实施方式 (功能描述来源，内容最长，优先级低，填补剩余空间)
        detailed_desc = patent_data.get("description", {}).get("detailed_description", "")
        
        remaining_chars = max_chars - current_length
        if remaining_chars > 500 and detailed_desc:
            if remaining_chars < len(detailed_desc):
                # 截取剩余空间，但确保不截断得太离谱
                truncated_detail = detailed_desc[:remaining_chars]
                text = f"### 【具体实施方式】(用于提取 function 描述)\n{truncated_detail}...\n(下文已截断)"
            else:
                text = f"### 【具体实施方式】(用于提取 function 描述)\n{detailed_desc}\n"
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
你是一个精通专利结构拆解的专家。请分析提供的专利片段，构建一个精确的“技术构件实体关系映射表”。

**输入数据源说明：**
1.  **【附图说明】**：这是“编号-名称”的权威字典。若文中出现 `10-电机`，则编号`"10"`的标准名称必须是`"电机"`。
2.  **【权利要求书】**：这是推断“包含关系”的法律依据。若描述“A包括B”或“A上设置有B”，则 A 是 B 的 `parent_system`。
3.  **【具体实施方式】**：这是提取“具体功能”的来源。
4.  **【摘要】**：辅助理解顶级发明的功能。

**输出格式要求：**
输出一个单一的 JSON 对象，Key 为**字符串格式的编号**，Value 为详细信息对象。

**Value 字段定义与提取规则（严格执行）：**
*   `"name"` (string): 构件的标准名称。优先使用【附图说明】中的命名。
*   `"function"` (string): **基于动作/目的的描述**。
    *   *必须*寻找：“用于...”、“用以...”、“从而实现...”、“能够...”等引导的语句。
    *   *禁止*同义反复（如：错误="这是一个底座"；正确="用于固定支撑整个装置，并连接液压杆"）。
*   `"parent_system"` (string | null): 
    *   父组件的编号。
    *   如果该组件是整个发明的核心顶级装置（无上级），或文本未提及上级，则为 `null`。
*   `"components"` (List[string] | null): 
    *   直接子组件的编号列表。若无子组件则为 `null`。

**处理逻辑与约束：**
1.  **去噪**：忽略非实体的编号（如步骤S1、时间t1、角度α1、实施例1、对比文件1）。
2.  **去重**：同一编号只输出一次。
3.  **一致性**：`parent_system` 和 `components` 必须逻辑互通（即：若 A 的子组件有 B，则 B 的父组件必须是 A）。
4.  **完整性**：提取文中出现的所有带有编号的实体构件。

**JSON 输出示例：**
```json
{
  "1": {
    "name": "桥梁检测机器人",
    "function": "用于对桥梁底部进行自动化巡检和污垢清除",
    "parent_system": null,
    "components": ["10", "20"]
  },
  "10": {
    "name": "移动底盘",
    "function": "带动整个装置沿桥面移动",
    "parent_system": "1",
    "components": ["101"]
  }
}
```
"""

        try:
            logger.info("[Knowledge] Sending request to LLM...")
            data = self.llm_service.chat_completion_json(
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