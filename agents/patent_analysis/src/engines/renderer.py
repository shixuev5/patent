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
        self.claims = patent_data.get("claims", [])
        self._sanitized_html_fragments_count = 0

    def _safe_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    def _indent_paragraph_text(self, text: str) -> str:
        """
        给普通正文段落添加首行缩进（HTML 实体）。
        """
        if not text:
            return ""
        # 移除可能存在的首尾空白，然后添加缩进
        clean_text = text.strip()
        if not clean_text:
            return ""
        return f"&emsp;&emsp;{clean_text}"

    def _indent_semantic_code_block_text(self, text: str) -> str:
        """
        给语义检索代码块中的每个非空行添加两个全角空格缩进。
        """
        if not text:
            return ""

        indented_lines: List[str] = []
        for line in str(text).splitlines():
            if line.strip():
                indented_lines.append(f"　　{line}")
            else:
                indented_lines.append("")
        return "\n".join(indented_lines)
    
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

    def _normalize_dependent_on_list(self, value: Any) -> List[str]:
        if not isinstance(value, list):
            return []

        normalized: List[str] = []
        for item in value:
            text = self._safe_text(item).strip()
            if not text:
                continue
            if text.lower() in ("null", "none"):
                continue
            if text not in normalized:
                normalized.append(text)
        return normalized

    def _build_claim_relation_map(self) -> Dict[str, Dict[str, Any]]:
        relation_map: Dict[str, Dict[str, Any]] = {}
        for idx, claim in enumerate(self.claims):
            if not isinstance(claim, dict):
                continue

            claim_id = str(claim.get("claim_id", "")).strip() or str(idx + 1)
            claim_type = str(claim.get("claim_type", "")).strip().lower()
            parent_ids_raw = claim.get("parent_claim_ids", [])

            if isinstance(parent_ids_raw, list):
                parent_ids = [
                    str(parent_id).strip()
                    for parent_id in parent_ids_raw
                    if str(parent_id).strip()
                ]
            elif parent_ids_raw is None:
                parent_ids = []
            else:
                parent_text = str(parent_ids_raw).strip()
                parent_ids = [parent_text] if parent_text else []

            relation_map[claim_id] = {
                "claim_type": claim_type,
                "parent_claim_ids": parent_ids,
            }

        return relation_map

    def _render_feature_number_cell(
        self,
        feature_no: str,
        claim_id: str,
        claim_relation_map: Dict[str, Dict[str, Any]],
    ) -> str:
        relation = claim_relation_map.get(claim_id, {})
        claim_type = str(relation.get("claim_type", "")).lower()
        parent_ids = relation.get("parent_claim_ids", [])

        relation_html = ""
        if parent_ids:
            parent_text = ", ".join(parent_ids)
            relation_html = (
                "<div style=\"margin-top: 4px; font-size: 11px; color: #666; "
                "line-height: 1.45; white-space: normal;\">"
                f"引用权 {parent_text}"
                "</div>"
            )

        return (
            "<div>"
            f"<div>{feature_no}</div>"
            f"{relation_html}"
            "</div>"
        )

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
        lines.append(f"{self._indent_paragraph_text(abstract)}\n")

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
        lines.append(f"{self._indent_paragraph_text(domain)}\n")

        append_numbered_section("现有技术问题")
        problem = self._safe_text(data.get("technical_problem"), "未提取到技术问题")
        lines.append(f"{self._indent_paragraph_text(problem)}\n")

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
<div>{self._indent_paragraph_text(definition)}</div>
</div>
<div style="flex: 1; padding: 8px;">
<div style="font-size: 12px; font-weight: bold; margin-bottom: 4px;">通俗理解</div>
<div>{self._indent_paragraph_text(analogy)}</div>
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
            scheme = self._indent_paragraph_text(scheme)
        lines.append(f"{scheme}\n")

        append_numbered_section("核心技术手段")
        means = self._safe_text(data.get("technical_means"), "未提取到技术手段")
        lines.append(f"{self._indent_paragraph_text(means)}\n")

        features = data.get("technical_features", [])
        feature_name_map = {}
        if isinstance(features, list) and features:
            lines.append("### 关键技术特征表")
            claim_relation_map = self._build_claim_relation_map()

            features_by_claim: Dict[str, List[Dict[str, Any]]] = {}
            for feat in features:
                if not isinstance(feat, dict):
                    continue
                claim_id = str(feat.get("claim_id", "")).strip() or "1"
                features_by_claim.setdefault(claim_id, []).append(feat)

            ordered_claim_ids: List[str] = []
            seen_claim_ids = set()

            for idx, claim in enumerate(self.claims):
                c_dict = claim.model_dump() if hasattr(claim, "model_dump") else claim
                if not isinstance(c_dict, dict):
                    continue
                claim_id = str(c_dict.get("claim_id", "")).strip() or str(idx + 1)
                if claim_id in features_by_claim and claim_id not in seen_claim_ids:
                    ordered_claim_ids.append(claim_id)
                    seen_claim_ids.add(claim_id)

            for claim_id in features_by_claim:
                if claim_id not in seen_claim_ids:
                    ordered_claim_ids.append(claim_id)
                    seen_claim_ids.add(claim_id)

            table_html = """<table>
<thead>
<tr>
<th style="width: 48px; text-align: center;">特征编号</th>
<th style="width: 20%;">特征名称</th>
<th style="width: 68px; text-align: center;">属性</th>
<th>详细定义</th>
</tr>
</thead>
<tbody>"""

            for claim_id in ordered_claim_ids:
                claim_features = features_by_claim.get(claim_id, [])
                if not claim_features:
                    continue

                for f_idx, feat in enumerate(claim_features, start=1):
                    name = self._safe_text(feat.get("name"), "-")
                    feature_no = f"{claim_id}.{f_idx}"
                    if name.strip() and name.strip() not in feature_name_map:
                        feature_name_map[name.strip()] = feature_no

                    desc_raw = self._safe_text(feat.get("description"))
                    rationale_raw = self._safe_text(feat.get("rationale"))
                    desc = self._md_bold_to_html(desc_raw.replace("\n", "<br>"))
                    rationale = self._md_bold_to_html(rationale_raw.replace("\n", "<br>"))

                    is_distinguishing = feat.get("is_distinguishing", False)
                    source = str(feat.get("claim_source", "")).lower()
                    is_independent_feature = source == "independent"
                    is_dependent_feature = source == "dependent"
                    is_independent_preamble = (not is_distinguishing) and is_independent_feature
                    if is_distinguishing:
                        badge_text = "🌟 区别特征"
                    elif is_independent_preamble:
                        badge_text = "⚪ 前序特征"
                    else:
                        badge_text = "🔹 从权特征"
                    name_font_weight = (
                        "bold"
                        if is_distinguishing and not is_dependent_feature
                        else "normal"
                    )
                    name_color = "#666" if is_independent_preamble else "#222"

                    feature_number_cell = self._render_feature_number_cell(
                        feature_no=feature_no,
                        claim_id=claim_id,
                        claim_relation_map=claim_relation_map,
                    )

                    table_html += f"""<tr>
<td rowspan="2" style="text-align: center; font-weight: bold; background-color: #f8f9fa; vertical-align: top;">{feature_number_cell}</td>
<td style="font-weight: {name_font_weight}; color: {name_color};">{name}</td>
<td style="text-align: center;">{badge_text}</td>
<td>{desc}</td>
</tr>
<tr>
<td colspan="3">{rationale}</td>
</tr>"""

            table_html += "</tbody></table>\n"
            lines.append(table_html)

        append_numbered_section("技术效果与机理验证")
        raw_effects = data.get("technical_effects",[])
        
        if isinstance(raw_effects, list) and raw_effects:
            # === 1. 智能层级排序算法 (树状构建) ===
            ordered_effects =[]
            
            # 分类节点
            core_nodes =[
                e
                for e in raw_effects
                if isinstance(e, dict) and self._safe_int(e.get("tcs_score"), default=0) >= 5
            ]
            sub_nodes =[
                e
                for e in raw_effects
                if isinstance(e, dict)
                and 3 <= self._safe_int(e.get("tcs_score"), default=0) <= 4
            ]
            base_nodes =[
                e
                for e in raw_effects
                if isinstance(e, dict) and self._safe_int(e.get("tcs_score"), default=0) <= 2
            ]

            # 异常降级处理：如果大模型没有给出任何 5 分，直接按分数降序平铺
            if not core_nodes:
                sorted_raw_effects = sorted(
                    (e for e in raw_effects if isinstance(e, dict)),
                    key=lambda x: self._safe_int(x.get("tcs_score"), default=0),
                    reverse=True,
                )
                ordered_effects =[{"effect_data": e, "level": 0} for e in sorted_raw_effects]
            else:
                # 遍历所有 5 分核心节点，寻找归属于它的子节点
                for core in core_nodes:
                    ordered_effects.append({"effect_data": core, "level": 0})
                    core_features = core.get("contributing_features", [])
                    
                    remaining_sub =[]
                    for sub in sub_nodes:
                        deps = self._normalize_dependent_on_list(sub.get("dependent_on"))
                        # 匹配逻辑：如果子节点声明的依存特征，包含在父节点的贡献特征中（或者反过来）
                        is_match = False
                        if deps:
                            for dep in deps:
                                for cf in core_features:
                                    if cf in dep or dep in cf:
                                        is_match = True
                                        break
                                if is_match:
                                    break
                        
                        if is_match:
                            ordered_effects.append({"effect_data": sub, "level": 1})
                        else:
                            remaining_sub.append(sub)
                    # 更新尚未分配的从属节点
                    sub_nodes = remaining_sub
                
                # 将未能匹配到父节点的 4/3 分节点（模型幻觉或跨权项）补在后面
                sub_nodes.sort(
                    key=lambda x: self._safe_int(x.get("tcs_score"), default=0), reverse=True
                )
                for sub in sub_nodes:
                    ordered_effects.append({"effect_data": sub, "level": 0})
                
                # 最后追加 1-2 分的常规背景特征
                for base in base_nodes:
                    ordered_effects.append({"effect_data": base, "level": 0})

            # === 2. HTML 渲染 ===
            table_html = """<table>
<thead>
<tr>
<th style="width: 28px; text-align: center;">序号</th>
<th>技术效果</th>
<th style="width: 75px; text-align: center;">TCS 评分</th>
<th style="width: 35%;">贡献特征</th>
<th style="width: 65px; text-align: center;">检索分块</th>
</tr>
</thead>
<tbody>"""

            for effect_idx, item in enumerate(ordered_effects, 1):
                eff = item["effect_data"]
                level = item["level"]
                
                desc = self._safe_text(eff.get("effect"), "未命名效果")
                score = self._safe_int(eff.get("tcs_score"), default=0)
                dependent_on = self._normalize_dependent_on_list(eff.get("dependent_on"))

                # 分数样式与 Block 映射（加回彩色原点，增强可读性）
                if score >= 5:
                    score_html = f"<span style='color: #c7254e;'>🔴 {score}</span>"
                    abc = "Block B<br>(核心)"
                elif score == 4:
                    score_html = f"<span style='color: #d35400;'>🟠 {score}</span>"
                    abc = "Block C<br>(必要)"
                elif score == 3:
                    score_html = f"<span style='color: #8a6d3b;'>🟡 {score}</span>"
                    abc = "Block C<br>(可选)"
                else:
                    score_html = f"<span style='color: #6c757d;'>⚪ {score}</span>"
                    abc = "Block A<br>(背景)"

                # 层级视觉呈现
                if level == 1:
                    desc_styled = (
                        f"<div style='color: #495057;'>"
                        f"<span style='background-color: #e9ecef; color: #495057; font-size: 11px; padding: 2px 6px; border-radius: 3px; margin-right: 6px;'>协同效果</span>"
                        f"<span style='line-height: 1.5;'>{desc}</span></div>"
                    )
                    row_bg = "background-color: #fafbfc;"
                else:
                    desc_styled = f"<div style='font-weight: bold;'>{desc}</div>"
                    row_bg = ""

                # 贡献特征处理 (带上序号)
                contributors = eff.get("contributing_features",[])
                if isinstance(contributors, list) and contributors:
                    formatted_items =[]
                    for c in contributors:
                        c_clean = self._safe_text(c).strip()
                        if not c_clean: continue
                        feat_idx = feature_name_map.get(c_clean)
                        if feat_idx:
                            formatted_items.append(f"{c_clean} [{feat_idx}]")
                        else:
                            formatted_items.append(c_clean)
                    list_items = "".join([f"<li>{x}</li>" for x in formatted_items])
                    contrib_html = f"<ul style='margin: 0; padding-left: 16px;'>{list_items}</ul>"
                    
                    # 针对未被完美挂载但自带依附信息的节点，补充一个标签
                    if dependent_on and level == 0 and score in (3, 4):
                        dep_text = ", ".join(dependent_on)
                        contrib_html += f"<div style='margin-top: 4px; font-size: 0.85em; color: #6c757d; border-top: 1px dashed #dee2e6; padding-top: 2px;'>依附: {dep_text}</div>"
                else:
                    contrib_html = "-"

                # 机理与证据
                rationale_raw = self._safe_text(eff.get("rationale"))
                rationale = self._md_bold_to_html(rationale_raw)
                raw_evidence = self._safe_text(eff.get("evidence"))
                evidence_text = self._md_bold_to_html(raw_evidence)

                if "仅声称" in raw_evidence or "无实施例" in raw_evidence:
                    evidence_styled = f"<i style='color: #dc3545;'>⚠️ {evidence_text}</i>"
                else:
                    evidence_styled = evidence_text

                # 行渲染
                table_html += f"""<tr style="{row_bg}">
<td rowspan="2" style="text-align: center; font-weight: bold; background-color: #f8f9fa;">{effect_idx}</td>
<td>{desc_styled}</td>
<td style="text-align: center;">{score_html}</td>
<td>{contrib_html}</td>
<td style="text-align: center; font-size: 0.9em; color: #495057;">{abc}</td>
</tr>
<tr style="{row_bg}">
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
                lines.append(
                    f"\n**【智能解说】**\n\n{self._indent_paragraph_text(explanation)}\n"
                )

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

        semantic_name = self._safe_text(semantic.get("name"), "语义检索")
        if semantic_queries:
            lines.append("## 2. 按核心效果分块检索方案")
            # 将指南提取到循环外，全局只渲染一次
            lines.append(self._get_search_matrix_guide())
            lines.append("")

            for idx, query_item in enumerate(semantic_queries, start=1):
                query_cluster_ids = self._extract_effect_cluster_ids(query_item)
                effect_cluster_id = query_cluster_ids[0] if query_cluster_ids else f"E{idx}"
                effect_text = self._safe_text(query_item.get("effect"), f"核心效果{idx}")
                content = self._safe_text(query_item.get("content"))

                lines.append(f"### 核心效果{idx}：{effect_text}")
                lines.append("#### 语义检索")
                lines.append(
                    f"```text\n{self._indent_semantic_code_block_text(content)}\n```\n"
                )
                lines.append("#### 检索要素表")
                filtered_matrix = self._filter_matrix_by_effect_cluster(
                    matrix, effect_cluster_id=effect_cluster_id
                )
                lines.extend(self._render_matrix_table(filtered_matrix))
                lines.append("")
        else:
            # 回退模式：保留全局检索要素和语义展示，避免报告空白
            lines.append("## 2. 检索要素表")
            lines.append(self._get_search_matrix_guide())
            lines.append("")
            lines.extend(self._render_matrix_table(matrix))
            lines.append(f"## 3. {semantic_name}\n")
            legacy_content = self._safe_text(semantic.get("content"))
            if legacy_content:
                lines.append(
                    f"```text\n{self._indent_semantic_code_block_text(legacy_content)}\n```\n"
                )
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

            is_common = block_id == "A" or (block_id in {"C", "E"} and not cluster_ids)
            belongs_to_current = target in cluster_ids
            if is_common or belongs_to_current:
                filtered.append(item)
        return filtered

    def _get_search_matrix_guide(self) -> str:
        """优化版：极简指南，去除非必要文字和 Emoji，将 Hub 中文化为枢纽"""
        guide = """<div style="background-color: #f8f9fc; border-left: 3px solid #4e73df; padding: 10px 14px; margin-bottom: 12px; font-size: 13px; color: #333; line-height: 1.5;">
