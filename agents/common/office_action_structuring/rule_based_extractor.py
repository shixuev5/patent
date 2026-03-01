"""
审查意见通知书结构化提取器
基于规则的审查意见通知书结构化提取器，用于从markdown格式的审查意见通知书中提取结构化数据
"""

import re
from typing import List
from agents.common.office_action_structuring.models import (
    OfficeAction,
    ComparisonDocument,
    OfficeActionParagraph
)
from loguru import logger


class OfficeActionExtractor:
    """审查意见通知书结构化提取器"""

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
                        doc = ComparisonDocument(
                            document_number=cells[1].strip(),
                            publication_date=cells[2].strip() if cells[2].strip() else None
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
                    paragraphs.append(OfficeActionParagraph(
                        content=content
                    ))

            logger.info(f"共提取到 {len(paragraphs)} 个段落")
        else:
            logger.warning("未找到审查意见通知书章节")

        return paragraphs
