import re
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
        logger.info("[RuleBasedExtractor] Starting rule-based parsing...")

        try:
            # 统一换行符，避免不同操作系统的回车换行干扰正则匹配
            md_content = md_content.replace("\r\n", "\n")

            result = {
                "bibliographic_data": RuleBasedExtractor._parse_bibliographic_data(md_content),
                "claims": RuleBasedExtractor._parse_claims(md_content),
                "description": RuleBasedExtractor._parse_description(md_content),
                "drawings": RuleBasedExtractor._parse_drawings(md_content),
            }

            logger.success("[RuleBasedExtractor] Parsing completed successfully")
            return result

        except Exception as e:
            logger.exception(f"[RuleBasedExtractor] Parsing failed: {e}")
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
        claim_texts = RuleBasedExtractor._extract_claim_texts(md_content)

        for text in claim_texts:
            clean_text = text.strip()
            
            # 规则 1：根据是否以 "一种" 开头为独权、以 "根据权利要求" 开头为从权
            if clean_text.startswith("一种"):
                claim_type = "independent"
            elif clean_text.startswith("根据权利要求"):
                claim_type = "dependent"
            else:
                # 兜底：如果都不匹配，默认视作独权
                claim_type = "independent"

            claims.append({
                "claim_text": text,
                "claim_type": claim_type
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
        figure_captions = {}
        
        # 1. 从附图说明部分精确提取图注信息
        brief_desc_pattern = r"#+\s*附图说明\s*([\s\S]*?)(?=#|$)"
        brief_desc_match = re.search(brief_desc_pattern, md_content, re.DOTALL)
        if brief_desc_match:
            brief_desc_text = brief_desc_match.group(1)
            for line in brief_desc_text.split('\n'):
                line = re.sub(r"\[\d{4}\]\s*", "", line).strip()
                if not line:
                    continue
                # 兼容多种格式: "图1是为..." / "图2中(a)为..." / "图3为..." / "图4："
                match = re.search(r"^图\s*(\d+)(?:[^，,。；;\n]*?[为是：:])?\s*(.*)", line)
                if match:
                    fig_num = match.group(1)
                    caption = match.group(2).strip("；;。 ")
                    
                    # 规则 3：图表标题只取第一次匹配行的文本（防止把后面的附图标记等追加进来）
                    if fig_num not in figure_captions:
                        figure_captions[fig_num] = caption
                        
        # 2. 匹配 Markdown 图片格式 ![](images/xxx.jpg) 后跟 图 X
        image_pattern = r"!\[.*?\]\((.*?)\)\s*(?:<br>|\n)*\s*图\s*(\d+)"
        image_matches = re.finditer(image_pattern, md_content)
        
        for match in image_matches:
            file_path = match.group(1).strip()
            fig_num = match.group(2)
            caption = figure_captions.get(fig_num, "")
            drawings.append({
                "file_path": file_path,
                "figure_label": f"图{fig_num}",
                "caption": caption
            })

        return drawings

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
        pattern = r"\(30\)\s*优先权数据\s*(\d{4}\s*[\.\-]\s*\d{1,2}\s*[\.\-]\s*\d{1,2})"
        match = re.search(pattern, md_content)
        return match.group(1).replace(" ", "") if match else None

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
        pattern = r"\(54\)\s*发明名称\s*\n*\s*([^\n]+)"
        match = re.search(pattern, md_content)
        return match.group(1).strip() if match else None

    @staticmethod
    def _extract_ipc_classifications(md_content: str) -> list:
        pattern = r"\(51\)\s*Int\s*\.\s*[Cc][LlIi1]\.?\s*([\s\S]*?)(?=(?:\(\d+\)|#|$))"
        match = re.search(pattern, md_content, re.DOTALL)
        if match:
            ipc_text = match.group(1).strip()
            ipc_pattern = r"([A-Z][0-9]{2}[A-Z]?\s*\d+/\d+(?:\s*\(\d{4}\.\d{2}\))?)"
            matches = re.findall(ipc_pattern, ipc_text)
            processed_matches =[]
            for m in matches:
                main_class = re.sub(r"\s*\(\d{4}\.\d{2}\)", "", m).strip()
                processed_matches.append(main_class)
            return processed_matches
        return[]

    @staticmethod
    def _extract_applicants(md_content: str) -> list:
        applicants =[]
        pattern = r"\(71\)\s*申请人\s*([\s\S]*?)(?=\(\d+\)|#|$)"
        match = re.search(pattern, md_content, re.DOTALL)
        
        if match:
            applicant_text = match.group(1).strip()
            lines =[line.strip() for line in applicant_text.split('\n') if line.strip()]
            
            current_applicant = {"name": "", "address": ""}
            
            for line in lines:
                if line.startswith("地址"):
                    address = line.replace("地址", "").strip()
                    address = re.sub(r"\s*\(.*?\)\s*$", "", address)
                    if not current_applicant["name"]:
                        current_applicant["name"] = "Unknown"
                    current_applicant["address"] = address
                    
                    applicants.append(current_applicant)
                    current_applicant = {"name": "", "address": ""}
                else:
                    if current_applicant["name"]:
                        applicants.append(current_applicant)
                        current_applicant = {"name": "", "address": ""}
                    current_applicant["name"] = line
            
            if current_applicant["name"]:
                applicants.append(current_applicant)

        return applicants

    @staticmethod
    def _extract_inventors(md_content: str) -> list:
        pattern = r"\(72\)\s*发明人\s*([\s\S]*?)(?=(?:\(\d+\)|#|$))"
        match = re.search(pattern, md_content, re.DOTALL)
        if match:
            inventor_text = match.group(1).strip()
            if "地址" in inventor_text:
                inventor_text = inventor_text.split("地址")[0].strip()
            inventors = re.split(r"[\s;；，,\n]+", inventor_text)
            return [name.strip() for name in inventors if name.strip()]
        return[]

    @staticmethod
    def _extract_agency(md_content: str) -> dict:
        pattern = r"\(74\)\s*专利代理机构\s*([^\n]+)"
        match = re.search(pattern, md_content)
        if match:
            agency_raw = match.group(1).strip()
            agency_name = re.sub(r"\s+\d+$", "", agency_raw).strip()
            
            agents =[]
            agent_pattern = r"(?:专利代理师|代理人)\s*([^\n]+)"
            agent_match = re.search(agent_pattern, md_content)
            if agent_match:
                agents_raw = agent_match.group(1)
                agents = [name.strip() for name in re.split(r"[\s;；，,]+", agents_raw) if name.strip()]
                
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
    def _extract_claim_texts(md_content: str) -> list:
        claims =[]
        abstract_match = re.search(r"\(57\)\s*摘要", md_content)
        start_search_pos = abstract_match.end() if abstract_match else 0
        
        start_match = re.search(r"(?m)^1\s*[\.．]\s*", md_content[start_search_pos:])
        if not start_match:
            return claims
            
        start_idx = start_search_pos + start_match.start()
        
        end_match = re.search(r"(?m)^#+\s+", md_content[start_idx:])
        end_idx = start_idx + end_match.start() if end_match else len(md_content)
            
        claims_section = md_content[start_idx:end_idx].strip()
        
        pattern = r"(?m)^(\d+)\s*[\.．]\s*([\s\S]*?)(?=(?:^\d+\s*[\.．]\s*)|\Z)"
        matches = re.finditer(pattern, claims_section)
        
        for match in matches:
            claim_text = match.group(2).strip()
            if claim_text:
                claims.append(claim_text)

        return claims

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
        pattern = r"#+\s*发明内容\s*([\s\S]*?)(?=#|$)"
        match = re.search(pattern, md_content, re.DOTALL)
        if not match:
            return None, None
            
        content = match.group(1).strip()
        
        split_pattern = r"(\[?\d{4}\]?\s*(?:总体而言[，,]\s*)?(?:通过.*?)?与现有技术相比[^\n]*?(?:有益|技术)效果[。：]?|\[?\d{4}\]?\s*(?:有益|技术)效果[：为。]|\[?\d{4}\]?\s*优点[：为。]|\[?\d{4}\]?\s*本发明的?(?:有益)?效果[^\n]*?[：。])"
        
        matches = list(re.finditer(split_pattern, content))
        if matches:
            split_idx = matches[-1].start()
            summary = content[:split_idx].strip()
            effect = content[split_idx:].strip()
            
            summary = re.sub(r"\[\d{4}\]\s*", "", summary).strip()
            effect = re.sub(r"\[\d{4}\]\s*", "", effect).strip()
            return summary, effect if effect else None
            
        backup_pattern = r"(\[?\d{4}\]?\s*[^\[\n]*(?:本发明.*?(?:有益|效果|优点)|通过.*?实现.*?效果)[^\n]*)"
        backup_matches = list(re.finditer(backup_pattern, content))
        if backup_matches:
            split_idx = backup_matches[-1].start()
            summary = content[:split_idx].strip()
            effect = content[split_idx:].strip()
            
            summary = re.sub(r"\[\d{4}\]\s*", "", summary).strip()
            effect = re.sub(r"\[\d{4}\]\s*", "", effect).strip()
            return summary, effect
            
        summary = re.sub(r"\[\d{4}\]\s*", "", content).strip()
        return summary, None

    @staticmethod
    def _extract_brief_description(md_content: str) -> str:
        """
        规则 2：提取 # 附图说明 章节从后到最后一个图名标题行的内容，如果为空返回 None
        """
        pattern = r"#+\s*附图说明\s*([\s\S]*?)(?=#|$)"
        match = re.search(pattern, md_content, re.DOTALL)
        if match:
            content = match.group(1).strip()
            # 去除段落编号
            content = re.sub(r"\[\d{4}\]\s*", "", content)
            
            lines =[line.strip() for line in content.split('\n') if line.strip()]
            last_title_idx = -1
            
            # 从后往前找最后一个图名标题行
            for i in range(len(lines) - 1, -1, -1):
                # 排除带有 "中："（标号说明）的行，其余以 "图X" 开头的视为图名标题行
                if re.match(r"^图\s*\d+(?!.*中[：:]).*$", lines[i]):
                    last_title_idx = i
                    break
                    
            if last_title_idx != -1 and last_title_idx < len(lines) - 1:
                part_list_content = "\n".join(lines[last_title_idx + 1:]).strip()
                if part_list_content:
                    return part_list_content
                    
        return None

    @staticmethod
    def _extract_detailed_description(md_content: str) -> str:
        pattern = r"#+\s*具体实施方式\s*([\s\S]*?)(?=#|$)"
        match = re.search(pattern, md_content, re.DOTALL)
        if match:
            content = match.group(1).strip()
            content = re.sub(r"\[\d{4}\]\s*", "", content)
            
            img_start_idx = content.find("![")
            if img_start_idx != -1:
                content = content[:img_start_idx].strip()
                
            return content
        return None