<b style="color: #4e73df; font-size: 14px;">布尔检索策略配置指南</b>
<div style="margin-top: 6px;">
<b>1. 基础组配：</b> 优先使用 <code>(Block A) AND (Block B_i)</code>。若结果过多，追加 <code>AND (Block C/E)</code> 降噪。<br>
<b>2. 优先级说明：</b>
   <span style="color:#c7254e; background:#f9f2f4; padding:1px 4px; border-radius:2px;">核心特征</span>(关键突破,不可删) &nbsp;
   <span style="color:#8a6d3b; background:#fcf8e3; padding:1px 4px; border-radius:2px;">限定特征</span>(实施例限定,优先放开) &nbsp;
   <span style="color:#666; background:#f5f5f5; padding:1px 4px; border-radius:2px;">降噪/环境</span>(用于排除无关文献) &nbsp;
   <span style="color:#8e44ad; font-weight:bold;">[枢纽]</span>(跨效果复用锚点)<br>
<b>3. 范围控制：</b>
   <span style="border:1px solid #b8daff; color:#004085; padding:0 3px; border-radius:2px; font-size:11px;">全文 TX</span> (特异低频词) &nbsp;
   <span style="border:1px solid #f5c6cb; color:#721c24; padding:0 3px; border-radius:2px; font-size:11px;">限字段 TAC/CL</span> (泛化高频词，或搭配位置算符 W/3)
