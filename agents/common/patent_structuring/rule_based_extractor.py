import html
import re
from typing import List, Dict, Optional
from loguru import logger

from agents.common.patent_structuring.date_utils import parse_common_date_string

class RuleBasedExtractor:
    """基于规则的专利文档结构化提取器"""

    @staticmethod
    def extract(md_content: str) -> dict:
        """
        使用规则解析专利文档

        Args:
            md_content: 专利文档的 Markdown 内容

        Returns:
            结构化的专利数据字典
        """
        logger.info("规则抽取器开始解析专利文本")

        try:
            # 统一换行符/全角标点，并清理常见 OCR 断字噪声
            md_content = RuleBasedExtractor._normalize_document_text(md_content)

            result = {
                "bibliographic_data": RuleBasedExtractor._parse_bibliographic_data(md_content),
                "claims": RuleBasedExtractor._parse_claims(md_content),
                "description": RuleBasedExtractor._parse_description(md_content),
                "drawings": RuleBasedExtractor._parse_drawings(md_content),
            }
            from agents.common.patent_structuring.models import PatentDocument
            result = PatentDocument.model_validate(result).model_dump()

            logger.success("规则抽取器解析完成")
            return result

        except Exception as e:
            logger.exception(f"规则抽取器解析失败: {e}")
            return {}

    @staticmethod
    def _normalize_document_text(md_content: str) -> str:
        text = str(md_content or "").replace("\r\n", "\n")
        text = text.replace("（", "(").replace("）", ")")
        text = text.replace("：", ":")
        text = re.sub(r"([A-Za-z])-\s*\d+\s+([A-Za-z])", r"\1\2", text)
        text = re.sub(r"(?m)^\s*\d+\s*$", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _to_search_text(md_content: str) -> str:
        text = RuleBasedExtractor._normalize_document_text(md_content)
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(r"(?i)</?(?:table|tr)>", "\n", text)
        text = re.sub(r"(?i)</?(?:td|th)>", " ", text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = html.unescape(text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _clean_field_value(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n:;,.")

    @staticmethod
    def _find_first_group(text: str, patterns: List[str], flags: int = 0) -> str:
        for pattern in patterns:
            match = re.search(pattern, text, flags)
            if match:
                value = RuleBasedExtractor._clean_field_value(match.group(1))
                if value:
                    return value
        return ""

    @staticmethod
    def _extract_date_value(text: str) -> str:
        value = str(text or "").strip()
        if not value:
            return ""

        normalized = parse_common_date_string(value)
        if normalized:
            return normalized

        loose = re.search(r"(\d{4})\D{0,8}(\d{1,2})\D{0,8}(\d{1,2})", value)
        if loose:
            return RuleBasedExtractor._normalize_date(
                f"{loose.group(1)}.{loose.group(2)}.{loose.group(3)}"
            ) or ""

        return ""

    @staticmethod
    def _extract_field_block(md_content: str, field_no: str) -> str:
        text = RuleBasedExtractor._to_search_text(md_content)
        pattern = rf"\({field_no}\)\s*([\s\S]*?)(?=\(\d+\)|#\s*\(\d+\)|\Z)"
        match = re.search(pattern, text)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _extract_section_by_headings(md_content: str, headings: List[str], next_headings: List[str]) -> str:
        text = RuleBasedExtractor._normalize_document_text(md_content)
        heading_pattern = "|".join(headings)
        next_pattern = "|".join(next_headings)
        pattern = rf"(?ims)(?:^|\n)(?:#\s*)?(?:{heading_pattern})\s*([\s\S]*?)(?=(?:^|\n)(?:#\s*)?(?:{next_pattern})\s*$|\Z)"
        match = re.search(pattern, text)
        if not match:
            return ""
        content = match.group(1).strip()
        return re.sub(r"\[\d{4}\]\s*", "", content).strip()

    @staticmethod
    def _parse_bibliographic_data(md_content: str) -> dict:
        """解析 bibliographic data (著录项目) 部分"""
        data = {
            "application_number": RuleBasedExtractor._extract_application_number(md_content),
            "application_date": RuleBasedExtractor._extract_application_date(md_content),
            "priority_date": RuleBasedExtractor._extract_priority_date(md_content),
            "publication_number": RuleBasedExtractor._extract_publication_number(md_content),
            "publication_date": RuleBasedExtractor._extract_publication_date(md_content),
            "invention_title": RuleBasedExtractor._extract_invention_title(md_content),
            "ipc_classifications": RuleBasedExtractor._extract_ipc_classifications(md_content),
            "applicants": RuleBasedExtractor._extract_applicants(md_content),
            "inventors": RuleBasedExtractor._extract_inventors(md_content),
            "agency": RuleBasedExtractor._extract_agency(md_content),
            "abstract": RuleBasedExtractor._extract_abstract(md_content),
            "abstract_figure": RuleBasedExtractor._extract_abstract_figure(md_content),
        }
        return data

    @staticmethod
    def _parse_claims(md_content: str) -> list:
        """解析 claims (权利要求) 部分"""
        claims =[]
        claims_section = RuleBasedExtractor._extract_claims_section(md_content)
        if not claims_section:
            return claims

        # 兼容中文/英文/日文/韩文 claim 标记
        for item in RuleBasedExtractor.extract_structured_claims(claims_section):
            claims.append({
                "claim_id": item.get("claim_id", ""),
                "claim_text": item["claim_text"],
                "claim_type": item["claim_type"],
                "parent_claim_ids": item.get("parent_claim_ids", []),
            })
            
        return claims

    @staticmethod
    def _extract_claims_section(md_content: str) -> str:
        text = RuleBasedExtractor._normalize_document_text(md_content)
        search_cutoff = re.search(r"(?im)^#\s*(?:EUROPEAN SEARCH REPORT|ANNEX TO THE EUROPEAN SEARCH REPORT)\b", text)
        if search_cutoff:
            text = text[: search_cutoff.start()]

        amended_match = re.search(
            r"(?ims)^#\s*Amended claims[^\n]*\n([\s\S]*?)\Z",
            text,
        )
        if amended_match:
            return amended_match.group(1).strip()

        jp_match = re.search(
            r"【特許請求の範囲】([\s\S]*?)(?=【発明の詳細な説明】|\Z)",
            text,
        )
        if jp_match:
            return jp_match.group(1).strip()

        kr_match = re.search(r"(?ims)^#\s*청구항\s*1\s*$([\s\S]*?)\Z", text)
        if kr_match:
            return kr_match.group(0).strip()

        claims_heading = re.search(r"(?ims)^#\s*Claims\s*$([\s\S]*?)\Z", text)
        if claims_heading:
            return claims_heading.group(1).strip()

        run_matches = list(re.finditer(r"(?m)^1\s*[\.．]\s*", text))
        if not run_matches:
            return ""
        stop_pattern = (
            r"(?im)^#\s*(?:技术领域|背景技术|发明内容|附图说明|具体实施方式|说明书|"
            r"FIELD OF THE INVENTION|BACKGROUND|SUMMARY(?: OF THE INVENTION)?|"
            r"BRIEF DESCRIPTION OF THE DRAWINGS|DETAILED DESCRIPTION(?: OF THE PREFERRED EMBODIMENTS OF THE INVENTION)?|"
            r"【技術分野】|【背景技術】|【発明の概要】|【図面の簡単な説明】|【発明の詳細な説明】)\b"
        )
        best_section = ""
        best_score = (-1, -1, 10**9)

        for match in run_matches:
            candidate = text[match.start() :]
            stop_match = re.search(stop_pattern, candidate)
            if stop_match:
                candidate = candidate[: stop_match.start()]
            candidate = candidate.strip()
            if not candidate:
                continue

            claim_ids = [
                int(item.group(1))
                for item in re.finditer(r"(?m)^\s*(\d+)\s*[\.．]\s*", candidate)
            ]
            if not claim_ids or claim_ids[0] != 1:
                continue

            consecutive = 0
            expected = 1
            for claim_id in claim_ids:
                if claim_id == expected:
                    consecutive += 1
                    expected += 1
                else:
                    break

            dependent_hits = len(re.findall(r"(?:根据|如)\s*权利要求", candidate))
            score = (consecutive, dependent_hits, match.start())
            if score[0] > best_score[0] or (score[0] == best_score[0] and score[1] > best_score[1]) or (
                score[0] == best_score[0] and score[1] == best_score[1] and score[2] < best_score[2]
            ):
                best_section = candidate
                best_score = score

        return best_section

    @staticmethod
    def _parse_description(md_content: str) -> dict:
        """解析 description (说明书正文) 部分"""
        summary, effect = RuleBasedExtractor._extract_summary_and_effect(md_content)
        return {
            "technical_field": RuleBasedExtractor._extract_technical_field(md_content),
            "background_art": RuleBasedExtractor._extract_background_art(md_content),
            "summary_of_invention": summary,
            "technical_effect": effect,
            "brief_description_of_drawings": RuleBasedExtractor._extract_brief_description(md_content),
            "detailed_description": RuleBasedExtractor._extract_detailed_description(md_content),
        }

    @staticmethod
    def _parse_drawings(md_content: str) -> list:
        """解析 drawings (附图资源) 部分"""
        drawings =[]
        figure_captions = RuleBasedExtractor._extract_figure_captions(md_content)
        abstract_figure = RuleBasedExtractor._extract_abstract_figure(md_content)

        # 仅提取“# 具体实施方式”到文末的附图区域，避免误纳入摘要附图。
        drawings_zone = RuleBasedExtractor._extract_drawings_zone(md_content)
        if not drawings_zone:
            return drawings

        # 使用逐行状态机，保证“一个图片只能绑定一个图号”。
        lines = drawings_zone.replace("\r\n", "\n").split("\n")
        pending_images: List[str] = []

        def _flush(labels: List[str]) -> None:
            nonlocal drawings, pending_images
            if not labels or not pending_images:
                return

            # 单个图号：允许同图号对应多张图片
            if len(labels) == 1:
                fig_num = labels[0]
                caption = figure_captions.get(fig_num, "")
                for file_path in pending_images:
                    drawings.append({
                        "file_path": file_path,
                        "figure_label": f"图{fig_num}",
                        "caption": caption,
                    })
                pending_images =[]
                return

            # 多个图号连在一起：每个图号只绑定一张图片，绝不一图多号
            image_count = len(pending_images)
            label_count = len(labels)

            if image_count <= label_count:
                selected_images = pending_images
                selected_labels = labels[-image_count:]
            else:
                leading_images = pending_images[: image_count - label_count]
                selected_images = pending_images[image_count - label_count :]
                selected_labels = labels

                # 多出来的前置图片归入第一个图号（表示同图号多图，不做图组合并）
                first_fig_num = labels[0]
                first_caption = figure_captions.get(first_fig_num, "")
                for file_path in leading_images:
                    drawings.append({
                        "file_path": file_path,
                        "figure_label": f"图{first_fig_num}",
                        "caption": first_caption,
                    })

            for file_path, fig_num in zip(selected_images, selected_labels):
                caption = figure_captions.get(fig_num, "")
                drawings.append({
                    "file_path": file_path,
                    "figure_label": f"图{fig_num}",
                    "caption": caption,
                })

            pending_images =[]

        i = 0
        while i < len(lines):
            line = lines[i].strip()

            image_match = re.search(r"!\[.*?\]\((.*?)\)", line)
            if image_match:
                file_path = image_match.group(1).strip()
                if abstract_figure and file_path == abstract_figure:
                    i += 1
                    continue
                pending_images.append(file_path)
                i += 1
                continue
            
            # 支持字母和连接符图号（如图1A, 图2b）
            normalized_line = re.sub(r"\[\d{4}\]\s*", "", line)
            label_match = re.match(r"^(?:图|FIG\.?|Fig\.?)\s*([0-9a-zA-Z\-()]+)\s*$", normalized_line, re.IGNORECASE)
            if not label_match:
                label_match = re.match(r"^【図\s*([0-9a-zA-Z\-()]+)】\s*$", normalized_line)
            if label_match:
                labels: List[str] =[]
                while i < len(lines):
                    current = re.sub(r"\[\d{4}\]\s*", "", lines[i]).strip()
                    current_match = re.match(r"^(?:图|FIG\.?|Fig\.?)\s*([0-9a-zA-Z\-()]+)\s*$", current, re.IGNORECASE)
                    if not current_match:
                        current_match = re.match(r"^【図\s*([0-9a-zA-Z\-()]+)】\s*$", current)
                    if not current_match:
                        break
                    labels.append(current_match.group(1))
                    i += 1

                _flush(labels)
                continue

            i += 1

        # 末尾如果有悬空图片，无法可靠绑定图号，丢弃。
        return drawings

    @staticmethod
    def _extract_drawings_zone(md_content: str) -> str:
        """
        返回摘要之后的附图区域文本。
        优先从摘要后开始，避免把首页扫描图混入附图列表。
        """
        text = RuleBasedExtractor._normalize_document_text(md_content)
        abstract_match = re.search(r"\(57\)", text)
        start_idx = abstract_match.end() if abstract_match else 0
        search_cutoff = re.search(r"(?im)^#\s*(?:EUROPEAN SEARCH REPORT|ANNEX TO THE EUROPEAN SEARCH REPORT)\b", text)
        end_idx = search_cutoff.start() if search_cutoff else len(text)
        return text[start_idx:end_idx].strip()

    @staticmethod
    def _extract_figure_captions(md_content: str) -> Dict[str, str]:
        """从附图说明提取 图号 -> 图题。"""
        figure_captions: Dict[str, str] = {}
        text = RuleBasedExtractor._normalize_document_text(md_content)
        brief_desc_text = RuleBasedExtractor._extract_section_by_headings(
            text,
            [r"附图说明", r"BRIEF DESCRIPTION OF THE DRAWINGS", r"【図面の簡単な説明】"],
            [
                r"具体实施方式",
                r"DETAILED DESCRIPTION(?: OF THE PREFERRED EMBODIMENTS OF THE INVENTION)?",
                r"【発明を実施するための形態】",
                r"【符号の説明】",
                r"Claims",
            ],
        )
        if not brief_desc_text:
            brief_desc_text = text

        for raw_line in brief_desc_text.split("\n"):
            line = re.sub(r"\[\d{4}\]\s*", "", raw_line).strip()
            if not line:
                continue

            # 支持图1表示/示出/为/是/: ... 等句式
            head_match = re.search(r"^图\s*([0-9a-zA-Z\-()]+)\s*(.*)$", line)
            if not head_match:
                head_match = re.search(r"^(?:FIG\.?|Figure)\s*([0-9A-Za-z\-()]+)\s*(.*)$", line, re.IGNORECASE)
            if not head_match:
                head_match = re.search(r"^【図\s*([0-9A-Za-z\-()]+)】\s*(.*)$", line)
            if not head_match:
                continue

            fig_num = head_match.group(1)
            remainder = head_match.group(2).strip()
            if not remainder:
                continue

            if re.search(r"附图标记|図面符号", remainder):
                continue

            caption = re.sub(
                r"^(?:(?:是|为|示出|表示|[:：]|is|shows?|illustrates?|depicts?|respectively show|を示す|である)\s*)+",
                "",
                remainder,
                flags=re.IGNORECASE,
            ).strip("；;。. ")
            if not caption:
                continue
            if fig_num not in figure_captions:
                figure_captions[fig_num] = caption

            for sub_match in re.finditer(
                r"图\s*(\d+)\s*的\s*\(([A-Za-z])\)\s*(?:为|是|示出|表示|[:：])\s*([^；;。]+)",
                line,
                re.IGNORECASE,
            ):
                sub_key = f"{sub_match.group(1)}({sub_match.group(2)})"
                sub_caption = sub_match.group(3).strip("；;。. ")
                if sub_caption and sub_key not in figure_captions:
                    figure_captions[sub_key] = sub_caption

        return figure_captions

    # ================= 著录项目字段提取 =================

    @staticmethod
    def _extract_application_number(md_content: str) -> str:
        text = RuleBasedExtractor._to_search_text(md_content)
        return RuleBasedExtractor._find_first_group(
            text,
            [
                r"\(21\)\s*(?:申请号|Application number|Appl\.?\s*No\.?|出願番号)\s*:?\s*([^\n]+)",
                r"\(21\)\s*[^\n]{0,30}?([A-Za-z]*\d[\dA-Za-z./,\-() ]+)",
            ],
            re.IGNORECASE,
        )

    @staticmethod
    def _extract_application_date(md_content: str) -> str:
        text = RuleBasedExtractor._to_search_text(md_content)
        raw = RuleBasedExtractor._find_first_group(
            text,
            [
                r"\(22\)\s*(?:申请日|Date of filing|Filed|出願日)\s*:?\s*([^\n]+)",
                r"\(22\)\s*([^\n]+)",
            ],
            re.IGNORECASE,
        )
        return RuleBasedExtractor._extract_date_value(raw)

    @staticmethod
    def _extract_priority_date(md_content: str) -> str:
        text = RuleBasedExtractor._to_search_text(md_content)
        block_pattern = r"\(30\)\s*(?:优先权数据|Foreign Application Priority Data)?\s*([\s\S]*?)(?=(?:\(\d+\)|#|$))"
        block_match = re.search(block_pattern, text, re.DOTALL | re.IGNORECASE)
        search_text = block_match.group(1) if block_match else ""

        date_pattern = r"(\d{4}\s*[.\-/]\s*\d{1,2}\s*[.\-/]\s*\d{1,2})"
        date_matches = re.findall(date_pattern, search_text)
        if not date_matches and block_match:
            date_matches = re.findall(date_pattern, block_match.group(0))
        if not date_matches and search_text:
            normalized = RuleBasedExtractor._extract_date_value(search_text)
            return normalized if normalized else ""
        if not date_matches:
            return ""

        normalized_dates = []
        for raw_date in date_matches:
            normalized = RuleBasedExtractor._normalize_date(raw_date)
            if normalized:
                normalized_dates.append(normalized)

        if not normalized_dates:
            return ""

        return sorted(normalized_dates)[0]

    @staticmethod
    def _extract_publication_number(md_content: str) -> str:
        text = RuleBasedExtractor._to_search_text(md_content)
        value = RuleBasedExtractor._find_first_group(
            text,
            [
                r"(?m)^\s*\((?:10|11)\)\s*(?:公开号|授权公告号|公告号|Pub\.\s*No\.?|Publication\s+No\.?|Patent\s+No\.?|特許出願公開番号|公開番号)\s*:?\s*([^\n]+)",
                r"(?m)^\s*\((?:10|11)\)\s*[^\n]{0,40}?([A-Za-z]{2}(?:[\s-]*\d[\dA-Za-z./,\-\s]*[A-Za-z0-9]))",
            ],
            re.IGNORECASE,
        )
        return RuleBasedExtractor._normalize_publication_number(value)

    @staticmethod
    def _extract_publication_date(md_content: str) -> str:
        text = RuleBasedExtractor._to_search_text(md_content)
        raw = RuleBasedExtractor._find_first_group(
            text,
            [
                r"\((?:43|45)\)\s*(?:公开日|授权公告日|公告日|Date of publication|Pub\.\s*Date|公開日)\s*:?\s*([^\n]+)",
                r"\((?:43|45)\)\s*([^\n]+)",
            ],
            re.IGNORECASE,
        )
        return RuleBasedExtractor._extract_date_value(raw)

    @staticmethod
    def _extract_invention_title(md_content: str) -> str:
        text = RuleBasedExtractor._to_search_text(md_content)
        value = RuleBasedExtractor._find_first_group(
            text,
            [
                r"\(54\)\s*(?:发明名称|实用新型名称|外观设计名称)\s*\n*\s*([^\n#]+)",
                r"\(54\)\s*([^\n#]+)",
            ],
            re.IGNORECASE,
        )
        value = re.sub(r"^[\[【(].{0,20}?(?:名称|title)[^)\]】]{0,8}[)\]】]?\s*", "", value, flags=re.IGNORECASE)
        return RuleBasedExtractor._clean_field_value(value)

    @staticmethod
    def _extract_ipc_classifications(md_content: str) -> list:
        ipc_blocks: List[str] = []
        for field_no in ("51", "52"):
            block = RuleBasedExtractor._extract_field_block(md_content, field_no)
            if not block:
                continue
            ipc_blocks.append(block)

        if not ipc_blocks:
            return []

        ipc_text = "\n".join(ipc_blocks)
        ipc_text = re.sub(
            r"(?i)\b(?:Int\s*\.\s*[Cc][LlIi1]\.?|IPC|International Patent Classification|U\.S\.\s*Cl\.?)\b\s*:?",
            " ",
            ipc_text,
        )
        ipc_pattern = re.compile(
            r"([A-Z])\s*([0-9OIlL]{2})\s*([A-Z])\s*([0-9OIlL]+)\s*/\s*([0-9OIlL]+)(?:\s*\(\d{4}\.\d{2}\))?",
            re.IGNORECASE,
        )
        ipc_codes = []

        for item in ipc_pattern.finditer(ipc_text):
            section = item.group(1).upper()
            class_digits = RuleBasedExtractor._normalize_ocr_digits(item.group(2))
            subclass = item.group(3).upper()
            main_group = RuleBasedExtractor._normalize_ocr_digits(item.group(4))
            sub_group = RuleBasedExtractor._normalize_ocr_digits(item.group(5))

            if not (class_digits.isdigit() and main_group.isdigit() and sub_group.isdigit()):
                continue

            ipc_code = f"{section}{class_digits}{subclass} {main_group}/{sub_group}"
            if ipc_code not in ipc_codes:
                ipc_codes.append(ipc_code)

        return ipc_codes

    @staticmethod
    def _extract_applicants(md_content: str) -> list:
        applicants =[]
        applicant_text = RuleBasedExtractor._extract_field_block(md_content, "71")
        if not applicant_text:
            applicant_text = RuleBasedExtractor._extract_field_block(md_content, "73")
        if not applicant_text:
            return applicants

        applicant_text = re.sub(
            r"^(?:申请人|专利权人|Applicant(?:s)?|出願人)\s*:?\s*",
            "",
            applicant_text,
            flags=re.IGNORECASE,
        ).strip()
        lines =[line.strip() for line in applicant_text.split('\n') if line.strip()]
        if not lines:
            return applicants

        current_name = ""
        current_address = ""

        def _flush_current() -> None:
            nonlocal current_name, current_address
            name = str(current_name or "").strip()
            if not name:
                current_name = ""
                current_address = ""
                return
            applicants.append({
                "name": name,
                "address": str(current_address or "").strip(),
            })
            current_name = ""
            current_address = ""

        for raw_line in lines:
            line = re.sub(r"\[\d{4}\]\s*", "", raw_line).strip()
            if not line:
                continue

            parsed = RuleBasedExtractor._split_applicant_line(line)
            if not parsed:
                continue
            name_part, address_part, has_address_marker = parsed

            if has_address_marker:
                if name_part:
                    _flush_current()
                    applicants.append({
                        "name": name_part,
                        "address": address_part,
                    })
                    continue

                # 地址独占一行时，优先绑定到当前申请人；若无当前项则补到上一项
                if current_name:
                    current_address = address_part
                    _flush_current()
                    continue
                if applicants and not applicants[-1].get("address"):
                    applicants[-1]["address"] = address_part
                continue

            if current_name:
                _flush_current()
            current_name = name_part

        _flush_current()
        return applicants

    @staticmethod
    def _split_applicant_line(line: str) -> tuple[str, str, bool] | None:
        """拆分申请人行：返回 (name, address, has_address_marker)。"""
        text = str(line or "").strip().strip("；;")
        if not text:
            return None

        text = re.sub(r"^(?:申请人|专利权人)\s*", "", text).strip()
        if not text:
            return None

        marker_match = re.search(r"(通讯地址|联系地址|住址|地址)\s*[:：]?\s*", text)
        if not marker_match:
            return text.strip("，,。 "), "", False

        marker = marker_match.group(1)
        name_part = text[: marker_match.start()].strip("，,。:： ")
        address_part = text[marker_match.end() :].strip("，,。:： ")

        # 避免把公司名中偶发的“地址”误判为地址标记
        if marker == "地址" and name_part and address_part:
            if not RuleBasedExtractor._looks_like_address(address_part):
                return text.strip("，,。 "), "", False

        address_part = re.sub(r"\s*\(.*?\)\s*$", "", address_part).strip()
        return name_part, address_part, True

    @staticmethod
    def _looks_like_address(text: str) -> bool:
        value = str(text or "").strip()
        if not value:
            return False
        return bool(re.match(
            r"^(?:\d{6}|中国|[^\s]{1,8}(?:省|市|自治区|特别行政区)|[^\s]{1,8}(?:区|县|镇|乡|街道)|[^\s]{1,12}(?:路|街|道|号|弄|室))",
            value,
        ))

    @staticmethod
    def _normalize_ocr_digits(text: str) -> str:
        """将 OCR 常见字符误识别归一化为数字。"""
        return (text or "").translate(str.maketrans({
            "O": "0",
            "o": "0",
            "I": "1",
            "l": "1",
            "L": "1",
        }))

    @staticmethod
    def _normalize_publication_number(value: str) -> str:
        cleaned = RuleBasedExtractor._clean_field_value(value)
        if not cleaned:
            return ""

        cleaned = re.sub(
            r"^(?:Pub\.\s*No\.?|Publication\s+No\.?|Patent\s+No\.?|公开号|授权公告号|公告号|特許出願公開番号|公開番号)\s*:?\s*",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\s*-\s*", "-", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" \t\r\n,;.")

        core_match = re.search(
            r"([A-Za-z]{2}(?:[\s-]*\d[\dA-Za-z./,\-\s]*[A-Za-z0-9]))",
            cleaned,
            re.IGNORECASE,
        )
        if core_match:
            cleaned = core_match.group(1).strip()

        if not re.search(r"\d", cleaned):
            return ""

        return re.sub(r"\s{2,}", " ", cleaned)

    @staticmethod
    def _normalize_date(text: str) -> str | None:
        cleaned = (text or "").strip()
        match = re.search(r"(\d{4})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})", cleaned)
        if not match:
            return None
        year = int(match.group(1))
        month = int(match.group(2))
        day = int(match.group(3))
        if month < 1 or month > 12 or day < 1 or day > 31:
            return None
        return f"{year:04d}.{month:02d}.{day:02d}"

    @staticmethod
    def _split_people(raw_text: str) -> List[str]:
        text = str(raw_text or "").strip()
        if not text:
            return []

        primary = [item.strip() for item in re.split(r"[;；，,\n]+|\s{2,}", text) if item.strip()]
        if len(primary) == 1:
            candidate = primary[0]
            if " " in candidate and re.fullmatch(r"[\u4e00-\u9fff·\s]+", candidate):
                primary = [item.strip() for item in re.split(r"\s+", candidate) if item.strip()]

        deduped: List[str] = []
        for name in primary:
            if name not in deduped:
                deduped.append(name)
        return deduped

    @staticmethod
    def _extract_inventors(md_content: str) -> list:
        inventor_text = RuleBasedExtractor._extract_field_block(md_content, "72")
        if inventor_text:
            inventor_text = re.sub(
                r"^(?:发明人|Inventor(?:s)?|発明者|龚明者)\s*:?\s*",
                "",
                inventor_text,
                flags=re.IGNORECASE,
            ).strip()
            if "地址" in inventor_text:
                inventor_text = inventor_text.split("地址")[0].strip()
            if ";" in inventor_text:
                inventors = []
                for segment in inventor_text.split(";"):
                    name = segment.strip()
                    if "," in name:
                        head = name.split(",", 1)[0].strip()
                        if re.search(r"\([A-Z]{2}\)$", name) and head:
                            name = head
                    name = re.sub(r"\b\d{4,6}\b.*$", "", name).strip(" ,")
                    if name and name not in inventors:
                        inventors.append(name)
                if inventors:
                    return inventors
            inventor_text = inventor_text.split("\n", 1)[0].strip()
            inventor_text = re.sub(r"\b\d{4,6}\b.*$", "", inventor_text).strip(" ,")
            if re.fullmatch(r"[A-Za-zÀ-ÿ'.\- ]+,\s*[A-Za-zÀ-ÿ'.\- ]+", inventor_text):
                return [inventor_text]
            return RuleBasedExtractor._split_people(inventor_text)
        return[]

    @staticmethod
    def _extract_agency(md_content: str) -> dict:
        agency_block = RuleBasedExtractor._extract_field_block(md_content, "74")
        if agency_block:
            agency_block = agency_block.strip()
            first_line = agency_block.split("\n", 1)[0]
            agency_raw = re.sub(
                r"^(?:专利代理机构|代理人|专利代理师|Representative|代理人)\s*:?\s*",
                "",
                first_line,
                flags=re.IGNORECASE,
            ).strip()
            agency_name = re.sub(r"\d{3,}$", "", agency_raw).strip()

            agents =[]
            agent_pattern = r"(?:专利代理师|代理人)\s*([^\n]+)"
            agent_match = re.search(agent_pattern, agency_block)
            if agent_match:
                agents_raw = agent_match.group(1)
                agents = RuleBasedExtractor._split_people(agents_raw)

            return {"agency_name": agency_name, "agents": agents}
        return None

    @staticmethod
    def _extract_abstract(md_content: str) -> str:
        text = RuleBasedExtractor._normalize_document_text(md_content)
        pattern = r"\(57\)\s*(?:摘要|ABSTRACT|【要約】)?\s*([\s\S]*?)(?=!\[|^#\s*(?:Claims|Description|Publication Classification|BRIEF DESCRIPTION OF THE DRAWINGS|DETAILED DESCRIPTION|청구항\s*1)|^【特許請求の範囲】|^1\s*[\.．]\s*|\Z)"
        match = re.search(pattern, text, flags=re.DOTALL | re.MULTILINE | re.IGNORECASE)
        if match:
            abstract = match.group(1).strip()
            abstract = re.sub(r"!\[.*?\]\(.*?\)", "", abstract)
            abstract = re.sub(r"\s+", " ", abstract)
            return abstract.strip()
        title_match = re.search(r"(?m)^\s*#\s*\(54\)\s*[^\n]+\n+\s*#?\s*\n+([\s\S]*?)(?=\n#\s|\n\(|\Z)", text)
        if title_match:
            abstract = title_match.group(1).strip()
            abstract = re.sub(r"\s+", " ", abstract)
            if len(abstract) >= 40:
                return abstract
        return ""

    @staticmethod
    def _extract_abstract_figure(md_content: str) -> str:
        text = RuleBasedExtractor._normalize_document_text(md_content)
        heading_match = re.search(r"\(57\)\s*(?:摘要|ABSTRACT|【要約】)?", text, flags=re.IGNORECASE)
        if heading_match:
            search_end = min(heading_match.end() + 500, len(text))
            after_abstract = text[heading_match.end() : search_end]
            heading_stop = re.search(r"(?m)^#\s+|^【特許請求の範囲】|^1\s*[\.．]\s*", after_abstract)
            if heading_stop:
                after_abstract = after_abstract[: heading_stop.start()]
            fig_match = re.search(r"!\[.*?\]\((.*?)\)", after_abstract)
            if fig_match:
                return fig_match.group(1).strip()
        return ""

    # ================= 权利要求与说明书字段提取 =================

    @staticmethod
    def extract_structured_claims(claims_section: str) -> List[Dict[str, object]]:
        """
        解析权利要求文本为结构化列表。
        从 1. 开始到结尾，每个序号之间文本为一项权利要求。
        """
        claims: List[Dict[str, object]] =[]
        if not claims_section:
            return claims

        section = RuleBasedExtractor._normalize_document_text(claims_section)

        if re.search(r"【請求項\d+】", section):
            pattern = r"【請求項(\d+)】\s*([\s\S]*?)(?=【請求項\d+】|【発明の詳細な説明】|\Z)"
        elif re.search(r"(?m)^#\s*청구항\s*\d+\s*$", section):
            pattern = r"(?m)^#\s*청구항\s*(\d+)\s*$\s*([\s\S]*?)(?=^#\s*청구항\s*\d+\s*$|\Z)"
        else:
            pattern = r"(?m)^\s*(\d+)\s*[\.．]\s*([\s\S]*?)(?=(?:^\s*\d+\s*[\.．]\s*)|\Z)"

        matches = re.finditer(pattern, section)

        for match in matches:
            claim_id = match.group(1).strip()
            claim_text = match.group(2).strip()
            claim_text = re.sub(r"(?m)^\s*\d+\s*$", "", claim_text)
            claim_text = re.sub(r"\s+", " ", claim_text).strip()
            if not claim_text:
                continue

            claim_type = RuleBasedExtractor._classify_claim_type(claim_text)
            parent_claim_ids = RuleBasedExtractor._extract_parent_claim_ids(claim_text, claim_type, claim_id)
            claims.append({
                "claim_id": claim_id,
                "claim_text": claim_text,
                "claim_type": claim_type,
                "parent_claim_ids": parent_claim_ids,
            })

        return claims

    @staticmethod
    def _classify_claim_type(claim_text: str) -> str:
        """
        根据是否以“一种”开头判定独权；
        根据是否以“根据权利要求”开头判定从权。
        """
        text = str(claim_text or "").strip()
        if re.match(r"^\s*(?:根据|如|按照|依照)\s*权利要求", text):
            return "dependent"
        if re.match(r"^\s*权利要求\s*\d+", text):
            return "dependent"
        if re.search(r"\bclaim\s+\d+\b", text, re.IGNORECASE):
            return "dependent"
        if re.search(r"\bpreceding claims?\b", text, re.IGNORECASE):
            return "dependent"
        if re.search(r"請求項\s*[0-9０-９]+", text):
            return "dependent"
        if re.search(r"제\s*[0-9０-９]+\s*항", text):
            return "dependent"
        return "independent"

    @staticmethod
    def _extract_parent_claim_ids(claim_text: str, claim_type: str, claim_id: str = "") -> List[str]:
        """提取从属权利要求的直接父权项编号（不展开祖先）。"""
        if claim_type != "dependent":
            return []

        text = str(claim_text or "").strip()
        if not text:
            return []

        if re.search(
            r"(?:any of the preceding claims|any preceding claim|前記請求項\d+から\d+|請求項\d+から\d+のいずれか1項|any of claims?\s+\d+\s*(?:to|-)\s*\d+)",
            text,
            re.IGNORECASE,
        ) and str(claim_id).isdigit():
            current_id = int(claim_id)
            if current_id > 1 and re.search(r"preceding claims", text, re.IGNORECASE):
                return [str(value) for value in range(1, current_id)]

        head_match = re.search(r"(其特征在于|其特征为|特征在于)", text)
        head_text = text[: head_match.start()] if head_match else text[:160]
        candidates: List[str] = []

        for match in re.finditer(r"权利要求([0-9０-９、,，及和或与\-~～至到\s]+)", head_text):
            expr = match.group(1)
            for claim_id in RuleBasedExtractor._parse_claim_id_expression(expr):
                if claim_id not in candidates:
                    candidates.append(claim_id)

        for match in re.finditer(r"claims?\s+([0-9０-９,\-~～toandor\s]+)", head_text, re.IGNORECASE):
            expr = match.group(1)
            for parent_id in RuleBasedExtractor._parse_claim_id_expression(expr):
                if parent_id not in candidates:
                    candidates.append(parent_id)

        for match in re.finditer(r"請求項([0-9０-９、,，又はまたは及びおよび\-~～から\s]+)", head_text):
            expr = match.group(1)
            for parent_id in RuleBasedExtractor._parse_claim_id_expression(expr):
                if parent_id not in candidates:
                    candidates.append(parent_id)

        for match in re.finditer(r"제\s*([0-9０-９]+(?:\s*항)?(?:\s*(?:또는|및|내지)\s*제?\s*[0-9０-９]+\s*항?)*)", head_text):
            expr = match.group(1)
            for parent_id in RuleBasedExtractor._parse_claim_id_expression(expr):
                if parent_id not in candidates:
                    candidates.append(parent_id)

        if candidates:
            return candidates

        # 兜底：若“权利要求”后未完整命中表达式，尝试从首句提取数字范围
        first_sentence = re.split(r"[。；;\n]", head_text, maxsplit=1)[0]
        if "权利要求" in first_sentence:
            fallback_expr = first_sentence.split("权利要求", 1)[-1]
            return RuleBasedExtractor._parse_claim_id_expression(fallback_expr)

        return []

    @staticmethod
    def _parse_claim_id_expression(expr: str) -> List[str]:
        """将 '1或2'、'1至3任一项' 等表达式展开为编号列表。"""
        text = str(expr or "")
        if not text:
            return []

        text = text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
        text = text.replace("至", "-").replace("到", "-").replace("～", "-").replace("~", "-").replace("から", "-").replace("내지", "-").replace("to", "-")
        text = re.sub(r"[、，,;；]|及|和|或|与|以及|or|and|または|又は|および|及び|또는|및", ",", text, flags=re.IGNORECASE)
        text = re.sub(
            r"(所述|任意一项|任一项|任一|之一|中任一项|中任一|任意|請求項|claims?|claim|제|항|項|に記載の|に記載|記載の|いずれか1項|いずれか一項)",
            "",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"\s+", "", text)
        text = re.sub(r"[^0-9,\-]", "", text)
        if not text:
            return []

        results: List[str] = []
        for token in [part for part in text.split(",") if part]:
            range_match = re.fullmatch(r"(\d+)-(\d+)", token)
            if range_match:
                start = int(range_match.group(1))
                end = int(range_match.group(2))
                if start <= end and end - start <= 50:
                    for value in range(start, end + 1):
                        claim_id = str(value)
                        if claim_id not in results:
                            results.append(claim_id)
                else:
                    for value in (str(start), str(end)):
                        if value not in results:
                            results.append(value)
                continue

            if token.isdigit() and token not in results:
                results.append(token)

        return results

    @staticmethod
    def _extract_technical_field(md_content: str) -> str:
        return RuleBasedExtractor._extract_section_by_headings(
            md_content,
            [r"技术领域", r"FIELD OF THE INVENTION", r"【技術分野】"],
            [
                r"背景技术",
                r"BACKGROUND",
                r"【背景技術】",
                r"发明内容",
                r"SUMMARY OF THE INVENTION",
                r"【発明の概要】",
            ],
        )

    @staticmethod
    def _extract_background_art(md_content: str) -> str:
        return RuleBasedExtractor._extract_section_by_headings(
            md_content,
            [r"背景技术", r"BACKGROUND", r"【背景技術】"],
            [
                r"发明内容",
                r"SUMMARY OF THE INVENTION",
                r"【発明の概要】",
                r"先行技術文献",
                r"【先行技術文献】",
            ],
        )

    @staticmethod
    def _extract_summary_and_effect(md_content: str) -> tuple:
        """从发明内容中精准拆分 技术方案(summary) 与 技术效果(effect)"""
        content = RuleBasedExtractor._extract_section_by_headings(
            md_content,
            [
                r"发明内容",
                r"实用新型内容",
                r"外观设计简要说明",
                r"SUMMARY OF THE INVENTION",
                r"【発明の概要】",
            ],
            [
                r"附图说明",
                r"BRIEF DESCRIPTION OF THE DRAWINGS",
                r"【図面の簡単な説明】",
                r"具体实施方式",
                r"DETAILED DESCRIPTION(?: OF THE PREFERRED EMBODIMENTS OF THE INVENTION)?",
                r"【発明を実施するための形態】",
            ],
        )
        if not content and "【課題を解決するための手段】" in md_content:
            summary = RuleBasedExtractor._extract_section_by_headings(
                md_content,
                [r"【課題を解決するための手段】"],
                [r"【発明の効果】", r"【図面の簡単な説明】", r"【発明を実施するための形態】"],
            )
            effect = RuleBasedExtractor._extract_section_by_headings(
                md_content,
                [r"【発明の効果】"],
                [r"【図面の簡単な説明】", r"【発明を実施するための形態】"],
            )
            return summary or "", effect or ""
        if not content:
            return "", ""

        def _clean_section_text(text: str) -> str:
            return re.sub(r"\[\d{4}\]\s*", "", str(text or "")).strip()

        # 优先识别“发明效果/有益效果/技术效果”作为显式分界标题
        heading_match = re.search(
            r"(?m)^\s*(?:\[\d{4}\]\s*)?(?:发明效果|有益效果|技术效果)\s*[：:]?\s*",
            content,
        )
        if heading_match:
            summary = _clean_section_text(content[: heading_match.start()])
            effect = _clean_section_text(content[heading_match.start() :])
            effect = re.sub(r"^(?:发明效果|有益效果|技术效果)\s*[：:]?\s*", "", effect).strip()
            return summary or "", effect or ""
        
        split_pattern = r"(\[?\d{4}\]?\s*(?:总体而言[，,]\s*)?(?:通过.*?)?与现有技术相比[^\n]*?(?:有益|技术)效果[。：]?|\[?\d{4}\]?\s*(?:有益|技术)效果[：为。]|\[?\d{4}\]?\s*优点[：为。]|\[?\d{4}\]?\s*本(?:发明|实用新型)的?(?:有益)?效果[^\n]*?[：。])"
        
        matches = list(re.finditer(split_pattern, content))
        if matches:
            split_idx = matches[-1].start()
            summary = content[:split_idx].strip()
            effect = content[split_idx:].strip()
            
            summary = _clean_section_text(summary)
            effect = _clean_section_text(effect)
            return summary or "", effect or ""
            
        backup_pattern = r"(\[?\d{4}\]?\s*[^\[\n]*(?:本(?:发明|实用新型).*?(?:有益|效果|优点)|通过.*?实现.*?效果)[^\n]*)"
        backup_matches = list(re.finditer(backup_pattern, content))
        if backup_matches:
            split_idx = backup_matches[-1].start()
            summary = content[:split_idx].strip()
            effect = content[split_idx:].strip()
            
            summary = _clean_section_text(summary)
            effect = _clean_section_text(effect)
            return summary or "", effect or ""
            
        summary = _clean_section_text(content)
        return summary or "", ""

    @staticmethod
    def _extract_brief_description(md_content: str) -> str:
        """提取附图标记说明中的“标记-名称”，支持数字与字母数字标记。"""
        content = RuleBasedExtractor._extract_section_by_headings(
            md_content,
            [r"附图说明", r"【図面の簡単な説明】", r"BRIEF DESCRIPTION OF THE DRAWINGS"],
            [
                r"具体实施方式",
                r"DETAILED DESCRIPTION(?: OF THE PREFERRED EMBODIMENTS OF THE INVENTION)?",
                r"【発明を実施するための形態】",
                r"【符号の説明】",
            ],
        )
        if not content:
            return ""
        content = re.sub(r"\[\d{4}\]\s*", "", content).strip()
        if not content:
            return ""

        # 优先在“附图标记说明”子区块提取，避免误吸收图解说明句
        marker_block_match = re.search(
            r"(?:附图标记(?:说明|如下|说明如下|对照|释义)?(?:表)?\s*[：:]?)\s*([\s\S]*)$",
            content,
            re.DOTALL,
        )
        marker_block_text = marker_block_match.group(1).strip() if marker_block_match else ""

        # 支持 D1/N/T/2A/101 等标记，并避免把“图1...”中的1误识别为标记
        marker_pattern = re.compile(
            r"(?<!图)\b([A-Za-z]+\d+[A-Za-z]?|\d+[A-Za-z]?|[A-Za-z]{1,3})\b\s*[-：:、\.\s]\s*([^、，,。；;\n]+)"
        )
        def _collect_items(text: str) -> List[str]:
            items: List[str] = []
            seen = set()
            for item in marker_pattern.finditer(text):
                marker = item.group(1).strip()
                name = item.group(2).strip()
                if not marker or not name:
                    continue
                normalized = f"{marker}-{name}"
                if normalized in seen:
                    continue
                seen.add(normalized)
                items.append(normalized)
            return items

        # 先尝试“附图标记说明”子区块，若无结果再回退全附图说明，兼容无固定标识的文档
        items = _collect_items(marker_block_text) if marker_block_text else []
        if not items:
            items = _collect_items(content)

        if items:
            return "、".join(items)

        return ""

    @staticmethod
    def _extract_detailed_description(md_content: str) -> str:
        content = RuleBasedExtractor._extract_section_by_headings(
            md_content,
            [
                r"具体实施方式",
                r"DETAILED DESCRIPTION(?: OF THE PREFERRED EMBODIMENTS OF THE INVENTION)?",
                r"Description",
                r"【発明を実施するための形態】",
            ],
            [r"Claims", r"【符号の説明】", r"EUROPEAN SEARCH REPORT"],
        )
        if not content:
            return ""

        content = re.sub(r"\[\d{4}\]\s*", "", content)
        tail_pattern = r"(?:\s|!\[.*?\]\(.*?\)|(?:^|\n)\s*(?:图|FIG\.?|Fig\.?|【図)\s*[0-9a-zA-Z\-]+[】]?)+$"
        content = re.sub(tail_pattern, "", content, flags=re.IGNORECASE)
        return content.strip()
