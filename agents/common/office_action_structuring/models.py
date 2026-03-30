"""
审查意见通知书数据模型
定义审查意见通知书的结构化数据模型，用于验证和格式化解析结果
"""

from typing import List, Optional
from pydantic import BaseModel, Field


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
    cited_doc_ids: List[str] = Field(default_factory=list, description="该段明确引用的对比文件编号，如 D1 / D2")
    content: str = Field(..., description="段落内容")


class OfficeAction(BaseModel):
    """审查意见通知书结构化数据"""
    application_number: str = Field(..., description="原专利申请号")
    current_notice_round: int = Field(..., description="当前上传通知书轮次，如 1/2/3")
    comparison_documents: List[ComparisonDocument] = Field(default_factory=list, description="对比文件列表")
    paragraphs: List[OfficeActionParagraph] = Field(default_factory=list, description="审查意见通知书段落内容")
