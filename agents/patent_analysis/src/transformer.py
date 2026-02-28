import json
from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from loguru import logger
from agents.patent_analysis.src.utils.llm import get_llm_service
from config import Settings


class EntityInfo(BaseModel):
    """个人或机构实体信息"""

    name: str = Field(..., description="实体名称（申请人或机构名）")
    address: Optional[str] = Field(None, description="地址信息，若无则为 null")


class PatentAgency(BaseModel):
    """专利代理机构信息"""

    agency_name: str = Field(..., description="代理机构全称")
    agents: List[str] = Field(default_factory=list, description="代理师/代理人姓名列表")


class BibliographicData(BaseModel):
    """
    专利著录项目 (Bibliographic Data)
    对应 INID 码标准
    """

    application_number: str = Field(
        ..., description="[INID 21] 申请号，需去除空格和CN前缀"
    )
    application_date: str = Field(
        ..., description="[INID 22] 申请日，统一格式为 YYYY.MM.DD"
    )
    priority_date: Optional[str] = Field(
        None, description="[INID 30] 最早优先权日。统一格式为 YYYY.MM.DD"
    )
    publication_number: Optional[str] = Field(
        None, description="[INID 10] 申请公布号 (如 CN 116793681 A) 或 授权公告号"
    )
    publication_date: Optional[str] = Field(
        None,
        description="[INID 43] 申请公布日 或 [INID 45] 授权公告日。统一格式为 YYYY.MM.DD",
    )
    invention_title: str = Field(..., description="[INID 54] 发明名称")
    ipc_classifications: List[str] = Field(
        ..., description="[INID 51] IPC 国际专利分类号列表"
    )
    applicants: List[EntityInfo] = Field(..., description="[INID 71] 申请人列表")
    inventors: List[str] = Field(..., description="[INID 72] 发明人姓名列表")
    agency: Optional[PatentAgency] = Field(
        None, description="[INID 74] 专利代理机构与代理人"
    )
    abstract: str = Field(..., description="[INID 57] 摘要纯文本，不包含图片链接")
    abstract_figure: Optional[str] = Field(
        None, description="摘要附图的图片链接，通常位于摘要文字下方"
    )


class PatentClaim(BaseModel):
    """单项权利要求"""

    claim_text: str = Field(
        ...,
        description="权利要求内容。保留所有数学公式(LaTeX/$$)和特殊符号，去除开头编号。",
    )
    claim_type: Literal["independent", "dependent"] = Field(
        ...,
        description="""类型判定规则：
        1. independent (独立)：通常以 '一种...' (A/An...) 开头。注意：即使内容中提到了其他权利要求（例如'一种用于权利要求1所述装置的制造方法'），只要它定义的是一个新的技术主题且不以'根据...'开头，它就是独立权利要求。
        2. dependent (从属)：通常以 '根据权利要求X所述的...' (According to claim X...) 或 '如权利要求X所述的...' 开头，是对在前权利要求的进一步限定。
        """,
    )


class DescriptionSection(BaseModel):
    """说明书各章节内容"""

    technical_field: str = Field(..., description="技术领域")
    background_art: str = Field(..., description="背景技术")
    summary_of_invention: str = Field(
        ...,
        description="发明内容（技术方案部分）。**注意**：请在此处截断，不要包含'有益效果'或'技术效果'的相关段落。",
    )
    technical_effect: str = Field(
        ...,
        description="有益效果/技术效果。提取'发明内容'章节末尾关于'本发明具有如下有益效果'或'技术效果'的描述段落。若无明确描述则为null。",
    )
    brief_description_of_drawings: str = Field(
        ..., description="附图标记说明（标号说明）。请提取位于'附图说明'章节末尾或说明书全文末尾的附图标记对应的名称列表。**注意**：严禁包含'附图说明'章节中关于'图1是...'、'图2是...'的图解说明文字。"
    )
    detailed_description: str = Field(
        ...,
        description="具体实施方式。保留所有数学公式(LaTeX/$$)和特殊符号，去除开头编号。",
    )


class DrawingResource(BaseModel):
    """附图资源引用"""

    file_path: str = Field(..., description="附图的图片链接")
    figure_label: str = Field(
        ..., description="图号标签（如'图1'），从图片下方文字或附图说明中提取"
    )
    caption: Optional[str] = Field(
        None,
        description="图的文字解释。必须去除开头的图号（如“图1”）及紧跟图号的谓语动词或连接词（包括但不限于：“为”、“是”、“示出”、“示出了”、“显示”、“表示”、“：”），只保留描述内容。",
    )


