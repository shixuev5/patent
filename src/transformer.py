import json
from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from loguru import logger
from openai import OpenAI
from config import settings

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
    application_number: str = Field(..., description="[INID 21] 申请号，需去除空格和CN前缀")
    filing_date: str = Field(..., description="[INID 22] 申请日，统一格式为 YYYY.MM.DD")
    invention_title: str = Field(..., description="[INID 54] 发明名称")
    ipc_classifications: List[str] = Field(..., description="[INID 51] IPC 国际专利分类号列表")
    applicants: List[EntityInfo] = Field(..., description="[INID 71] 申请人列表")
    inventors: List[str] = Field(..., description="[INID 72] 发明人姓名列表")
    agency: Optional[PatentAgency] = Field(None, description="[INID 74] 专利代理机构与代理人")
    abstract: str = Field(..., description="[INID 57] 摘要文本")

class PatentClaim(BaseModel):
    """单项权利要求"""
    claim_number: int = Field(..., description="权利要求编号")
    claim_text: str = Field(..., description="权利要求的内容文本（必须去除开头的编号）")
    claim_type: Literal["independent", "dependent"] = Field(
        ..., 
        description="类型判定：内容包含'根据权利要求...所述'为 dependent (从属)，否则为 independent (独立)"
    )

class DescriptionSection(BaseModel):
    """说明书各章节内容"""
    technical_field: str = Field(..., description="技术领域")
    background_art: str = Field(..., description="背景技术")
    summary_of_invention: str = Field(..., description="发明内容")
    brief_description_of_drawings: str = Field(..., description="附图说明（仅提取文字描述）")
    detailed_description: str = Field(..., description="具体实施方式")

class DrawingResource(BaseModel):
    """附图资源引用"""
    file_path: str = Field(..., description="Markdown图片链接地址")
    figure_label: str = Field(..., description="图号标签（如'图1'）")
    caption: Optional[str] = Field(None, description="从'附图说明'中匹配到的该图的具体解释")

class PatentDocument(BaseModel):
    """
    专利文档根对象
    """
    bibliographic_data: BibliographicData
    claims: List[PatentClaim]
    description: DescriptionSection
    drawings: List[DrawingResource]


class PatentTransformer:
    # 系统提示词主要负责“清洗逻辑”和“边界情况处理”，结构定义交给 Pydantic
    SYSTEM_PROMPT = """你是一位资深的知识产权数据专家。你的任务是将输入的专利 Markdown 文本解析为结构化数据。

### 处理原则：
1. **去噪**：忽略所有的页码（如 "第1页/共5页"）、页眉、页脚信息。
2. **完整性**：如果某个字段在文中完全缺失，请返回 null 或空列表，严禁编造数据。
3. **文本清洗**：
   - 在提取 `detailed_description` (具体实施方式) 时，保留段落编号（如 [0023]）。
   - 在提取 `claim_text` 时，必须移除开头的数字编号。
4. **章节识别**：说明书的标题可能存在变体（如 "1. 技术领域" 或 "[技术领域]"），请根据语义灵活切分。

请严格根据提供的 Schema 提取信息。
"""

    def __init__(self, client: OpenAI):
        self.client = client

    def transform(self, md_content: str) -> dict:
        """
        使用 Structured Outputs 将 Markdown 解析为 Pydantic 对象并转为 dict
        """
        logger.info("[Transformer] Starting Structured Output parsing...")
        
        try:
            # 使用 beta.chat.completions.parse 接口
            completion = self.client.beta.chat.completions.parse(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": md_content},
                ],
                response_format=PatentDocument, # 直接传入 Pydantic 类
                temperature=0.1, # 低温度保持精确
            )

            # 获取解析后的 Pydantic 对象
            patent_obj = completion.choices[0].message.parsed
            
            # 转为字典返回
            result_dict = patent_obj.model_dump()
            
            logger.success(f"[Transformer] Successfully parsed patent: {patent_obj.bibliographic_data.application_number}")
            return result_dict

        except Exception as e:
            # 注意：如果模型输出拒绝解析（refusal），这里也会捕获
            logger.error(f"[Transformer] Parsing failed: {e}")
            # 这里的 fallback 逻辑可以根据需求处理，比如降级为普通 JSON 模式
            raise e

# 使用示例
if __name__ == "__main__":    
    # 模拟客户端
    # client = OpenAI(api_key="...")
    # transformer = PatentTransformer(client)
    # data = transformer.transform(md_content="...")
    pass