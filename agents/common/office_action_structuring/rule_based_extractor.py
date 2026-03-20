"""
审查意见通知书结构化提取器
基于规则的审查意见通知书结构化提取器，用于从markdown格式的审查意见通知书中提取结构化数据
"""

import re
from typing import List
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
        office_action = OfficeAction(
            application_number=self._extract_application_number(markdown_content),
            comparison_documents=self._extract_comparison_documents(markdown_content),
            paragraphs=self._extract_paragraphs(markdown_content)
        )
        return office_action

    def _extract_application_number(self, markdown_content: str) -> str:
        """提取原专利申请号"""
        pattern = r"申请号：?\s*([A-Z0-9.]+)"
        match = re.search(pattern, markdown_content)
        if match:
            application_number = match.group(1).strip()
            logger.info(f"提取到申请号: {application_number}")
            return application_number
        logger.warning("未找到申请号")
        return ""

    def _extract_comparison_documents(self, markdown_content: str) -> List[ComparisonDocument]:
        """提取对比文件列表"""
        comparison_documents = []

        # 查找对比文件表格
        table_pattern = r"对比文件\(其编号在今后的审查过程中继续沿用\)：?\s*<table>(.*?)</table>"
        table_match = re.search(table_pattern, markdown_content, re.DOTALL)

        if table_match:
            table_content = table_match.group(1)

            # 提取表格行
            row_pattern = r"<tr>(.*?)</tr>"
            rows = re.findall(row_pattern, table_content, re.DOTALL)

            if len(rows) > 1:  # 第一行是表头，从第二行开始提取数据
                for row in rows[1:]:
                    # 提取单元格内容
                    cell_pattern = r"<td>(.*?)</td>"
                    cells = re.findall(cell_pattern, row, re.DOTALL)

                    if len(cells) >= 3:
                        document_index = len(comparison_documents) + 1
                        document_number = cells[1].strip()
                        is_patent = is_patent_document(document_number)
                        doc = ComparisonDocument(
                            document_id=f"D{document_index}",
                            document_number=document_number,
                            is_patent=is_patent,
                            publication_date=cells[2].strip() if cells[2].strip() else None,
                        )
                        comparison_documents.append(doc)

        logger.info(f"共提取到 {len(comparison_documents)} 个对比文件")
        return comparison_documents

    def _extract_paragraphs(self, markdown_content: str) -> List[OfficeActionParagraph]:
        """提取审查意见通知书章节的段落内容"""
        paragraphs = []

        # 查找最后一个# 第x次审查意见通知书的位置
        chapter_pattern = r"# 第\s*([一二三四五六七八九十]+)\s*次\s*审\s*查\s*意\s*见\s*通\s*知\s*书"
        chapter_matches = list(re.finditer(chapter_pattern, markdown_content))

        if chapter_matches:
            # 提取最后一个审查意见通知书的内容
            last_chapter = chapter_matches[-1]
            chapter_index = last_chapter.start()
            chapter_end = len(markdown_content)
            # 查找下一个#标题或文件结束
            next_chapter_match = re.search(r"\n#", markdown_content[chapter_index+1:])
            if next_chapter_match:
                chapter_end = chapter_index + 1 + next_chapter_match.start()

            section_content = markdown_content[chapter_index + len(last_chapter.group()):chapter_end].strip()
            logger.info(f"找到第{last_chapter.group(1)}次审查意见通知书章节，长度: {len(section_content)}")

            # 提取段落内容（以数字+.或数字+、开头的段落）
            paragraph_pattern = r"(\d+)\s*[、.]\s*(.*?)(?=\n\s*\d+\s*[、.]\s*|\n#|\Z)"
            matches = re.finditer(paragraph_pattern, section_content, re.DOTALL)

            for match in matches:
                content = match.group(2).strip()

                # 最后一段只提取到“基于上述理由”之前的内容
                if "基于上述理由" in content:
                    content = content.split("基于上述理由")[0].strip()

                if content:
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
        else:
            logger.warning("未找到审查意见通知书章节")

        return paragraphs

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
