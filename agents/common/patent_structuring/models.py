"""
专利文档数据模型
定义专利文档的结构化数据模型，用于验证和格式化解析结果
"""

import re
from typing import Any, List, Optional, Literal
from pydantic import BaseModel, Field, field_validator, model_validator

from agents.common.patent_structuring.date_utils import parse_common_date_string


class PatentStructuringBaseModel(BaseModel):
    @model_validator(mode="before")
    @classmethod
    def _normalize_null_string_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        for field_name, field_info in cls.model_fields.items():
            if field_info.annotation is str and field_name in normalized and normalized[field_name] is None:
                normalized[field_name] = ""
        return normalized

    @classmethod
    def _normalize_date_string(cls, value: Any) -> Any:
        text = str(value or "").strip()
        if not text:
            return ""

        normalized = parse_common_date_string(text)
        if normalized:
            return normalized

        match = re.search(r"\((\d{4})\.(\d{1,2})\.(\d{1,2})\)", text)
        if match:
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            if 1 <= month <= 12 and 1 <= day <= 31:
                return f"{year:04d}.{month:02d}.{day:02d}"

        return text


class EntityInfo(PatentStructuringBaseModel):
    name: str = Field(..., description="实体名称（申请人或机构名）")
    address: str = Field("", description="地址信息；缺失时为空字符串")


class PatentAgency(PatentStructuringBaseModel):
    agency_name: str = Field(..., description="代理机构全称")
    agents: List[str] = Field(default_factory=list, description="代理师/代理人姓名列表")


class BibliographicData(PatentStructuringBaseModel):
    application_number: str = Field(..., description="申请号")
    application_date: str = Field(..., description="申请日")
    priority_date: str = Field("", description="最早优先权日；缺失时为空字符串")
    publication_number: str = Field("", description="申请公布号或授权公告号；缺失时为空字符串")
    publication_date: str = Field("", description="申请公布日或授权公告日；缺失时为空字符串")
    invention_title: str = Field(..., description="发明/实用新型/外观设计名称")
    ipc_classifications: List[str] = Field(..., description="IPC 国际专利分类号列表")
    applicants: List[EntityInfo] = Field(..., description="申请人列表")
    inventors: List[str] = Field(..., description="发明人姓名列表")
    agency: Optional[PatentAgency] = Field(None, description="专利代理机构与代理人")
    abstract: str = Field(..., description="摘要纯文本")
    abstract_figure: str = Field("", description="摘要附图的图片链接；缺失时为空字符串")

    @field_validator("application_date", "priority_date", "publication_date", mode="before")
    @classmethod
    def _normalize_date_fields(cls, value: Any) -> str:
        normalized = cls._normalize_date_string(value)
        return str(normalized or "")


class PatentClaim(PatentStructuringBaseModel):
    claim_id: str = Field("", description="权利要求编号")
    claim_text: str = Field(..., description="权利要求纯文本，不包含序号")
    claim_type: Literal["independent", "dependent"] = Field(..., description="独立或从属权利要求")
    parent_claim_ids: List[str] = Field(default_factory=list, description="直接父权利要求编号列表（仅从属权利要求有值）")


class DescriptionSection(PatentStructuringBaseModel):
    technical_field: str = Field(..., description="技术领域")
    background_art: str = Field(..., description="背景技术")
    summary_of_invention: str = Field(..., description="发明内容（仅保留技术方案部分）")
    technical_effect: str = Field("", description="有益效果/技术效果；缺失时为空字符串")
    brief_description_of_drawings: str = Field("", description="仅提取附图标记说明列表（如 1-定子），绝不包含图解描述；缺失时为空字符串")
    detailed_description: str = Field(..., description="具体实施方式")


class DrawingResource(PatentStructuringBaseModel):
    file_path: str = Field(..., description="附图的图片链接")
    figure_label: str = Field(..., description="图号标签（如'图1'）")
    caption: str = Field("", description="图的文字解释；缺失时为空字符串")


class PatentDocument(PatentStructuringBaseModel):
    bibliographic_data: BibliographicData
    claims: List[PatentClaim]
    description: DescriptionSection
    drawings: List[DrawingResource]
