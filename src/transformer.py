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
    abstract: str = Field(..., description="[INID 57] 摘要纯文本，不包含图片链接")
    abstract_figure: Optional[str] = Field(
        None, 
        description="摘要附图的图片链接，通常位于摘要文字下方"
    )

class PatentClaim(BaseModel):
    """单项权利要求"""
    claim_number: int = Field(..., description="权利要求编号")
    claim_text: str = Field(..., description="权利要求内容。保留所有数学公式(LaTeX/$$)和特殊符号，去除开头编号。")
    claim_type: Literal["independent", "dependent"] = Field(
        ..., 
        description="类型判定：内容包含'根据权利要求...所述'为 dependent (从属)，否则为 independent (独立)"
    )

class DescriptionSection(BaseModel):
    """说明书各章节内容"""
    technical_field: str = Field(..., description="技术领域")
    background_art: str = Field(..., description="背景技术")
    summary_of_invention: str = Field(..., description="发明内容，保留公式")
    brief_description_of_drawings: str = Field(..., description="附图说明（仅提取文字描述）")
    detailed_description: str = Field(..., description="具体实施方式，保留段落编号[00xx]和公式")

class DrawingResource(BaseModel):
    """附图资源引用"""
    file_path: str = Field(..., description="Markdown图片链接")
    figure_label: str = Field(..., description="图号标签（如'图1'），从图片下方文字或附图说明中提取")
    caption: Optional[str] = Field(None, description="从'附图说明'章节匹配到的该图的具体文字解释")

class PatentDocument(BaseModel):
    """
    专利文档根对象
    """
    bibliographic_data: BibliographicData
    claims: List[PatentClaim]
    description: DescriptionSection
    drawings: List[DrawingResource]


class PatentTransformer:
    def __init__(self, client: OpenAI):
        self.client = client
        # 预先生成 JSON Schema 字符串
        self.json_schema = json.dumps(PatentDocument.model_json_schema(), ensure_ascii=False, indent=2)

    def _get_system_prompt(self):
        return f"""你是一个精通专利文档结构的AI助手。请将Markdown文本解析为JSON。

### 处理原则：
1. **去噪**：忽略所有的页码（如 "第1页/共5页"）、页眉、页脚信息。
2. **公式保留**：严禁修改或删除文本中的 LaTeX 公式（如 $$...$$）或数学符号。
3. **完整性**：如果某个字段在文中完全缺失，请返回 null 或空列表，严禁编造数据。
4. **文本清洗**：
    - `abstract`: 仅提取文本，不要包含 Markdown 图片链接。
    - `claim_text`: 必须去除行首的序号（如 "1."），但保留内部引用的序号。
5. **图像识别**：
   - 如果图片出现在【摘要】部分，提取到 `bibliographic_data.abstract_figure`。
   - 如果图片出现在【附图说明】或【文档末尾】，提取到 `drawings` 列表。
   - 图片链接（仅提取URL，不包含![]部分）。
6. **章节识别**：说明书的标题可能存在变体（如 "1. 技术领域" 或 "[技术领域]"），请根据语义灵活切分。

### 输出格式要求
请严格遵守以下 JSON Schema 输出 JSON 数据：

```json
{self.json_schema}
```
"""

    def transform(self, md_content: str) -> dict:
        """
        使用 Structured Outputs 将 Markdown 解析为 Pydantic 对象并转为 dict
        """
        logger.info("[Transformer] Starting Structured Output parsing...")
        
        try:
            response = self.client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": self._get_system_prompt()},
                    {"role": "user", "content": md_content},
                ],
                response_format={"type": "json_object"},
                temperature=0.1, # 低温度保持精确
            )

            # 1. 获取原始内容
            content = response.choices[0].message.content
            
            # 2. 解析 JSON 字符串
            try:
                json_data = json.loads(content)
            except json.JSONDecodeError:
                logger.error("Model output is not valid JSON")
                raise ValueError("Model output is not valid JSON")

            # 3. 使用 Pydantic 进行结构校验和类型转换
            patent_obj = PatentDocument.model_validate(json_data)
            
            # 4. 转回字典
            result_dict = patent_obj.model_dump()
                
            logger.success(f"[Transformer] Successfully parsed patent: {patent_obj.bibliographic_data.application_number}")
            return result_dict

        except Exception as e:
            logger.error(f"[Transformer] Parsing failed: {e}")
            raise e

# 使用示例
if __name__ == "__main__":    
    # 模拟客户端
    # client = OpenAI(api_key="...")
    # transformer = PatentTransformer(client)
    # data = transformer.transform(md_content="...")
    pass