"""
审查意见通知书结构化处理模块
提供审查意见通知书的结构化提取和处理功能
"""

from agents.common.office_action_structuring.rule_based_extractor import OfficeActionExtractor
from agents.common.office_action_structuring.models import (
    OfficeAction,
    ComparisonDocument,
    OfficeActionParagraph
)

__all__ = [
    "OfficeActionExtractor",
    "OfficeAction",
    "ComparisonDocument",
    "OfficeActionParagraph"
]
