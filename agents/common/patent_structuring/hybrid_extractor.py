"""
混合抽取模式的专利文档结构化提取器
优先基于规则进行抽取，只有当关键字段内容缺失时，才使用LLM进行完整抽取
"""

from loguru import logger
from agents.common.patent_structuring.rule_based_extractor import RuleBasedExtractor
from agents.common.patent_structuring.llm_based_extractor import LLMBasedExtractor


class HybridExtractor:
    """混合抽取模式的专利文档结构化提取器"""

    def __init__(self):
        self.rule_extractor = RuleBasedExtractor()
        self.llm_extractor = LLMBasedExtractor()

    def extract(self, md_content: str) -> dict:
        """
        混合抽取专利文档的结构化信息

        Args:
            md_content: 专利文档的 Markdown 内容

        Returns:
            结构化的专利数据字典
        """
        logger.info("混合抽取开始：优先规则抽取")

        # 首先使用规则抽取器进行抽取
        rule_result = RuleBasedExtractor.extract(md_content)

        # 检查关键字段是否缺失
        missing_fields = self._check_missing_fields(rule_result)

        if missing_fields:
            if missing_fields:
                logger.warning(f"规则抽取存在关键字段缺失: {missing_fields}")
            logger.info("开始执行 LLM 全量结构化抽取")
            # 直接使用LLM进行完整抽取
            llm_result = self.llm_extractor.extract(md_content)
            logger.success("LLM 结构化抽取完成")
            return llm_result
        else:
            logger.success("规则抽取通过，直接返回规则结果")
            return rule_result

    def _check_missing_fields(self, patent_data: dict) -> list:
        """
        检查专利数据中是否有关键字段缺失

        Args:
            patent_data: 专利结构化数据字典

        Returns:
            缺失字段的路径列表
        """
        missing_fields = []

        # 定义需要检查的关键字段（按层级结构）
        required_fields = {
            "bibliographic_data": [
                "application_number", "application_date", "invention_title",
                "ipc_classifications", "applicants", "inventors", "abstract"
            ],
            "claims": [],  # claims是列表，检查是否为空及内容完整性
            "description": [
                "technical_field", "background_art", "summary_of_invention",
                "detailed_description"
            ],
            # drawings 和 brief_description_of_drawings 字段可能缺失，不强制检查
        }

        # 检查各个字段
        for section, fields in required_fields.items():
            if section not in patent_data or not patent_data[section]:
                missing_fields.append(section)
                continue

            # 检查子字段
            if fields:
                for field in fields:
                    if field not in patent_data[section] or not patent_data[section][field]:
                        missing_fields.append(f"{section}.{field}")

        # 检查claims列表是否为空或内容不完整
        if "claims" in patent_data:
            if not patent_data["claims"]:
                missing_fields.append("claims")
            else:
                for i, claim in enumerate(patent_data["claims"]):
                    if not claim.get("claim_text") or not claim.get("claim_type"):
                        missing_fields.append(f"claims[{i}]")

        return missing_fields