class PatentDocument(BaseModel):
    """
    专利文档根对象
    """

    bibliographic_data: BibliographicData
    claims: List[PatentClaim]
    description: DescriptionSection
    drawings: List[DrawingResource]


class PatentTransformer:
    def __init__(self):
        self.llm_service = get_llm_service()

    def _get_system_prompt(self):
        return r"""你是一个精通专利文档结构的AI助手。请将Markdown文本解析为符合专利文档结构的JSON格式。

### 核心指令 (Critical Instructions)
1. **去噪**：忽略所有的页码（如 "第1页/共5页"）、页眉、页脚信息。
2. **公式转义与保留**：
   - 严禁修改或删除文本中的 LaTeX 公式（如 `$$...$$` 或 `$...$`）。
   - **JSON转义铁律**：在生成 JSON 字符串时，原文中所有的 LaTeX 反斜杠 `\` 必须转义为双反斜杠 `\\`。
     - 错误示例：`"content": "$120 \mathrm{{mm}}$"` (会导致 JSON 解析错误)
     - 正确示例：`"content": "$120 \\mathrm{{mm}}$"`
3. **完整性**：如果某个字段在文中完全缺失，请返回 null 或空列表，严禁编造数据。
4. **章节识别**：说明书的标题可能存在变体（如 "1. 技术领域" 或 "[技术领域]"），请根据语义灵活切分。

### 输出格式要求
请严格按照以下 JSON 示例格式输出：

```json
{
  "bibliographic_data": {
    "application_number": "202310001234.5",
    "application_date": "2023.01.01",
    "priority_date": null,
    "publication_number": "CN116793681A",
    "publication_date": "2024.03.20",
    "invention_title": "一种高效节能的电动机",
    "ipc_classifications": ["H02K5/04", "H02K7/00"],
    "applicants": [{"name": "某科技有限公司", "address": "北京市海淀区"}] ,
    "inventors": ["张三", "李四"],
    "agency": {"agency_name": "某专利代理事务所", "agents": ["王五"]},
    "abstract": "本发明公开了一种高效节能的电动机...",
    "abstract_figure": null
  },
  "claims": [
    {
      "claim_text": "一种高效节能的电动机，包括定子、转子和外壳...",
      "claim_type": "independent"
    },
    {
      "claim_text": "根据权利要求1所述的电动机，其特征在于：所述定子包括...",
      "claim_type": "dependent"
    }
  ],
  "description": {
    "technical_field": "本发明涉及电动机技术领域...",
    "background_art": "目前的电动机存在效率低、能耗高等问题...",
    "summary_of_invention": "本发明提供了一种高效节能的电动机...",
    "technical_effect": "本发明具有高效、节能、噪音低等优点...",
    "brief_description_of_drawings": "1-定子，2-转子，3-外壳，4-绕组...",
    "detailed_description": "下面结合附图对本发明的具体实施方式进行详细描述..."
  },
  "drawings": [
    {
      "file_path": "figures/figure1.jpg",
      "figure_label": "图1",
      "caption": "电动机整体结构示意图"
    }
  ]
}
```

### 字段约束说明
- **bibliographic_data**：包含申请号、申请日、发明名称、申请人等专利著录信息
- **claims**：权利要求列表，每个权利要求包含内容和类型（独立/从属）
- **description**：说明书各章节内容，包括技术领域、背景技术、发明内容、技术效果等
- **drawings**：附图资源引用，包括文件路径、图号标签和说明文字
"""

    def transform(self, md_content: str) -> dict:
        """
        使用 Structured Outputs 将 Markdown 解析为 Pydantic 对象并转为 dict
        """
        logger.info("[Transformer] Starting Structured Output parsing...")

        try:
            json_data = self.llm_service.chat_completion_json(
                model=Settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": self._get_system_prompt()},
                    {"role": "user", "content": md_content},
                ],
                temperature=0.1,  # 低温度保持精确
            )

            # 使用 Pydantic 进行结构校验和类型转换
            patent_obj = PatentDocument.model_validate(json_data)

            # 转回字典
            result_dict = patent_obj.model_dump()

            logger.success(
                f"[Transformer] Successfully parsed patent: {patent_obj.bibliographic_data.application_number}"
            )
            return result_dict

        except Exception as e:
            logger.error(f"[Transformer] Parsing failed: {e}")
            # 提供更详细的错误信息
            logger.error(f"[Transformer] Error type: {type(e).__name__}")
            if 'json_data' in locals():
                logger.error(f"[Transformer] Invalid JSON data: {json.dumps(json_data, ensure_ascii=False, indent=2)}")
            raise e


# 使用示例
if __name__ == "__main__":
    # 模拟使用
    # transformer = PatentTransformer()
    # print(transformer._get_system_prompt())
    pass
