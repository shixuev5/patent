"""
审查意见通知书数据模型
定义审查意见通知书的结构化数据模型，用于验证和格式化解析结果
"""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class ParagraphEvaluation(str, Enum):
    """审查意见段落结论倾向。"""

    NEGATIVE = "negative"
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class ComparisonDocument(BaseModel):
    """对比文件信息"""
    document_id: str = Field(..., description="对比文件编号，如 D1、D2")
    document_number: str = Field(..., description="对比文件号或名称")
    is_patent: bool = Field(..., description="是否为专利文献（True=专利，False=非专）")
    publication_date: Optional[str] = Field(None, description="公开日期或抵触申请的申请日")


class OfficeActionParagraph(BaseModel):
    """审查意见通知书段落内容"""
    paragraph_id: str = Field(..., description="段落编号，如 Claim1、Claim2")
    claim_ids: List[str] = Field(default_factory=list, description="关联权利要求编号列表，如 [\"1\", \"2\"]")
    legal_basis: List[str] = Field(default_factory=list, description="法律依据，如 A22.3 / R20.1")
    issue_types: List[str] = Field(default_factory=list, description="缺陷类型，如 创造性 / 清楚")
    cited_doc_ids: List[str] = Field(default_factory=list, description="该段明确引用的对比文件编号，如 D1 / D2")
    evaluation: ParagraphEvaluation = Field(ParagraphEvaluation.UNKNOWN, description="该段结论倾向")
    content: str = Field(..., description="段落内容")


class OfficeAction(BaseModel):
    """审查意见通知书结构化数据"""
    application_number: str = Field(..., description="原专利申请号")
    comparison_documents: List[ComparisonDocument] = Field(default_factory=list, description="对比文件列表")
    paragraphs: List[OfficeActionParagraph] = Field(default_factory=list, description="审查意见通知书段落内容")
