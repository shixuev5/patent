"""
专利结构化处理模块
提供基于 LLM、基于规则和混合模式的专利文档结构化提取功能
"""

from agents.common.patent_structuring.llm_based_extractor import LLMBasedExtractor
from agents.common.patent_structuring.rule_based_extractor import RuleBasedExtractor
from agents.common.patent_structuring.hybrid_extractor import HybridExtractor
import re
from typing import List, Dict


def extract_structured_data(md_content: str, method: str = "hybrid") -> dict:
    """
    专利文档结构化提取入口函数

    Args:
        md_content: 专利文档的 Markdown 内容
        method: 提取方法，可选 "llm"、"rule" 或 "hybrid"（默认）

    Returns:
        结构化的专利数据字典
    """
    if method == "llm":
        return LLMBasedExtractor().extract(md_content)
    elif method == "rule":
        return RuleBasedExtractor.extract(md_content)
    elif method == "hybrid":
        return HybridExtractor().extract(md_content)
    else:
        raise ValueError(f"Unknown extraction method: {method}")


def extract_structured_claims(md_content: str) -> List[Dict[str, str]]:
    """
    从新权利要求书文本中提取结构化权利要求：
    从以 1. 开始的位置，直到文本结尾，按序号切分。
    """
    content = str(md_content or "").replace("\r\n", "\n")
    start_match = re.search(r"(?m)^1\s*[\.．]\s*", content)
    if not start_match:
        return []
    claims_section = content[start_match.start():]
    return RuleBasedExtractor.extract_structured_claims(claims_section)