</div>
</div>"""
        return f"\n{guide}\n"

    def _render_matrix_table(
        self,
        matrix: Any,
    ) -> List[str]:
        lines: List[str] =[]
        if not isinstance(matrix, list) or not matrix:
            lines.append("> 未生成检索要素表。\n")
            return lines

        # 去除了所有 Emoji，文本更简练
        type_mapping = {
            "Product_Structure": "实体结构",
            "Method_Process": "方法/工艺",
            "Algorithm_Logic": "算法逻辑",
            "Material_Composition": "材料/组分",
            "Parameter_Condition": "参数/限定",
        }

        # 扁平化微底色，增加不换行属性
        priority_mapping = {
            "core": "<span style='color:#c7254e; background-color:#f9f2f4; padding:2px 4px; border-radius:3px; font-size:12px; white-space:nowrap;'>核心特征</span>",
            "assist": "<span style='color:#8a6d3b; background-color:#fcf8e3; padding:2px 4px; border-radius:3px; font-size:12px; white-space:nowrap;'>限定特征</span>",
            "filter": "<span style='color:#666; background-color:#f5f5f5; padding:2px 4px; border-radius:3px; font-size:12px; white-space:nowrap;'>降噪/环境</span>",
        }

        # 极简表头，避免长表头挤压换行
        lines.append("| 逻辑块 | 检索要素 | 中文扩展 | 英文扩展 | 分类号 (IPC/CPC) |")
        lines.append("| :--- | :--- | :--- | :--- | :--- |")

        for item in matrix:
            if not isinstance(item, dict):
                continue

            concept = self._safe_text(item.get("element_name"), "-").replace("|", "\\|")
            block_id = self._safe_text(item.get("block_id")).upper()

            # 缩减 Block 列备注信息，减小占用高度
            if block_id in ["A", "C", "E"]:
                block_display = f"<b>Block {block_id}</b>"
            else:
                display_block_id = block_id if block_id else "?"
                block_display = f"<b>Block {display_block_id}</b>"

            priority = self._safe_text(item.get("priority_tier", "assist")).lower()
            if block_id == "A":
                p_badge = (
                    "<span style='color:#31708f; background-color:#d9edf7; padding:2px 4px; "
                    "border-radius:3px; font-size:12px; white-space:nowrap;'>基准环境</span>"
                )
            else:
                p_badge = priority_mapping.get(priority, priority_mapping["assist"])
            col_block = f"{block_display}<br><div style='margin-top:4px;'>{p_badge}</div>"

            e_type_raw = self._safe_text(item.get("element_type"))
            e_type_display = type_mapping.get(e_type_raw, e_type_raw)
            is_hub = bool(item.get("is_hub_feature", False))

            # 精简 Hub 标签样式并中文化为 [枢纽]
            hub_badge = ""
            if is_hub:
                hub_badge = "&nbsp;<span title='跨效果枢纽特征' style='color:#8e44ad; font-size:12px; font-weight:bold;'>[枢纽]</span>"
            term_freq = self._safe_text(item.get("term_frequency", "")).lower()
            scope_badge = ""
            if term_freq == "low":
                scope_badge = (
                    "<span style='border:1px solid #b8daff; background:#e6f2ff; color:#004085; "
                    "padding:1px 4px; border-radius:2px; font-size:11px; white-space:nowrap;'>全文 TX</span>"
                )
            elif term_freq == "high":
                scope_badge = (
                    "<span style='border:1px solid #f5c6cb; background:#fff2f3; color:#721c24; "
                    "padding:1px 4px; border-radius:2px; font-size:11px; white-space:nowrap;'>限字段 TAC</span>"
                )

            elements_stack: List[str] = [f"<b>{concept}</b>{hub_badge}"]
            if e_type_display:
                elements_stack.append(
                    f"<div style='margin-top:6px; font-size:12px; color:#888;'>{e_type_display}</div>"
                )
            if scope_badge:
                elements_stack.append(f"<div style='margin-top:4px;'>{scope_badge}</div>")
            col_concept = f"<div style='min-width: 90px;'>{''.join(elements_stack)}</div>"

            zh_list = item.get("keywords_zh", [])
            en_list = item.get("keywords_en", [])
            ref_list = item.get("ipc_cpc_ref", [])

            zh_cleaned = [
                self._safe_text(v).replace("|", "\\|")
                for v in zh_list
                if self._safe_text(v)
            ] if isinstance(zh_list, list) else []
            en_cleaned = [
                self._safe_text(v).replace("|", "\\|")
                for v in en_list
                if self._safe_text(v)
            ] if isinstance(en_list, list) else []
            ref_cleaned = [
                self._safe_text(v).replace("|", "\\|")
                for v in ref_list
                if self._safe_text(v)
            ] if isinstance(ref_list, list) else []

            zh_str = " <small style='color:#ccc;'>OR</small> ".join(zh_cleaned) if zh_cleaned else "-"
            en_str = " <small style='color:#ccc;'>OR</small> ".join(en_cleaned) if en_cleaned else "-"
            class_str = "<br>".join(ref_cleaned) if ref_cleaned else "-"

            lines.append(
                f"| {col_block} | {col_concept} | {zh_str} | {en_str} | {class_str} |"
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
