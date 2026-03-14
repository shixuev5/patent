import html
import re
from pathlib import Path
from typing import Dict, Any, List, Optional
from loguru import logger

from agents.common.rendering.report_render import render_markdown_to_pdf


class ReportRenderer:
    _HTML_TAG_RE = re.compile(r"<\s*/?\s*(?:div|span|p|a|img|table|tbody|thead|tr|td|th|ul|li|b|strong|i|em|h[1-6]|br|hr)\b[^>]*>", re.IGNORECASE)
    _HTML_COMMENT_RE = re.compile(r"(?s)<!--.*?-->")
    _SCRIPT_STYLE_RE = re.compile(r"(?is)<\s*(script|style)\b.*?>.*?<\s*/\s*\1\s*>")
    _CODE_FENCE_OPEN_RE = re.compile(r"```[a-zA-Z0-9_-]*\n?")

    def __init__(self, patent_data: Dict[str, Any]):
        self.patent_data = patent_data
        self._sanitized_html_fragments_count = 0

    def _indent_text(self, text: str) -> str:
        """
        辅助函数：给文本首行添加两个全角空格缩进 (HTML实体)
        """
        if not text:
            return ""
        # 移除可能存在的首尾空白，然后添加缩进
        clean_text = text.strip()
        if not clean_text:
            return ""
        return f"&emsp;&emsp;{clean_text}"
    
    def _md_bold_to_html(self, text):
        """
        将 **文本** 转换为 <strong>文本</strong>
        
        :param text: 说明
        """
        if not text: return "-"
        # 将 **text** 替换为 <strong>text</strong>，re.DOTALL 允许跨行匹配
        return re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', str(text), flags=re.DOTALL)

    def _sanitize_llm_text(self, value: Any) -> str:
        """清理 LLM 产物中的代码围栏与 HTML 标签，避免破坏 Markdown 结构。"""
        text = str(value or "")
        if not text:
            return ""

        text = html.unescape(text)
        text, removed_script_style = self._SCRIPT_STYLE_RE.subn("", text)
        text, removed_comments = self._HTML_COMMENT_RE.subn("", text)
        text, removed_fence_open = self._CODE_FENCE_OPEN_RE.subn("", text)
        text, removed_fence_tail = re.subn(r"```", "", text)
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text, removed_tags = self._HTML_TAG_RE.subn("", text)

        lines = [line.strip() for line in text.splitlines()]
        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip()

        self._sanitized_html_fragments_count += (
            removed_script_style
            + removed_comments
            + removed_fence_open
            + removed_fence_tail
            + removed_tags
        )
        return text

    def _safe_text(self, value: Any, default: str = "") -> str:
        cleaned = self._sanitize_llm_text(value)
        if cleaned:
            return cleaned
        return default

    def render(
        self,
        report_data: Dict[str, Any],
        search_data: Optional[Dict[str, Any]],
        md_path: Path,
        pdf_path: Path,
    ):
        """
        主入口：组装分析报告和检索策略，生成 MD 和 PDF
        """
        logger.info("开始渲染报告内容")
        self._sanitized_html_fragments_count = 0

        parts = []

        # 1. 渲染分析报告部分
        if report_data:
            parts.append(self._render_analysis_section(report_data))

        # 2. 渲染检索策略部分
        if search_data:
            # 添加分页符，确保检索策略从新页面开始
            parts.append("\n<div style='page-break-before: always;'></div>\n")
            parts.append(self._render_search_section(search_data))

        full_md_content = "\n".join(parts)
        if self._sanitized_html_fragments_count > 0:
            logger.warning(
                f"报告渲染阶段清理了 {self._sanitized_html_fragments_count} 处 HTML/代码围栏片段"
            )

        # 4. 写入 .md 文件
        try:
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(full_md_content, encoding="utf-8")
            logger.success(f"Markdown 报告生成完成: {md_path}")
        except Exception as e:
            logger.error(f"写入 Markdown 失败: {e}")
            raise

        # 4. 导出 .pdf 文件
        self._export_pdf(full_md_content, pdf_path)
        if not pdf_path.exists() or pdf_path.stat().st_size == 0:
            raise RuntimeError(f"PDF generation failed: output file missing or empty: {pdf_path}")

    def _render_analysis_section(self, data: Dict[str, Any]) -> str:
        """
        渲染第一部分：专利技术分析报告
        顺序：AI标题 -> 摘要 -> 主图 -> 技术问题 -> 技术手段(含特征) -> 技术效果 -> 图解说明
        """
        lines = []
        title = self._safe_text(data.get("ai_title"), "未命名 AI 分析报告")
        lines.append(f"# {title}\n")

        lines.append("## 摘要")
        abstract = self._safe_text(data.get("ai_abstract"), "暂无摘要")
        lines.append(f"{self._indent_text(abstract)}\n")

        main_fig = str(data.get("abstract_figure") or "").strip()
        if main_fig:
            lines.append(f"![Main Figure]({main_fig})\n")

        section_no = 1

        def append_numbered_section(title_text: str) -> None:
            nonlocal section_no
            lines.append(f"## {section_no}. {title_text}")
            section_no += 1

        append_numbered_section("技术领域")
        domain = self._safe_text(data.get("technical_field"), "未提取到技术领域")
        lines.append(f"{self._indent_text(domain)}\n")

        append_numbered_section("现有技术问题")
        problem = self._safe_text(data.get("technical_problem"), "未提取到技术问题")
        lines.append(f"{self._indent_text(problem)}\n")

        bg_knowledge = data.get("background_knowledge", [])
        if isinstance(bg_knowledge, list) and bg_knowledge:
            append_numbered_section("核心概念百科")
            lines.append(
                "> 💡 *阅读提示：以下是本案涉及的关键术语解释，旨在辅助非本领域人员理解技术方案。*\n"
            )

            for item in bg_knowledge:
                if not isinstance(item, dict):
                    continue
                term = self._safe_text(item.get("term"), "未命名术语")
                definition = self._safe_text(item.get("definition"), "-")
                analogy = self._safe_text(item.get("analogy"), "-")
                context = self._safe_text(item.get("context_in_patent"), "-")

                card_html = f"""<div style="border: 1px solid #dfe2e5; margin-bottom: 20px; page-break-inside: avoid; background-color: #fff;">
<div style="background-color: #f2f6f9; padding: 6px 8px; border-bottom: 1px solid #dfe2e5;">
<strong style="color: #2c3e50; font-size: 14px;">{term}</strong>
</div>
<div style="display: flex; flex-direction: row; border-bottom: 1px solid #dfe2e5;">
<div style="flex: 1; padding: 8px; border-right: 1px solid #dfe2e5;">
<div style="font-size: 12px; font-weight: bold; margin-bottom: 4px;">专业定义</div>
<div>{self._indent_text(definition)}</div>
</div>
<div style="flex: 1; padding: 8px;">
<div style="font-size: 12px; font-weight: bold; margin-bottom: 4px;">通俗理解</div>
<div>{self._indent_text(analogy)}</div>
</div>
</div>
<div style="padding: 6px 8px;">
<span style="font-size: 12px; font-weight: bold; ">本案应用：</span>
<span style="font-size: 12px;">{context}</span>
</div>
</div>"""
                lines.append(card_html)
            lines.append("\n")

        append_numbered_section("技术方案概要")
        subject_matter = self._safe_text(data.get("claim_subject_matter"))
        if subject_matter:
            lines.append(f"> **🛡️ 保护主题**：{subject_matter}\n")

        scheme = self._safe_text(data.get("technical_scheme"), "未提取到技术方案")
        if "\n" not in scheme:
            scheme = self._indent_text(scheme)
        lines.append(f"{scheme}\n")

        append_numbered_section("核心技术手段")
        means = self._safe_text(data.get("technical_means"), "未提取到技术手段")
        lines.append(f"{self._indent_text(means)}\n")

        features = data.get("technical_features", [])
        feature_name_map = {}
        if isinstance(features, list) and features:
            lines.append("### 关键技术特征表")

            table_html = """<table>
<thead>
<tr>
<th style="width: 28px; text-align: center;">序号</th>
<th style="width: 20%;">特征名称</th>
<th style="width: 70px; text-align: center;">属性</th>
<th>详细定义</th>
</tr>
</thead>
<tbody>"""
            feature_idx = 0
            for feat in features:
                if not isinstance(feat, dict):
                    continue
                feature_idx += 1
                name = self._safe_text(feat.get("name"), "-")
                feature_name_map[name.strip()] = feature_idx

                desc_raw = self._safe_text(feat.get("description"))
                rationale_raw = self._safe_text(feat.get("rationale"))
                desc = self._md_bold_to_html(desc_raw.replace("\n", "<br>"))
                rationale = self._md_bold_to_html(rationale_raw.replace("\n", "<br>"))

                is_distinguishing = feat.get("is_distinguishing", False)
                source = str(feat.get("claim_source", "")).lower()

                if is_distinguishing:
                    badge_text = "🌟 区别特征"
                elif "independent" in source:
                    badge_text = "⚪ 前序特征"
                else:
                    badge_text = "🔹 从权特征"

                table_html += f"""<tr>
<td rowspan="2" style="text-align: center; font-weight: bold; background-color: #f8f9fa;">{feature_idx}</td>
<td style="font-weight: bold;">{name}</td>
<td style="text-align: center;">{badge_text}</td>
<td>{desc}</td>
</tr>
<tr>
<td colspan="3">{rationale}</td>
</tr>"""

            table_html += "</tbody></table>\n"
            lines.append(table_html)

        append_numbered_section("技术效果")
        effects = data.get("technical_effects", [])
        if isinstance(effects, list) and effects:
            table_html = """<table>
<thead>
<tr>
<th style="width: 28px; text-align: center;">序号</th>
<th>技术效果</th>
<th style="width: 60px; text-align: center;">TCS 评分</th>
<th style="width: 40%;">贡献特征</th>
<th style="width: 40px; text-align: center;">检索分块</th>
</tr>
</thead>
<tbody>"""

            effect_idx = 0
            for eff in effects:
                if not isinstance(eff, dict):
                    continue
                effect_idx += 1
                desc = self._safe_text(eff.get("effect"), "未命名效果")
                score = int(eff.get("tcs_score", 0) or 0)

                if score >= 5:
                    score_html = f"<span style='color: #dc3545;'>🔴 {score}</span>"
                    abc = "Block B"
                elif score == 4:
                    score_html = f"<span style='color: #fd7e14;'>🟠 {score}</span>"
                    abc = "Block C<br>(核心)"
                elif score == 3:
                    score_html = f"<span style='color: #ffc107;'>🟡 {score}</span>"
                    abc = "Block C<br>(可选)"
                else:
                    score_html = f"<span style='color: #6c757d;'>⚪ {score}</span>"
                    abc = "Block A"

                contributors = eff.get("contributing_features", [])
                if isinstance(contributors, list) and contributors:
                    formatted_items = []
                    for c in contributors:
                        c_clean = self._safe_text(c).strip()
                        if not c_clean:
                            continue
                        feat_idx = feature_name_map.get(c_clean)
                        if feat_idx:
                            formatted_items.append(f"{c_clean} [{feat_idx}]")
                        else:
                            formatted_items.append(c_clean)
                    if formatted_items:
                        list_items = "".join([f"<li>{item}</li>" for item in formatted_items])
                        contrib_html = f"<ul style='margin: 0;'>{list_items}</ul>"
                    else:
                        contrib_html = "-"
                else:
                    contrib_html = "-"

                rationale_raw = self._safe_text(eff.get("rationale"))
                rationale = self._md_bold_to_html(rationale_raw)
                raw_evidence = self._safe_text(eff.get("evidence"))
                evidence_text = self._md_bold_to_html(raw_evidence)

                if "仅声称" in raw_evidence or "无实施例" in raw_evidence:
                    evidence_styled = f"<i style='color: #dc3545;'>⚠️ {evidence_text}</i>"
                else:
                    evidence_styled = evidence_text

                table_html += f"""<tr>
<td rowspan="2" style="text-align: center; font-weight: bold; background-color: #f8f9fa;">{effect_idx}</td>
<td style="font-weight: bold;">{desc}</td>
<td style="text-align: center;">{score_html}</td>
<td>{contrib_html}</td>
<td style="text-align: center;">{abc}</td>
</tr>
<tr>
<td colspan="4">
<div style="margin-bottom: 8px;">
<span style="font-weight:bold; color:#2c3e50;">机理推演：</span>
<span>{rationale}</span>
</div>
<div>
<span style="font-weight:bold; color:#2c3e50;">验证证据：</span>
<span>{evidence_styled}</span>
</div>
</td>
</tr>"""

            table_html += "</tbody></table>\n"
            lines.append(table_html)
        else:
            lines.append("> *未提取到明确的技术效果或评分数据。*\n")

        append_numbered_section("图解说明")
        figures = data.get("figure_explanations", [])
        if not isinstance(figures, list) or not figures:
            lines.append("暂无图片分析。\n")
            return "\n".join(lines)

        for fig in figures:
            if not isinstance(fig, dict):
                continue
            img_paths = fig.get("image_paths") or []
            img_title = self._safe_text(fig.get("image_title"), "图片")
            explanation = self._safe_text(fig.get("image_explanation"))
            parts = fig.get("parts_info", [])

            if img_paths:
                image_html = "\n".join([f'<img src="{path}" alt="{img_title}">' for path in img_paths if path]
                )
                figure_html = f"""<figure>
{image_html}
<figcaption>{img_title}</figcaption>
</figure>"""
                lines.append(figure_html)

            if explanation:
                lines.append(f"\n**【智能解说】**\n\n{self._indent_text(explanation)}\n")

            if isinstance(parts, list) and parts:
                lines.append("\n**【可见部件清单】**\n")
                lines.append("| 标号 | 名称 | 功能/作用 | 空间连接 |")
                lines.append("| :---: | :--- | :--- | :--- |")
                for p in parts:
                    if not isinstance(p, dict):
                        continue
                    pid = self._safe_text(p.get("id"), "-") or "-"
                    pname = self._safe_text(p.get("name"), "-") or "-"
                    pfunc = self._safe_text(p.get("function"), "-") or "-"
                    pspatial = self._safe_text(p.get("spatial_connections"), "-") or "-"
                    lines.append(f"| {pid} | {pname} | {pfunc} | {pspatial} |")
                lines.append("\n")

            lines.append("\n---\n")

        return "\n".join(lines)

    def _render_search_section(self, data: Dict[str, Any]) -> str:
        """
        渲染第二部分：检索策略
        """
        lines = []
        lines.append("# 专利审查检索策略建议书\n")

        # --- 1. 基础信息与时间截点 ---
        # 获取著录项目信息
        biblio = self.patent_data.get("bibliographic_data", {})
        title = self._safe_text(biblio.get("invention_title"), "未知标题")
        app_date = self._safe_text(biblio.get("application_date"), "未知")
        prio_date = self._safe_text(biblio.get("priority_date"))  # 获取优先权日
        applicants_raw = biblio.get("applicants", [])
        inventors_raw = biblio.get("inventors", [])

        applicant_names = []
        if isinstance(applicants_raw, list):
            for item in applicants_raw:
                name = ""
                if isinstance(item, dict):
                    name = self._safe_text(item.get("name")).strip()
                elif item is not None:
                    name = self._safe_text(item).strip()
                if name:
                    applicant_names.append(name)

        inventor_names = []
        if isinstance(inventors_raw, list):
            for item in inventors_raw:
                if item is None:
                    continue
                name = self._safe_text(item).strip()
                if name:
                    inventor_names.append(name)

        applicants_display = "、".join(applicant_names) if applicant_names else "-"
        inventors_display = "、".join(inventor_names) if inventor_names else "-"

        # 确定检索截止日
        if prio_date:
            critical_date = prio_date
            prio_display = f"{prio_date}"
            note_desc = (
                f"本案主张了优先权，**检索截止界限前移至优先权日 {prio_date}**。"
            )
        else:
            critical_date = app_date
            prio_display = "无"
            note_desc = f"本案未主张优先权，**检索截止界限为申请日 {app_date}**。"

        lines.append("## 1. 检索基础信息")
        lines.append(f"- **发明名称**: {title}")
        lines.append(f"- **申请日**: {app_date}")
        lines.append(f"- **优先权日**: {prio_display}")
        lines.append(f"- **申请人**: {applicants_display}")
        lines.append(f"- **发明人**: {inventors_display}")

        # 动态提示块
        lines.append(f"> **📅 检索截止日: {critical_date}**")
        lines.append(
            f"> *注：{note_desc} 重点排查该日期前公开的“现有技术”，同时需警惕该日期前申请、该日期后公开的“抵触申请”（仅限评价新颖性）。*\n"
        )

        # 获取数据源
        matrix = data.get("search_matrix", [])
        semantic = data.get("semantic_strategy", {})
        if not isinstance(semantic, dict):
            semantic = {}
        semantic_queries: List[Dict[str, Any]] = []
        if isinstance(semantic.get("queries"), list):
            for row in semantic.get("queries", []):
                if isinstance(row, dict):
                    semantic_queries.append(row)

        effect_cluster_map: Dict[str, str] = {}
        for row in semantic_queries:
            row_cluster_ids = self._extract_effect_cluster_ids(row)
            effect_text = self._safe_text(row.get("effect"))
            if row_cluster_ids and effect_text:
                for effect_cluster_id in row_cluster_ids:
                    effect_cluster_map[effect_cluster_id] = effect_text

        semantic_name = self._safe_text(semantic.get("name"), "语义检索")
        semantic_desc = self._safe_text(
            semantic.get("description"),
            "基于核心技术手段的自然语言高密度提炼，用于快速召回 X 类/ Y 类文献。",
        )

        if semantic_queries:
            lines.append("## 2. 按核心效果分组检索策略")
            lines.append(f"> **策略逻辑**: {semantic_desc}\n")
            for idx, query_item in enumerate(semantic_queries, start=1):
                block_id = self._safe_text(query_item.get("block_id"), f"B{idx}")
                query_cluster_ids = self._extract_effect_cluster_ids(query_item)
                effect_cluster_id = query_cluster_ids[0] if query_cluster_ids else f"E{idx}"
                effect_text = self._safe_text(query_item.get("effect"), f"核心效果{idx}")
                tcs_score = self._safe_text(query_item.get("tcs_score"), "-")
                content = self._safe_text(query_item.get("content"))

                lines.append(f"### 核心效果{idx}：{effect_text}")
                lines.append(f"> 效果簇：{effect_cluster_id} / 查询块：{block_id}")
                lines.append(f"> TCS评分：{tcs_score}\n")
                lines.append("#### 语义检索")
                lines.append(f"```text\n{content}\n```\n")
                lines.append("#### 检索要素表")
                filtered_matrix = self._filter_matrix_by_effect_cluster(
                    matrix, effect_cluster_id=effect_cluster_id
                )
                lines.extend(
                    self._render_matrix_table(
                        filtered_matrix,
                        effect_cluster_map=effect_cluster_map,
                    )
                )
                lines.append("")
        else:
            # 回退模式：保留全局检索要素和语义展示，避免报告空白
            lines.append("## 2. 检索要素表")
            lines.append("基于权利要求拆解的检索要素、多语言扩展词表及关联分类号：\n")
            lines.extend(
                self._render_matrix_table(
                    matrix,
                    effect_cluster_map=effect_cluster_map,
                )
            )
            lines.append(f"## 3. {semantic_name}\n")
            lines.append(f"> **策略逻辑**: {semantic_desc}\n")
            legacy_content = self._safe_text(semantic.get("content"))
            if legacy_content:
                lines.append(f"```text\n{legacy_content}\n```\n")
            else:
                lines.append("> 未生成语义检索 Query。\n")
            
        return "\n".join(lines)

    def _extract_effect_cluster_ids(self, item: Dict[str, Any]) -> List[str]:
        cluster_ids: List[str] = []
        multi_ids = item.get("effect_cluster_ids")
        if isinstance(multi_ids, list):
            for value in multi_ids:
                text = self._safe_text(value).upper()
                if re.fullmatch(r"E\d+", text) and text not in cluster_ids:
                    cluster_ids.append(text)
        return cluster_ids

    def _filter_matrix_by_effect_cluster(
        self,
        matrix: Any,
        *,
        effect_cluster_id: str,
    ) -> List[Dict[str, Any]]:
        if not isinstance(matrix, list):
            return []
        target = self._safe_text(effect_cluster_id).upper()
        if not target:
            return []

        filtered: List[Dict[str, Any]] = []
        for item in matrix:
            if not isinstance(item, dict):
                continue
            block_id = self._safe_text(item.get("block_id")).upper()
            cluster_ids = self._extract_effect_cluster_ids(item)

            is_common = block_id == "A" or (block_id == "C" and not cluster_ids)
            belongs_to_current = target in cluster_ids
            if is_common or belongs_to_current:
                filtered.append(item)
        return filtered

    def _render_matrix_table(
        self,
        matrix: Any,
        *,
        effect_cluster_map: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        if not isinstance(effect_cluster_map, dict):
            effect_cluster_map = {}
        lines: List[str] = []
        if not isinstance(matrix, list) or not matrix:
            lines.append("> 未生成检索要素表。\n")
            return lines

        role_mapping = {
            "Subject": "Block A<br>(应用/主题)",
            "KeyFeature": "Block B<br>(核心特征)",
            "Functional": "Block C<br>(功能/限定)",
        }
        priority_mapping = {
            "core": "核心",
            "assist": "辅助",
            "filter": "过滤",
        }
        frequency_mapping = {
            "low": "低频",
            "high": "高频",
        }
        type_mapping = {
            "Product_Structure": "实体结构",
            "Method_Process": "方法/工艺",
            "Algorithm_Logic": "算法逻辑",
            "Material_Composition": "材料/组分",
            "Parameter_Condition": "参数/限定",
        }

        lines.append("| 检索分块 | 效果簇 | 关联技术效果 | 检索要素 | 属性标签 | 中文关键词 | 英文关键词 | 分类号 (IPC/CPC) |")
        lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")

        for item in matrix:
            if not isinstance(item, dict):
                continue
            concept = self._safe_text(item.get("element_name"), "-").replace("|", "\\|")
            role_key = item.get("element_role", "Other")
            block_id = self._safe_text(item.get("block_id")).upper()
            cluster_ids = self._extract_effect_cluster_ids(item)

            if block_id:
                if block_id == "A":
                    block_display = "Block A<br>(应用/主题)"
                elif block_id == "C":
                    block_display = "Block C<br>(功能/限定)"
                elif block_id.startswith("B"):
                    block_display = f"Block {block_id}<br>(核心子块)"
                else:
                    block_display = f"Block {block_id}"
            else:
                block_display = role_mapping.get(role_key, f"Block ?<br>({role_key})")

            e_type_raw = self._safe_text(item.get("element_type"))
            e_type_display = type_mapping.get(e_type_raw, e_type_raw)
            if e_type_display:
                concept_display = f"**{concept}**<br><sub>*{e_type_display}*</sub>"
            else:
                concept_display = f"**{concept}**"

            zh_list = item.get("keywords_zh", [])
            en_list = item.get("keywords_en", [])
            ref_list = item.get("ipc_cpc_ref", [])

            zh_cleaned = []
            if isinstance(zh_list, list):
                for value in zh_list:
                    cleaned = self._safe_text(value)
                    if cleaned:
                        zh_cleaned.append(cleaned)
            en_cleaned = []
            if isinstance(en_list, list):
                for value in en_list:
                    cleaned = self._safe_text(value)
                    if cleaned:
                        en_cleaned.append(cleaned)
            ref_cleaned = []
            if isinstance(ref_list, list):
                for value in ref_list:
                    cleaned = self._safe_text(value)
                    if cleaned:
                        ref_cleaned.append(cleaned)

            zh_str = ", ".join(zh_cleaned) if zh_cleaned else "-"
            en_str = ", ".join(en_cleaned) if en_cleaned else "-"
            class_str = "<br>".join(ref_cleaned) if ref_cleaned else "-"
            cluster_display = ",".join(cluster_ids) if cluster_ids else "-"
            effect_names = []
            for cluster_id in cluster_ids:
                effect_name = self._safe_text(effect_cluster_map.get(cluster_id))
                if effect_name:
                    effect_names.append(effect_name)
            effect_display = " / ".join(effect_names) if effect_names else "-"
            term_frequency = frequency_mapping.get(
                self._safe_text(item.get("term_frequency")).lower(), "-"
            )
            priority_tier = priority_mapping.get(
                self._safe_text(item.get("priority_tier")).lower(), "-"
            )
            is_hub_feature = bool(item.get("is_hub_feature", False))
            tag_items = [
                f"类型:{e_type_display or '-'}",
                f"频率:{term_frequency}",
                f"优先级:{priority_tier}",
                f"Hub:{'是' if is_hub_feature else '否'}",
            ]
            tag_display = "<br>".join(tag_items)

            lines.append(
                f"| **{block_display}** | {cluster_display} | {effect_display} | "
                f"{concept_display} | {tag_display} | {zh_str} | {en_str} | {class_str} |"
            )
        lines.append("\n")
        return lines

    def _export_pdf(self, md_text: str, output_path: Path):
        """
        使用 Playwright 将 Markdown (转HTML后) 打印为 PDF
        """
        logger.info("开始生成 PDF")
        render_markdown_to_pdf(
            md_text=md_text,
            output_path=output_path,
            title="Patent Analysis Report",
            enable_mathjax=True,
        )
