"""
专利文档数据模型
定义专利文档的结构化数据模型，用于验证和格式化解析结果
"""

from typing import List, Optional, Literal
from pydantic import BaseModel, Field


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
    invention_title: str = Field(..., description="发明/实用新型/外观设计名称")
    ipc_classifications: List[str] = Field(..., description="IPC 国际专利分类号列表")
    applicants: List[EntityInfo] = Field(..., description="申请人列表")
    inventors: List[str] = Field(..., description="发明人姓名列表")
    agency: Optional[PatentAgency] = Field(None, description="专利代理机构与代理人")
    abstract: str = Field(..., description="摘要纯文本")
    abstract_figure: Optional[str] = Field(None, description="摘要附图的图片链接")


class PatentClaim(BaseModel):
    claim_id: str = Field("", description="权利要求编号")
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
