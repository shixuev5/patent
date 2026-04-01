"""
混合抽取模式的专利文档结构化提取器
优先基于规则进行抽取，只有当关键字段内容缺失时，才使用LLM进行完整抽取
"""

import re
from typing import List

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
        quality_issues = self._check_quality_issues(md_content, rule_result)

        if missing_fields or quality_issues:
            if missing_fields:
                logger.warning(f"规则抽取存在关键字段缺失: {missing_fields}")
            if quality_issues:
                logger.warning(f"规则抽取存在质量风险: {quality_issues}")
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
        检查专利数据中是否有核心必填字段缺失。
        对多法域文档放宽非关键字段，避免无优先权、无代理、无技术效果等合法空值误触发回退。

        Args:
            patent_data: 专利结构化数据字典

        Returns:
            缺失字段的路径列表
        """
        missing_fields = []

        required_fields = {
            "bibliographic_data": [
                "application_number",
                "application_date",
                "invention_title",
                "abstract",
            ],
            "description": [
                "detailed_description",
            ],
        }

        for section, fields in required_fields.items():
            section_data = patent_data.get(section)
            if not section_data:
                missing_fields.append(section)
                continue
            for field in fields:
                value = section_data.get(field) if isinstance(section_data, dict) else None
                if not str(value or "").strip():
                    missing_fields.append(f"{section}.{field}")

        applicants = patent_data.get("bibliographic_data", {}).get("applicants", [])
        if not applicants or not any(str((item or {}).get("name", "")).strip() for item in applicants):
            missing_fields.append("bibliographic_data.applicants")

        claims = patent_data.get("claims", [])
        if not claims:
            missing_fields.append("claims.empty")
        else:
            for i, claim in enumerate(claims):
                if not str(claim.get("claim_text", "")).strip() or not str(claim.get("claim_type", "")).strip():
                    missing_fields.append(f"claims[{i}].claim_text_or_type")
                    break

                parent_claim_ids = claim.get("parent_claim_ids")
                if str(claim.get("claim_type", "")).strip().lower() == "dependent":
                    if not isinstance(parent_claim_ids, list):
                        missing_fields.append(f"claims[{i}].parent_claim_ids_invalid")
                        break
                    if not parent_claim_ids:
                        missing_fields.append(f"claims[{i}].parent_claim_ids")
                        break

        return missing_fields

    def _check_quality_issues(self, md_content: str, patent_data: dict) -> List[str]:
        """
        检查规则抽取结果是否存在明显污染、越界或 OCR 脏数据风险。
        这些问题即使字段非空，也应该回退到 LLM。
        """
        issues: List[str] = []

        if not isinstance(patent_data, dict):
            return ["patent_data.invalid"]

        bibliographic = patent_data.get("bibliographic_data") or {}
        claims = patent_data.get("claims") or []
        description = patent_data.get("description") or {}

        suspicious_marker_pattern = re.compile(
            r"(?:"
            r"\(\d{2}\)|"
            r"#\s*\(|"
            r"【(?:請求項|背景技術|特許|発明)】|"
            r"청구항\s*\d+|"
            r"FIG\.?\s*\d+|"
            r"Figure\s*\d+|"
            r"Publication Classification|"
            r"EUROPEAN SEARCH REPORT|"
            r"CROSS-REFERENCE TO RELATED"
            r")",
            re.IGNORECASE,
        )

        title = str(bibliographic.get("invention_title") or "").strip()
        if len(title) > 300:
            issues.append("bibliographic_data.invention_title.too_long")
        elif title and suspicious_marker_pattern.search(title):
            issues.append("bibliographic_data.invention_title.polluted")

        applicants = bibliographic.get("applicants") or []
        if len(applicants) >= 6:
            issues.append("bibliographic_data.applicants.abnormally_many")
        for idx, applicant in enumerate(applicants):
            name = str((applicant or {}).get("name") or "").strip()
            if not name:
                continue
            if suspicious_marker_pattern.search(name):
                issues.append(f"bibliographic_data.applicants[{idx}].name.polluted")
            if re.fullmatch(r"[\d\W]+", name):
                issues.append(f"bibliographic_data.applicants[{idx}].name.invalid_chars")
            if self._looks_like_address_text(name):
                issues.append(f"bibliographic_data.applicants[{idx}].name.is_actually_address")

        inventors = bibliographic.get("inventors") or []
        for idx, inventor in enumerate(inventors[:5]):
            inventor_text = str(inventor or "").strip()
            if len(inventor_text) > 100 or suspicious_marker_pattern.search(inventor_text):
                issues.append(f"bibliographic_data.inventors[{idx}].polluted")

        agency = bibliographic.get("agency")
        if isinstance(agency, dict):
            for idx, agent in enumerate(agency.get("agents") or []):
                agent_text = str(agent or "").strip()
                if re.fullmatch(r"[\d\W]+", agent_text):
                    issues.append(f"bibliographic_data.agency.agents[{idx}].invalid_chars")

        abstract = str(bibliographic.get("abstract") or "").strip()
        if "(57)" in md_content and not abstract:
            issues.append("bibliographic_data.abstract.missing_despite_marker")

        has_section_markers = bool(
            re.search(
                r"FIELD OF THE INVENTION|BACKGROUND|SUMMARY OF THE INVENTION|【技術分野】|【背景技術】|【発明の概要】|技术领域|背景技术|发明内容",
                md_content,
                re.IGNORECASE,
            )
        )
        if has_section_markers:
            if not str(description.get("technical_field") or "").strip() and not str(description.get("background_art") or "").strip():
                issues.append("description.sections.missing_despite_explicit_markers_in_text")

        check_claims = claims[:5] + claims[-2:] if len(claims) > 5 else claims
        for idx, claim in enumerate(check_claims):
            claim_text = str((claim or {}).get("claim_text") or "").strip()
            if len(claim_text) < 10:
                issues.append(f"claims[{idx}].abnormally_short")
            if "![](" in claim_text:
                issues.append(f"claims[{idx}].contains_unprocessed_image")
            upper_claim = claim_text.upper()
            if "EUROPEAN SEARCH REPORT" in upper_claim or "ABSTRACT" in upper_claim:
                issues.append(f"claims[{idx}].contains_other_sections")

        return issues

    @staticmethod
    def _looks_like_address_text(text: str) -> bool:
        value = str(text or "").strip()
        if not value:
            return False

        if re.match(r"^(?:发区|片区|园区|路|街|道|号|室|栋|楼)", value):
            return True
        if re.search(r"(?:省|自治区|直辖市).*?(?:市|区|县).*?(?:路|街|道|镇|乡|村|号|室)", value):
            return True
        if re.search(r"\d+.*?(?:路|街|道|号|室|栋|楼|片区|园区|开发区|自贸区)", value):
            return True
        if re.search(r"(?:都|道|府|県).*?(?:市|区|郡).*?(?:町|村|丁目|番地?|号)", value) or re.search(r"^\d{3}-\d{4}", value):
            return True
        if re.search(r"(?:도|특별시|광역시).*?(?:시|군|구).*?(?:동|로|길)\s*\d+", value):
            return True
        if re.search(r"\b\d+\s+[A-Za-z\s]+(?:Street|St|Avenue|Ave|Boulevard|Blvd|Road|Rd|Drive|Dr)\b", value, re.IGNORECASE):
            return True
        if re.search(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b", value):
            return True
        return False
