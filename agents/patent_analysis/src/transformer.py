import re
import json
from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from loguru import logger
from agents.patent_analysis.src.utils.llm import get_llm_service
from config import Settings


class EntityInfo(BaseModel):
    name: str = Field(..., description="实体名称（申请人或机构名）")
    address: Optional[str] = Field(None, description="地址信息")

class PatentAgency(BaseModel):
    agency_name: str = Field(..., description="代理机构全称")
    agents: List[str] = Field(default_factory=list, description="代理师/代理人姓名列表")

class BibliographicData(BaseModel):
    application_number: str = Field(..., description="申请号")
    application_date: str = Field(..., description="申请日")
    priority_date: Optional[str] = Field(None, description="最早优先权日")
    publication_number: Optional[str] = Field(None, description="申请公布号或授权公告号")
    publication_date: Optional[str] = Field(None, description="申请公布日或授权公告日")
    invention_title: str = Field(..., description="发明名称")
    ipc_classifications: List[str] = Field(..., description="IPC 国际专利分类号列表")
    applicants: List[EntityInfo] = Field(..., description="申请人列表")
    inventors: List[str] = Field(..., description="发明人姓名列表")
    agency: Optional[PatentAgency] = Field(None, description="专利代理机构与代理人")
    abstract: str = Field(..., description="摘要纯文本")
    abstract_figure: Optional[str] = Field(None, description="摘要附图的图片链接")

class PatentClaim(BaseModel):
    claim_text: str = Field(..., description="权利要求纯文本，不包含序号")
    claim_type: Literal["independent", "dependent"] = Field(..., description="独立或从属权利要求")

class DescriptionSection(BaseModel):
    technical_field: str = Field(..., description="技术领域")
    background_art: str = Field(..., description="背景技术")
    summary_of_invention: str = Field(..., description="发明内容（仅保留技术方案部分）")
    technical_effect: Optional[str] = Field(None, description="有益效果/技术效果，若无则为 null")
    brief_description_of_drawings: Optional[str] = Field(None, description="仅提取附图标记说明列表（如 1-定子），绝不包含图解描述。若无则为 null")
    detailed_description: str = Field(..., description="具体实施方式")

class DrawingResource(BaseModel):
    file_path: str = Field(..., description="附图的图片链接")
    figure_label: str = Field(..., description="图号标签（如'图1'）")
    caption: Optional[str] = Field(None, description="图的文字解释")

class PatentDocument(BaseModel):
    bibliographic_data: BibliographicData
    claims: List[PatentClaim]
    description: DescriptionSection
    drawings: List[DrawingResource]


class PatentTransformer:
    def __init__(self):
        self.llm_service = get_llm_service()

    def _get_system_prompt(self):
        return r"""你是一个精通专利文档结构的资深AI分析师。你的任务是将杂乱的Markdown专利文本解析为结构化、干净的JSON格式。

### 核心清洗指令 (Critical Cleaning Rules) - 必须严格执行！
1. **全局去噪 (Noise Removal)**：
   - 必须彻底删除所有类似 `[0001]`、`[0025]` 的段落编号。
   - 必须删除所有的页码（如 "第1页/共5页"）、页眉、页脚信息。
2. **权利要求清洗 (Claims Cleaning)**：
   - `claim_text` 必须去除开头的序号和标点！(例如原文是 "1. 一种装置..." 或 "2、根据权利要求1..."，提取后必须变成 "一种装置..." 和 "根据权利要求1...")。
3. **附图标记说明 (Brief Description of Drawings) 提取规则**：
   - 此字段**仅用于**提取类似 "1-定子，2-转子" 或 "101：处理器" 的**部件标号说明**。
   - **严禁**提取 "图1是...的示意图" 这类图解说明文字。
   - 如果原文中根本没有部件标号说明列表，该字段**必须返回 null**，绝不可用图解说明文字充数。
4. **附图标题清洗 (Drawing Captions)**：
   - `drawings.caption` 必须去除开头的图号（如“图1”）及紧跟的谓语动词/连接词（如“为”、“是”、“示出”、“：”）。例如原文“图1是装置结构图”，提取后必须为“装置结构图”。
5. **公式与特殊符号 (LaTeX Rules)**：
   - 完整保留所有的 LaTeX 公式（`$$...$$` 或 `$...$`）。
   - **JSON转义铁律**：原文中所有的 LaTeX 反斜杠 `\` 必须转义为双反斜杠 `\\` (例如 `$120 \\mathrm{{mm}}$`)。

### 字段边界识别规则
- **summary_of_invention (发明内容)**：仅保留技术方案本体，遇到“本发明的有益效果”或“技术效果”时必须截断。
- **technical_effect (有益效果)**：单独提取“有益效果”段落，若文中未明确写出，则返回 null。
- **claim_type (权利要求类型)**：即使内容提到其他权利要求（如“一种用于权利要求1所述装置的方法”），只要不以“根据/如权利要求X所述”开头，就是独立(independent)权利要求。

### 输出格式参考
请严格按照以下结构输出：
```json
{
  "bibliographic_data": {
    "application_number": "202310001234.5",
    "application_date": "2023.01.01",
    "priority_date": null,
    "publication_number": "CN116793681A",
    "publication_date": "2024.03.20",
    "invention_title": "一种基于磁纳米粒子法拉第磁光效应的温度测量方法",
    "ipc_classifications": ["G01K 7/36"],
    "applicants": [{"name": "华中科技大学", "address": "湖北省武汉市..."}] ,
    "inventors": ["张三", "李四"],
    "agency": {"agency_name": "某专利中心", "agents": ["王五"]},
    "abstract": "本发明公开了一种基于磁纳米粒子...",
    "abstract_figure": "images/xxx.jpg"
  },
  "claims": [
    {
      "claim_text": "一种基于磁纳米粒子法拉第磁光效应的温度测量方法，包括...",
      "claim_type": "independent"
    },
    {
      "claim_text": "根据权利要求1所述的方法，其特征在于...",
      "claim_type": "dependent"
    }
  ],
  "description": {
    "technical_field": "本发明属于纳米材料测试技术领域...",
    "background_art": "温度是反映生命活动状态的重要指标...",
    "summary_of_invention": "针对现有技术的以上缺陷，本发明提供了一种...",
    "technical_effect": "本发明使用法拉第磁光效应对温度进行测量，可以实现...",
    "brief_description_of_drawings": null,
    "detailed_description": "为了使本发明的目的、技术方案及优点更加清楚明白..."
  },
  "drawings": [
    {
      "file_path": "images/figure1.jpg",
      "figure_label": "图1",
      "caption": "法拉第磁光效应测温装置示意图"
    }
  ]
}
```
"""

    @staticmethod
    def preprocess_patent_text(text: str) -> str:
        """在输入给 LLM 前进行正则清洗，降低 LLM 认知负担并节约 Token"""
        
        # 1. 移除专利局标准的段落号，例如 [0001], [0012] 等
        text = re.sub(r'\[\d{4}\]\s*', '', text)
        
        return text.strip()

    def transform(self, md_content: str) -> dict:
        """
        使用 Structured Outputs 将 Markdown 解析为 Pydantic 对象并转为 dict
        """
        logger.info("[Transformer] Starting Structured Output parsing...")
        
        cleaned_content = self.preprocess_patent_text(md_content)

        try:
            json_data = self.llm_service.chat_completion_json(
                model=Settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": self._get_system_prompt()},
                    {"role": "user", "content": cleaned_content},
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
