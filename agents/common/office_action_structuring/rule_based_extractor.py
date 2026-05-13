"""
审查意见通知书结构化提取器
基于规则的审查意见通知书结构化提取器，用于从markdown格式的审查意见通知书中提取结构化数据
"""

import re
from typing import Callable, List, Optional, Tuple
from agents.common.office_action_structuring.models import (
    OfficeAction,
    ComparisonDocument,
    OfficeActionParagraph,
)
from agents.ai_reply.src.utils import is_patent_document, normalize_patent_identifier
from loguru import logger


class OfficeActionExtractor:
    """审查意见通知书结构化提取器"""

    def __init__(self, patent_resolver: Optional[Callable[[str], bool]] = None):
        self.patent_resolver = patent_resolver
        self._patent_resolution_cache: dict[str, bool] = {}

    def extract(self, markdown_content: str) -> OfficeAction:
        """
        从markdown内容中提取审查意见通知书的结构化数据

        Args:
            markdown_content: 审查意见通知书的markdown内容

        Returns:
            结构化的审查意见通知书数据
        """
        markdown_content = self._normalize_document_content(markdown_content)
        current_notice_round, section_content = self._extract_latest_notice_section(markdown_content)
        office_action = OfficeAction(
            application_number=self._extract_application_number(markdown_content),
            current_notice_round=current_notice_round,
            comparison_documents=self._extract_comparison_documents(section_content, markdown_content),
            paragraphs=self._extract_paragraphs(section_content),
        )
        return office_action

    @staticmethod
    def _normalize_document_content(text: str) -> str:
        """对 OCR/Mineru 输出做最小限度的归一化，消除常见的不可见/全角字符差异。

        刻意只折叠不可见与数字字符，保留中文标点的原貌，避免改写正文。

        - 删除零宽字符（U+200B/200C/200D/2060/FEFF）
        - 全角空格 `　` 与 NBSP `\xa0` 折叠为普通空格
        - 全角数字 `０-９` 折叠为半角 `0-9`
        """
        if not text:
            return text
        text = re.sub(r"[​‌‍⁠﻿]", "", text)
        text = text.replace("　", " ").replace("\xa0", " ")
        text = text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        return text

    def _extract_latest_notice_section(self, markdown_content: str) -> Tuple[int, str]:
        """提取最新一份审查意见通知书的轮次与正文区段。"""
        chapter_pattern = re.compile(
            r"(?m)^[|>\s]*(?:#+\s*)?[\s*_]*第[\s*_]*([0-9一二三四五六七八九十百零〇两]+)[\s*_]*次[\s*_]*审[\s*_]*查[\s*_]*意[\s*_]*见[\s*_]*通[\s*_]*知[\s*_]*书"
        )
        chapter_matches = list(re.finditer(chapter_pattern, markdown_content))
        if not chapter_matches:
            chapter_matches = list(
                re.finditer(
                    r"第[\s*_]*([0-9一二三四五六七八九十百零〇两]+)[\s*_]*次[\s*_]*审[\s*_]*查[\s*_]*意[\s*_]*见[\s*_]*通[\s*_]*知[\s*_]*书",
                    markdown_content,
                )
            )
            if chapter_matches:
                logger.warning(
                    "未在行首匹配到带轮次的审查意见通知书标题，回退到全文搜索，"
                    f"命中数={len(chapter_matches)}"
                )
        if chapter_matches:
            last_chapter = chapter_matches[-1]
            round_raw = str(last_chapter.group(1) or "").strip()
            current_notice_round = self._parse_legal_number(round_raw)
            if current_notice_round is None or current_notice_round <= 0:
                raise ValueError(f"审查意见通知书轮次非法: {round_raw}")
            section_content = markdown_content[last_chapter.end():].strip()
            logger.info(f"识别到第{current_notice_round}次审查意见通知书章节，长度: {len(section_content)}")
            return current_notice_round, section_content

        fallback_pattern = re.compile(
            r"[\s|>]*(?:#+\s*)?[\s*_]*审[\s*_]*查[\s*_]*意[\s*_]*见[\s*_]*通[\s*_]*知[\s*_]*书"
        )
        fallback_matches = list(re.finditer(fallback_pattern, markdown_content))
        if fallback_matches:
            last_match = fallback_matches[-1]
            section_content = markdown_content[last_match.end():].strip()
            logger.warning(
                "未匹配到带轮次的审查意见通知书标题，回退使用通用标题并按第1次处理，"
                f"匹配数={len(fallback_matches)} 章节长度={len(section_content)}"
            )
            return 1, section_content

        raise ValueError("未识别审查意见通知书轮次(current_notice_round)")

    def _extract_application_number(self, markdown_content: str) -> str:
        """提取原专利申请号"""
        patterns = [
            r"申请号(?:或专利号)?\s*[:：]\s*([A-Z0-9.]+)",
            r"申请号(?:或专利号)?\s+([A-Z0-9.]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, markdown_content or "", re.IGNORECASE)
            if not match:
                continue
            application_number = match.group(1).strip().strip("，,；;。")
            if application_number:
                logger.info(f"提取到申请号: {application_number}")
                return application_number
        logger.warning("未找到申请号")
        return ""

    def _extract_comparison_documents(self, section_content: str, markdown_content: str = "") -> List[ComparisonDocument]:
        """提取对比文件列表"""
        comparison_documents = self._extract_comparison_documents_from_body(section_content)
        if comparison_documents:
            table_documents = self._extract_comparison_documents_from_table(markdown_content or section_content)
            if table_documents:
                comparison_documents = self._fill_publication_dates_from_table(
                    comparison_documents,
                    table_documents,
                )
            logger.info(f"正文共提取到 {len(comparison_documents)} 个对比文件")
            return comparison_documents

        comparison_documents = self._extract_comparison_documents_from_table(markdown_content or section_content)
        logger.info(f"表格兜底共提取到 {len(comparison_documents)} 个对比文件")
        return comparison_documents

    def _fill_publication_dates_from_table(
        self,
        body_documents: List[ComparisonDocument],
        table_documents: List[ComparisonDocument],
    ) -> List[ComparisonDocument]:
        """在保持正文编号与文献号口径不变的前提下，从表格补齐公开日期。"""
        dates_by_number = {
            self._clean_embedded_text(item.document_number): item.publication_date
            for item in table_documents
            if self._clean_embedded_text(item.document_number) and item.publication_date
        }
        dates_by_doc_id = {
            str(item.document_id or "").strip(): item.publication_date
            for item in table_documents
            if str(item.document_id or "").strip() and item.publication_date
        }

        enriched_documents: List[ComparisonDocument] = []
        for item in body_documents:
            publication_date = item.publication_date
            if not publication_date:
                normalized_number = self._clean_embedded_text(item.document_number)
                publication_date = dates_by_number.get(normalized_number) or dates_by_doc_id.get(item.document_id)

            enriched_documents.append(
                item.model_copy(update={"publication_date": publication_date})
            )
        return enriched_documents

    def _extract_comparison_documents_from_body(self, section_content: str) -> List[ComparisonDocument]:
        comparison_documents: List[ComparisonDocument] = []
        seen_doc_ids = set()
        pattern = re.compile(r"对比文件\s*([0-9０-９]+)\s*([（(])")

        for match in pattern.finditer(section_content or ""):
            raw_index = self._normalize_digits(match.group(1))
            if not raw_index.isdigit():
                continue
            document_id = f"D{int(raw_index)}"
            if document_id in seen_doc_ids:
                continue

            end_index = self._find_matching_bracket(section_content, match.end())
            if end_index is None:
                continue

            document_number = self._clean_embedded_text(section_content[match.end():end_index])
            if not document_number:
                continue

            comparison_documents.append(
                ComparisonDocument(
                    document_id=document_id,
                    document_number=document_number,
                    is_patent=self._resolve_is_patent(document_number),
                    publication_date=None,
                )
            )
            seen_doc_ids.add(document_id)
        return comparison_documents

    def _extract_comparison_documents_from_table(self, section_content: str) -> List[ComparisonDocument]:
        comparison_documents: List[ComparisonDocument] = []
        table_pattern = r"对比文件(?:\(其编号在今后的审查过程中继续沿用\)|（其编号在今后的审查过程中继续沿用）)[:：]?\s*<table\b[^>]*>(.*?)</table>"
        table_match = re.search(table_pattern, section_content, re.DOTALL)
        if not table_match:
            return comparison_documents

        table_content = table_match.group(1)
        row_pattern = r"<tr\b[^>]*>(.*?)</tr>"
        rows = re.findall(row_pattern, table_content, re.DOTALL)
        if len(rows) <= 1:
            return comparison_documents

        for row in rows[1:]:
            cell_pattern = r"<td\b[^>]*>(.*?)</td>"
            cells = re.findall(cell_pattern, row, re.DOTALL)
            if len(cells) < 3:
                continue

            document_index = len(comparison_documents) + 1
            document_number = self._clean_embedded_text(cells[1])
            if not document_number:
                continue

            comparison_documents.append(
                ComparisonDocument(
                    document_id=f"D{document_index}",
                    document_number=document_number,
                    is_patent=self._resolve_is_patent(document_number),
                    publication_date=self._clean_embedded_text(cells[2]) or None,
                )
            )
        return comparison_documents

    def _extract_paragraphs(self, section_content: str) -> List[OfficeActionParagraph]:
        """提取审查意见通知书章节的段落内容"""
        paragraphs = []
        paragraph_pattern = r"(\d+)\s*[、.]\s*(.*?)(?=\n\s*\d+\s*[、.]\s*|\n#|\Z)"
        matches = re.finditer(paragraph_pattern, section_content, re.DOTALL)

        for match in matches:
            content = match.group(2).strip()

            if "基于上述理由" in content:
                content = content.split("基于上述理由")[0].strip()

            if not content:
                continue

            paragraph_index = len(paragraphs) + 1
            paragraphs.append(OfficeActionParagraph(
                paragraph_id=f"Claim{paragraph_index}",
                claim_ids=self._extract_claim_ids(content),
                cited_doc_ids=self._extract_cited_doc_ids(content),
                content=content
            ))

        logger.info(f"共提取到 {len(paragraphs)} 个段落")
        return paragraphs

    def _find_matching_bracket(self, text: str, start_index: int) -> Optional[int]:
        depth = 1
        for index in range(start_index, len(text)):
            char = text[index]
            if char in {"(", "（"}:
                depth += 1
            elif char in {")", "）"}:
                depth -= 1
                if depth == 0:
                    return index
        return None

    def _clean_embedded_text(self, text: str) -> str:
        cleaned = re.sub(r"<[^>]+>", " ", str(text or ""))
        cleaned = self._normalize_digits(cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip(" \t\r\n，,；;。")

    def _normalize_digits(self, text: str) -> str:
        value = str(text or "")
        translation = str.maketrans("０１２３４５６７８９", "0123456789")
        return value.translate(translation)

    def _resolve_is_patent(self, document_number: str) -> bool:
        normalized_number = self._clean_embedded_text(document_number)
        cache_key = normalize_patent_identifier(normalized_number)
        if cache_key in self._patent_resolution_cache:
            return self._patent_resolution_cache[cache_key]

        is_patent = is_patent_document(normalized_number)
        if not is_patent and self.patent_resolver is not None and normalized_number:
            is_patent = bool(self.patent_resolver(normalized_number))

        self._patent_resolution_cache[cache_key] = is_patent
        return is_patent

    def _extract_claim_ids(self, content: str) -> List[str]:
        """仅提取段落中第一次出现的权利要求编号，兼容单点与区间表达（如 1-3）。"""
        normalized_content = self._normalize_digits(content)
        claim_keyword_pattern = r"权\s*利\s*要\s*求"
        first_pattern = re.compile(
            rf"{claim_keyword_pattern}\s*(\d+)(?:\s*(?:-|－|—|~|～|至|到)\s*(\d+))?"
        )
        match = first_pattern.search(normalized_content)
        if not match:
            return []

        start_raw = match.group(1)
        end_raw = match.group(2)
        start = int(start_raw)
        if start <= 0:
            return []
        if not end_raw:
            return [str(start)]

        end = int(end_raw)
        if end <= 0:
            return [str(start)]
        low, high = (start, end) if start <= end else (end, start)
        return [str(value) for value in range(low, high + 1)]

    def _extract_cited_doc_ids(self, content: str) -> List[str]:
        """提取段落中明确提及的 D 文献编号。"""
        doc_ids = set()
        patterns = [
            r"对比文件\s*(\d+)",
            r"[Dd]\s*(\d+)",
        ]
        for pattern in patterns:
            for value in re.findall(pattern, content or ""):
                doc_ids.add(f"D{int(value)}")
        return sorted(doc_ids, key=lambda item: int(item[1:]))

    def _parse_legal_number(self, value: str) -> int | None:
        text = str(value or "").strip()
        if not text:
            return None
        if text.isdigit():
            return int(text)

        digits = {
            "零": 0,
            "〇": 0,
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
        }
        if text == "十":
            return 10
        if "十" in text:
            left, right = text.split("十", 1)
            tens = digits.get(left, 1 if not left else None)
            units = digits.get(right, 0 if not right else None)
            if tens is None or units is None:
                return None
            return tens * 10 + units
        if all(char in digits for char in text):
            result = 0
            for char in text:
                result = result * 10 + digits[char]
            return result
        return None
