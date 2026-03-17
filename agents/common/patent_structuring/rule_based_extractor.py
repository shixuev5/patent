import re
from typing import List, Dict
from loguru import logger

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
            # 统一换行符，避免不同操作系统的回车换行干扰正则匹配
            md_content = md_content.replace("\r\n", "\n")

            result = {
                "bibliographic_data": RuleBasedExtractor._parse_bibliographic_data(md_content),
                "claims": RuleBasedExtractor._parse_claims(md_content),
                "description": RuleBasedExtractor._parse_description(md_content),
                "drawings": RuleBasedExtractor._parse_drawings(md_content),
            }

            logger.success("规则抽取器解析完成")
            return result

        except Exception as e:
            logger.exception(f"规则抽取器解析失败: {e}")
            return {}

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
        
        # 1. 定位并截取仅属于权利要求的区域
        # 为了避免匹配到前文内容，从 (57)摘要 之后开始寻找
        abstract_match = re.search(r"\(57\)\s*摘要", md_content)
        start_search_pos = abstract_match.end() if abstract_match else 0
        
        # 寻找真正的权利要求起点：行首的 1. 或 1．
        start_match = re.search(r"(?m)^1\s*[\.．]\s*", md_content[start_search_pos:])
        if not start_match:
            return claims
            
        start_idx = start_search_pos + start_match.start()
        
        # 寻找权利要求终点：第一个 Markdown 标题（如 "# 一种基于..." 或 "# 技术领域"）
        end_match = re.search(r"(?m)^#+\s+", md_content[start_idx:])
        end_idx = start_idx + end_match.start() if end_match else len(md_content)
            
        # 截取纯净的权利要求段落
        claims_section = md_content[start_idx:end_idx].strip()

        # 2. 从限定的纯净文本中解析结构化权利要求
        for item in RuleBasedExtractor.extract_structured_claims(claims_section):
            claims.append({
                "claim_id": item.get("claim_id", ""),
                "claim_text": item["claim_text"],
                "claim_type": item["claim_type"],
                "parent_claim_ids": item.get("parent_claim_ids", []),
            })
            
        return claims

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
                pending_images.append(file_path)
                i += 1
                continue
            
            # 支持字母和连接符图号（如图1A, 图2b）
            label_match = re.match(r"^图\s*([0-9a-zA-Z\-]+)\s*$", re.sub(r"\[\d{4}\]\s*", "", line))
            if label_match:
                labels: List[str] =[]
                while i < len(lines):
                    current = re.sub(r"\[\d{4}\]\s*", "", lines[i]).strip()
                    current_match = re.match(r"^图\s*([0-9a-zA-Z\-]+)\s*$", current)
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
        返回从“# 具体实施方式”标题后到文末的文本。
        若未找到该标题，则返回空字符串。
        """
        match = re.search(r"#+\s*具体实施方式\s*([\s\S]*)$", md_content, re.DOTALL)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _extract_figure_captions(md_content: str) -> Dict[str, str]:
        """从附图说明提取 图号 -> 图题。"""
        figure_captions: Dict[str, str] = {}
        brief_desc_pattern = r"#+\s*附图说明\s*([\s\S]*?)(?=#|$)"
        brief_desc_match = re.search(brief_desc_pattern, md_content, re.DOTALL)
        if not brief_desc_match:
            return figure_captions

        brief_desc_text = brief_desc_match.group(1)
        for raw_line in brief_desc_text.split("\n"):
            line = re.sub(r"\[\d{4}\]\s*", "", raw_line).strip()
            if not line:
                continue

            # 支持图1表示/示出/为/是/: ... 等句式
            head_match = re.search(r"^图\s*([0-9a-zA-Z\-]+)\s*(.*)$", line)
            if not head_match:
                continue

            fig_num = head_match.group(1)
            remainder = head_match.group(2).strip()
            if not remainder:
                continue

            # 排除“图1的附图标记如下”及其变体等非图题行
            if re.search(r"附图标记", remainder):
                continue

            caption = re.sub(r"^(?:是|为|示出|表示|[:：])+\s*", "", remainder).strip("；;。 ")
            if not caption:
                continue
            if fig_num not in figure_captions:
                figure_captions[fig_num] = caption

        return figure_captions

    # ================= 著录项目字段提取 =================

    @staticmethod
    def _extract_application_number(md_content: str) -> str:
        pattern = r"\(21\)\s*申请号\s*([^\s]+)"
        match = re.search(pattern, md_content)
        return match.group(1).strip() if match else None

    @staticmethod
    def _extract_application_date(md_content: str) -> str:
        pattern = r"\(22\)\s*申请日\s*(\d{4}\s*[\.\-]\s*\d{1,2}\s*[\.\-]\s*\d{1,2})"
        match = re.search(pattern, md_content)
        return match.group(1).replace(" ", "") if match else None

    @staticmethod
    def _extract_priority_date(md_content: str) -> str:
        block_pattern = r"\(30\)\s*优先权数据\s*([\s\S]*?)(?=(?:\(\d+\)|#|$))"
        block_match = re.search(block_pattern, md_content, re.DOTALL)
        search_text = block_match.group(1) if block_match else ""

        date_pattern = r"(\d{4}\s*[.\-/]\s*\d{1,2}\s*[.\-/]\s*\d{1,2})"
        date_matches = re.findall(date_pattern, search_text)
        if not date_matches and block_match:
            date_matches = re.findall(date_pattern, block_match.group(0))
        if not date_matches:
            return None

        normalized_dates = []
        for raw_date in date_matches:
            normalized = RuleBasedExtractor._normalize_date(raw_date)
            if normalized:
                normalized_dates.append(normalized)

        if not normalized_dates:
            return None

        return sorted(normalized_dates)[0]

    @staticmethod
    def _extract_publication_number(md_content: str) -> str:
        pattern = r"\((?:19|10|11)\)\s*(?:公开号|授权公告号|公告号)\s*([^\s]+)"
        match = re.search(pattern, md_content)
        return match.group(1).strip() if match else None

    @staticmethod
    def _extract_publication_date(md_content: str) -> str:
        pattern = r"\((?:43|45)\)\s*(?:公开日|授权公告日|公告日)\s*(\d{4}\s*[\.\-]\s*\d{1,2}\s*[\.\-]\s*\d{1,2})"
        match = re.search(pattern, md_content)
        return match.group(1).replace(" ", "") if match else None

    @staticmethod
    def _extract_invention_title(md_content: str) -> str:
        pattern = r"\(54\)\s*(?:发明名称|实用新型名称|外观设计名称)\s*\n*\s*([^\n#]+)"
        match = re.search(pattern, md_content)
        return match.group(1).strip() if match else None

    @staticmethod
    def _extract_ipc_classifications(md_content: str) -> list:
        pattern = r"\(51\)\s*Int\s*\.\s*[Cc][LlIi1]\.?\s*([\s\S]*?)(?=(?:\(\d+\)|\([A-Z]{2,}\)|#|$))"
        match = re.search(pattern, md_content, re.DOTALL)
        if not match:
            return[]

        ipc_text = match.group(1).strip()
        ipc_pattern = re.compile(
            r"([A-Z])([0-9OIlL])([0-9OIlL])([A-Z])\s*([0-9OIlL]+)\s*/\s*([0-9OIlL]+)(?:\s*\(\d{4}\.\d{2}\))?",
            re.IGNORECASE,
        )
        ipc_codes =[]

        for item in ipc_pattern.finditer(ipc_text):
            section = item.group(1).upper()
            class_digits = RuleBasedExtractor._normalize_ocr_digits(item.group(2) + item.group(3))
            subclass = item.group(4).upper()
            main_group = RuleBasedExtractor._normalize_ocr_digits(item.group(5))
            sub_group = RuleBasedExtractor._normalize_ocr_digits(item.group(6))

            if not (class_digits.isdigit() and main_group.isdigit() and sub_group.isdigit()):
                continue

            ipc_code = f"{section}{class_digits}{subclass} {main_group}/{sub_group}"
            if ipc_code not in ipc_codes:
                ipc_codes.append(ipc_code)

        return ipc_codes

    @staticmethod
    def _extract_applicants(md_content: str) -> list:
        applicants =[]
        pattern = r"\((?:71|73)\)\s*(?:申请人|专利权人)\s*([\s\S]*?)(?=\(\d+\)|#|$)"
        match = re.search(pattern, md_content, re.DOTALL)
        if not match:
            return applicants

        applicant_text = match.group(1).strip()
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
        pattern = r"\(72\)\s*发明人\s*([\s\S]*?)(?=(?:\(\d+\)|#|$))"
        match = re.search(pattern, md_content, re.DOTALL)
        if match:
            inventor_text = match.group(1).strip()
            if "地址" in inventor_text:
                inventor_text = inventor_text.split("地址")[0].strip()

            return RuleBasedExtractor._split_people(inventor_text)
        return[]

    @staticmethod
    def _extract_agency(md_content: str) -> dict:
        pattern = r"\(74\)\s*专利代理机构\s*([^\n]+)"
        match = re.search(pattern, md_content)
        if match:
            agency_raw = match.group(1).strip()
            agency_name = re.sub(r"\d{3,}$", "", agency_raw).strip()

            agents =[]
            agent_pattern = r"(?:专利代理师|代理人)\s*([^\n]+)"
            agent_match = re.search(agent_pattern, md_content)
            if agent_match:
                agents_raw = agent_match.group(1)
                agents = RuleBasedExtractor._split_people(agents_raw)

            return {"agency_name": agency_name, "agents": agents}
        return None

    @staticmethod
    def _extract_abstract(md_content: str) -> str:
        pattern = r"\(57\)\s*摘要\s*([\s\S]*?)(?=^1\s*[\.．]\s*|#|$)"
        match = re.search(pattern, md_content, flags=re.DOTALL | re.MULTILINE)
        if match:
            abstract = match.group(1).strip()
            abstract = re.sub(r"!\[.*?\]\(.*?\)", "", abstract)
            abstract = re.sub(r"\s+", " ", abstract)
            return abstract.strip()
        return None

    @staticmethod
    def _extract_abstract_figure(md_content: str) -> str:
        pattern = r"\(57\)\s*摘要\s*([\s\S]*?)(?=^1\s*[\.．]\s*|#|$)"
        match = re.search(pattern, md_content, flags=re.DOTALL | re.MULTILINE)
        if match:
            abstract_end = match.end()
            search_end = min(abstract_end + 300, len(md_content))
            after_abstract = md_content[abstract_end:search_end]
            fig_match = re.search(r"!\[.*?\]\((.*?)\)", after_abstract)
            if fig_match:
                return fig_match.group(1).strip()
        return None

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

        pattern = r"(?m)^(\d+)\s*[\.．]\s*([\s\S]*?)(?=(?:^\d+\s*[\.．]\s*)|\Z)"
        matches = re.finditer(pattern, claims_section)

        for match in matches:
            claim_id = match.group(1).strip()
            claim_text = match.group(2).strip()
            if not claim_text:
                continue

            claim_type = RuleBasedExtractor._classify_claim_type(claim_text)
            parent_claim_ids = RuleBasedExtractor._extract_parent_claim_ids(claim_text, claim_type)
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
        if text.startswith("一种"):
            return "independent"
        return "unknown"

    @staticmethod
    def _extract_parent_claim_ids(claim_text: str, claim_type: str) -> List[str]:
        """提取从属权利要求的直接父权项编号（不展开祖先）。"""
        if claim_type != "dependent":
            return []

        text = str(claim_text or "").strip()
        if not text:
            return []

        head_match = re.search(r"(其特征在于|其特征为|特征在于)", text)
        head_text = text[: head_match.start()] if head_match else text[:160]
        candidates: List[str] = []

        for match in re.finditer(r"权利要求([0-9０-９、,，及和或与\-~～至到\s]+)", head_text):
            expr = match.group(1)
            for claim_id in RuleBasedExtractor._parse_claim_id_expression(expr):
                if claim_id not in candidates:
                    candidates.append(claim_id)

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
        text = re.sub(r"(所述|任意一项|任一项|任一|之一|中任一项|中任一|任意)", "", text)
        text = text.replace("至", "-").replace("到", "-").replace("～", "-").replace("~", "-")
        text = re.sub(r"[、，,;；]|及|和|或|与|以及", ",", text)
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
        pattern = r"#+\s*技术领域\s*([\s\S]*?)(?=#|$)"
        match = re.search(pattern, md_content, re.DOTALL)
        if match:
            return re.sub(r"\[\d{4}\]\s*", "", match.group(1).strip())
        return None

    @staticmethod
    def _extract_background_art(md_content: str) -> str:
        pattern = r"#+\s*背景技术\s*([\s\S]*?)(?=#|$)"
        match = re.search(pattern, md_content, re.DOTALL)
        if match:
            return re.sub(r"\[\d{4}\]\s*", "", match.group(1).strip())
        return None

    @staticmethod
    def _extract_summary_and_effect(md_content: str) -> tuple:
        """从发明内容中精准拆分 技术方案(summary) 与 技术效果(effect)"""
        pattern = r"#+\s*(?:发明内容|实用新型内容|外观设计简要说明)\s*([\s\S]*?)(?=#|$)"
        match = re.search(pattern, md_content, re.DOTALL)
        if not match:
            return None, None
            
        content = match.group(1).strip()

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
            return summary or None, effect or None
        
        split_pattern = r"(\[?\d{4}\]?\s*(?:总体而言[，,]\s*)?(?:通过.*?)?与现有技术相比[^\n]*?(?:有益|技术)效果[。：]?|\[?\d{4}\]?\s*(?:有益|技术)效果[：为。]|\[?\d{4}\]?\s*优点[：为。]|\[?\d{4}\]?\s*本(?:发明|实用新型)的?(?:有益)?效果[^\n]*?[：。])"
        
        matches = list(re.finditer(split_pattern, content))
        if matches:
            split_idx = matches[-1].start()
            summary = content[:split_idx].strip()
            effect = content[split_idx:].strip()
            
            summary = _clean_section_text(summary)
            effect = _clean_section_text(effect)
            return summary or None, effect or None
            
        backup_pattern = r"(\[?\d{4}\]?\s*[^\[\n]*(?:本(?:发明|实用新型).*?(?:有益|效果|优点)|通过.*?实现.*?效果)[^\n]*)"
        backup_matches = list(re.finditer(backup_pattern, content))
        if backup_matches:
            split_idx = backup_matches[-1].start()
            summary = content[:split_idx].strip()
            effect = content[split_idx:].strip()
            
            summary = _clean_section_text(summary)
            effect = _clean_section_text(effect)
            return summary or None, effect or None
            
        summary = _clean_section_text(content)
        return summary or None, None

    @staticmethod
    def _extract_brief_description(md_content: str) -> str:
        """提取附图标记说明中的“标记-名称”，支持数字与字母数字标记。"""
        pattern = r"#+\s*附图说明\s*([\s\S]*?)(?=#|$)"
        match = re.search(pattern, md_content, re.DOTALL)
        if not match:
            return None

        content = re.sub(r"\[\d{4}\]\s*", "", match.group(1)).strip()
        if not content:
            return None

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

        return None

    @staticmethod
    def _extract_detailed_description(md_content: str) -> str:
        pattern = r"#+\s*具体实施方式\s*([\s\S]*?)(?=#|$)"
        match = re.search(pattern, md_content, re.DOTALL)
        if match:
            content = match.group(1).strip()
            
            # 1. 剔除段首常见的 [0001] 等段落编号
            content = re.sub(r"\[\d{4}\]\s*", "", content)
            
            # 2. 精准剥离文末的“附图+图号”区域，保留正文内的公式图片
            # 正则解释：
            # (?: ... )+$ 表示从文末倒推，匹配一个或多个以下组合，直到碰到正常正文为止
            #   \s+                             匹配换行符、空格
            #   !\[.*?\]\(.*?\)                 匹配Markdown图片
            #   (?:^|\n)\s*图\s*[0-9a-zA-Z\-]+   匹配独立成行的图号（如：图1、图2A）
            tail_pattern = r"(?:\s|!\[.*?\]\(.*?\)|(?:^|\n)\s*图\s*[0-9a-zA-Z\-]+)+$"
            
            # 仅替换掉文末的附图区，正文中间的 ![公式](...) 会安然无恙
            content = re.sub(tail_pattern, "", content)
                
            # 3. 去除首尾多余空白字符
            return content.strip()
        return None
