"""
审查意见通知书结构化提取器
基于规则的审查意见通知书结构化提取器，用于从markdown格式的审查意见通知书中提取结构化数据
"""

import re
from typing import List, Optional, Tuple
from agents.common.office_action_structuring.models import (
    OfficeAction,
    ComparisonDocument,
    OfficeActionParagraph,
    ParagraphEvaluation,
)
from agents.ai_reply.src.utils import is_patent_document
from loguru import logger


class OfficeActionExtractor:
    """审查意见通知书结构化提取器"""

    _ISSUE_TYPE_MAPPING = {
        "A22.2": "新颖性",
        "A22.3": "创造性",
        "A22.4": "实用性",
        "A26.3": "公开不充分",
        "A26.4": "清楚/支持",
        "A31.1": "单一性",
        "A33": "修改超范围",
        "R20": "说明书形式问题",
        "R20.1": "清楚",
        "R22": "权利要求书形式问题",
        "R23": "权利要求书形式问题",
        "R24": "权利要求书形式问题",
        "R25": "权利要求书形式问题",
        "R57.1": "修改不予接受",
        "R57.3": "修改不予接受",
    }

    def extract(self, markdown_content: str) -> OfficeAction:
        """
        从markdown内容中提取审查意见通知书的结构化数据

        Args:
            markdown_content: 审查意见通知书的markdown内容

        Returns:
            结构化的审查意见通知书数据
        """
        current_notice_round, section_content = self._extract_latest_notice_section(markdown_content)
        office_action = OfficeAction(
            application_number=self._extract_application_number(markdown_content),
            current_notice_round=current_notice_round,
            comparison_documents=self._extract_comparison_documents(section_content),
            paragraphs=self._extract_paragraphs(section_content),
        )
        return office_action

    def _extract_latest_notice_section(self, markdown_content: str) -> Tuple[int, str]:
        """提取最新一份审查意见通知书的轮次与正文区段。"""
        chapter_pattern = re.compile(
            r"(?m)^(?:\s*#+\s*)?(?:\*\*|__)?\s*第\s*([0-9一二三四五六七八九十百零〇两]+)\s*次\s*审\s*查\s*意\s*见\s*通\s*知\s*书\s*(?:\*\*|__)?\s*$"
        )
        chapter_matches = list(re.finditer(chapter_pattern, markdown_content))
        if not chapter_matches:
            raise ValueError("未识别审查意见通知书轮次(current_notice_round)")

        last_chapter = chapter_matches[-1]
        round_raw = str(last_chapter.group(1) or "").strip()
        current_notice_round = self._parse_legal_number(round_raw)
        if current_notice_round is None or current_notice_round <= 0:
            raise ValueError(f"审查意见通知书轮次非法: {round_raw}")

        section_content = markdown_content[last_chapter.end():].strip()
        logger.info(f"识别到第{current_notice_round}次审查意见通知书章节，长度: {len(section_content)}")
        return current_notice_round, section_content

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

    def _extract_comparison_documents(self, section_content: str) -> List[ComparisonDocument]:
        """提取对比文件列表"""
        comparison_documents = self._extract_comparison_documents_from_body(section_content)
        if comparison_documents:
            logger.info(f"正文共提取到 {len(comparison_documents)} 个对比文件")
            return comparison_documents

        comparison_documents = self._extract_comparison_documents_from_table(section_content)
        logger.info(f"表格兜底共提取到 {len(comparison_documents)} 个对比文件")
        return comparison_documents

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
                    is_patent=is_patent_document(document_number),
                    publication_date=None,
                )
            )
            seen_doc_ids.add(document_id)
        return comparison_documents

    def _extract_comparison_documents_from_table(self, section_content: str) -> List[ComparisonDocument]:
        comparison_documents: List[ComparisonDocument] = []
        table_pattern = r"对比文件(?:\(其编号在今后的审查过程中继续沿用\)|（其编号在今后的审查过程中继续沿用）)：?\s*<table>(.*?)</table>"
        table_match = re.search(table_pattern, section_content, re.DOTALL)
        if not table_match:
            return comparison_documents

        table_content = table_match.group(1)
        row_pattern = r"<tr>(.*?)</tr>"
        rows = re.findall(row_pattern, table_content, re.DOTALL)
        if len(rows) <= 1:
            return comparison_documents

        for row in rows[1:]:
            cell_pattern = r"<td>(.*?)</td>"
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
                    is_patent=is_patent_document(document_number),
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
            legal_basis = self._extract_legal_basis(content)
            paragraphs.append(OfficeActionParagraph(
                paragraph_id=f"Claim{paragraph_index}",
                claim_ids=self._extract_claim_ids(content),
                legal_basis=legal_basis,
                issue_types=self._map_issue_types(legal_basis, content),
                cited_doc_ids=self._extract_cited_doc_ids(content),
                evaluation=self._determine_evaluation(content),
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

    def _extract_claim_ids(self, content: str) -> List[str]:
        """提取段落中关联的权利要求编号，兼容单点与区间表达（如 1-3）。"""
        claim_ids: List[str] = []
        claim_keyword_pattern = r"权\s*利\s*要\s*求"

        # 1. 提取区间表达：权利要求1-3 / 权利要求1至3 / 权利要求1到3 / 权利要求1~3
        range_pattern = rf"{claim_keyword_pattern}\s*(\d+)\s*(?:-|－|—|~|～|至|到)\s*(\d+)"
        for start_raw, end_raw in re.findall(range_pattern, content):
            try:
                start = int(start_raw)
                end = int(end_raw)
            except ValueError:
                continue
            if start <= 0 or end <= 0:
                continue
            low, high = (start, end) if start <= end else (end, start)
            for value in range(low, high + 1):
                claim_id = str(value)
                if claim_id not in claim_ids:
                    claim_ids.append(claim_id)

        # 2. 提取单个表达：权利要求1
        single_pattern = rf"{claim_keyword_pattern}\s*(\d+)"
        for claim_raw in re.findall(single_pattern, content):
            claim_id = str(int(claim_raw))
            if claim_id not in claim_ids:
                claim_ids.append(claim_id)

        return claim_ids

    def _extract_legal_basis(self, content: str) -> List[str]:
        """提取法律依据，输出标准化编码，如 A22.3 / R20.1。"""
        results = set()
        pattern = r"(专利法|实施细则)第\s*([0-9一二三四五六七八九十百零〇两]+)\s*条(?:\s*第\s*([0-9一二三四五六七八九十百零〇两]+)\s*款)?"
        for law_type, article_raw, clause_raw in re.findall(pattern, content or ""):
            article = self._parse_legal_number(article_raw)
            clause = self._parse_legal_number(clause_raw) if clause_raw else None
            if article is None:
                continue
            short_name = "A" if law_type == "专利法" else "R"
            basis = f"{short_name}{article}" + (f".{clause}" if clause is not None else "")
            results.add(basis)
        return sorted(results)

    def _map_issue_types(self, legal_basis: List[str], content: str) -> List[str]:
        """基于法条映射并用关键词补偿缺陷类型。"""
        issues = {
            self._ISSUE_TYPE_MAPPING[basis]
            for basis in (legal_basis or [])
            if basis in self._ISSUE_TYPE_MAPPING
        }
        if issues:
            return sorted(issues)

        keyword_map = {
            "新颖性": r"不(?:具备|具有)新颖性",
            "创造性": r"不(?:具备|具有)创造性",
            "清楚": r"不清楚|不简要",
            "支持": r"得不到说明书的支持|不符合专利法第26条第4款",
            "修改超范围": r"不符合专利法第33条|超出原说明书和权利要求书记载的范围",
            "公开不充分": r"不符合专利法第26条第3款",
        }
        for label, pattern in keyword_map.items():
            if re.search(pattern, content or ""):
                issues.add(label)
        return sorted(issues)

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

    def _determine_evaluation(self, content: str) -> ParagraphEvaluation:
        text = str(content or "").strip()
        if not text:
            return ParagraphEvaluation.UNKNOWN

        positive_patterns = [
            r"未发现.{0,8}(?:不符合|缺陷|驳回理由)",
            r"具备(?:新颖性|创造性|工业实用性)",
            r"符合(?:专利法|实施细则)",
        ]
        if any(re.search(pattern, text) for pattern in positive_patterns):
            if all(keyword not in text for keyword in ["不具备", "不具有", "不符合", "不能被授予"]):
                return ParagraphEvaluation.POSITIVE

        negative_keywords = [
            "不具备",
            "不具有",
            "不符合",
            "不清楚",
            "得不到",
            "修改超出",
            "不能被授予",
            "将被驳回",
            "不予接受",
        ]
        if any(keyword in text for keyword in negative_keywords):
            return ParagraphEvaluation.NEGATIVE

        return ParagraphEvaluation.NEUTRAL

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
