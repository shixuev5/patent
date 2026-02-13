import os
import re
import markdown
from pathlib import Path
from typing import Dict, Any, Optional
from loguru import logger
from playwright.sync_api import sync_playwright

from config import settings


class ReportRenderer:
    def __init__(self, patent_data: Dict[str, Any]):
        self.patent_data = patent_data

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

    def render(
        self,
        report_data: Dict[str, Any],
        check_result: Optional[Dict[str, str]],
        search_data: Optional[Dict[str, Any]],
        md_path: Path,
        pdf_path: Path,
    ):
        """
        主入口：组装分析报告和检索策略，生成 MD 和 PDF
        """
        logger.info("Starting rendering process...")

        parts = []

        # 1. 渲染分析报告部分
        if report_data:
            parts.append(self._render_analysis_section(report_data))

        # 2. 形式缺陷审查报告
        if check_result:
            # 强制分页
            parts.append("\n<div style='page-break-before: always;'></div>\n")
            parts.append(self._render_formal_check_section(check_result))

        # 3. 渲染检索策略部分
        if search_data:
            # 添加分页符，确保检索策略从新页面开始
            parts.append("\n<div style='page-break-before: always;'></div>\n")
            parts.append(self._render_search_section(search_data))

        full_md_content = "\n".join(parts)

        # 4. 写入 .md 文件
        try:
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(full_md_content, encoding="utf-8")
            logger.success(f"Markdown report generated: {md_path}")
        except Exception as e:
            logger.error(f"Failed to write markdown: {e}")
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

        # --- 1. AI 标题 ---
        title = data.get("ai_title", "未命名专利分析报告")
        lines.append(f"# {title}\n")

        # --- 2. 摘要 ---
        lines.append("## 摘要")
        abstract = data.get("ai_abstract", "暂无摘要")
        lines.append(f"{self._indent_text(abstract)}\n")

        # --- 3. 主图 ---
        # 检查主图是否存在
        main_fig = data.get("abstract_figure")
        if main_fig:
            lines.append(f"![Main Figure]({main_fig})\n")

        # --- 4. 技术领域 ---
        lines.append("## 1. 技术领域")
        domain = data.get("technical_field", "未提取到技术领域")
        lines.append(f"{self._indent_text(domain)}\n")

        # --- 5. 技术问题 ---
        lines.append("## 2. 现有技术问题")
        problem = data.get("technical_problem", "未提取到技术问题")
        lines.append(f"{self._indent_text(problem)}\n")

        # --- 6. 背景知识百科 ---
        bg_knowledge = data.get("background_knowledge", [])
        if bg_knowledge:
            lines.append("## 3. 核心概念百科")
            lines.append("> 💡 *阅读提示：以下是本案涉及的关键术语解释，旨在辅助非本领域人员理解技术方案。*\n")
            
            for item in bg_knowledge:
                term = item.get("term", "未命名术语")
                definition = item.get("definition", "")
                analogy = item.get("analogy", "")
                context = item.get("context_in_patent", "")
                
                card_html = f"""
<div style="border: 1px solid #dfe2e5; margin-bottom: 20px; page-break-inside: avoid; background-color: #fff;">
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
</div>
"""
                lines.append(card_html)
            lines.append("\n")

        # --- 7 技术方案 ---
        lines.append("## 4. 技术方案概要")

        # 优先展示保护主题，作为方案的定性描述
        subject_matter = data.get("claim_subject_matter")
        if subject_matter:
            # 使用引用块或加粗形式，使其在视觉上区别于正文
            lines.append(f"> **🛡️ 保护主题**：{subject_matter}\n")

        scheme = data.get("technical_scheme", "未提取到技术方案")

        # 只在技术方案是一整段话时才进行缩进
        if "\n" not in scheme:
            scheme = self._indent_text(scheme)

        lines.append(f"{scheme}\n")

        # --- 8. 技术手段 (Technical Means) ---
        lines.append("## 5. 核心技术手段")
        means = data.get("technical_means", "未提取到技术手段")
        lines.append(f"{self._indent_text(means)}\n")

        # 8.1 技术特征列表
        features = data.get("technical_features", [])
        
        feature_name_map = {}
        
        if features:
            lines.append("### 关键技术特征表")

            # HTML 表格头
            table_html = """
<table>
    <thead>
        <tr>
            <th style="width: 28px; text-align: center;">序号</th>
            <th style="width: 20%;">特征名称</th>
            <th style="width: 70px; text-align: center;">属性</th>
            <th>详细定义</th>
        </tr>
    </thead>
    <tbody>
            """
            for idx, feat in enumerate(features, 1):
                name = feat.get("name", "-")
                
                # 存入映射表，方便技术效果中的贡献特征映射
                feature_name_map[name.strip()] = idx
                
                desc = self._md_bold_to_html(feat.get("description", "").replace("\n", "<br>")) 
                rationale = self._md_bold_to_html(feat.get("rationale", "").replace("\n", "<br>")) 

                is_distinguishing = feat.get("is_distinguishing", False)
                source = str(feat.get("claim_source", "")).lower()

                if is_distinguishing:
                    badge_text = "🌟 区别特征"
                elif "independent" in source:
                    badge_text = "⚪ 前序特征"
                else:
                    # 只要不是区别特征，且来源不是 independent，即为从权特征
                    badge_text = "🔹 从权特征"

                # Row 1: 序号使用 rowspan="2" 消除留白
                table_html += f"""
        <tr>
            <td rowspan="2" style="text-align: center; font-weight: bold; background-color: #f8f9fa;">{idx}</td>
            <td style="font-weight: bold;">{name}</td>
            <td style="text-align: center;">{badge_text}</td>
            <td>{desc}</td>
        </tr>
         <tr>
            <td colspan="3">{rationale}</td>
        </tr>
                """

            table_html += "</tbody></table>\n"
            lines.append(table_html)

        # --- 9. 技术效果 (Technical Effects) ---
        lines.append("## 6. 技术效果")
        effects = data.get("technical_effects", [])
        
        if effects:
            table_html = """
<table>
    <thead>
        <tr>
            <th style="width: 28px; text-align: center;">序号</th>
            <th>技术效果</th>
            <th style="width: 60px; text-align: center;">TCS 评分</th>
            <th style="width: 40%;">贡献特征</th>
            <th style="width: 40px; text-align: center;">检索分块</th>
        </tr>
    </thead>
    <tbody>
            """

            for idx, eff in enumerate(effects, 1):
                desc = eff.get("effect", "未命名效果")
                score = eff.get("tcs_score", 0)

                # 评分样式
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

                # 贡献特征：处理为无序列表 <ul>
                contributors = eff.get("contributing_features", [])
                if isinstance(contributors, list) and contributors:
                    formatted_items = []
                    for c in contributors:
                        c_clean = str(c).strip()
                        # 【修改点 1】尝试查找序号并追加
                        feat_idx = feature_name_map.get(c_clean)
                        if feat_idx:
                            formatted_items.append(f"{c_clean} [{feat_idx}]")
                        else:
                            formatted_items.append(c_clean)
                            
                    # 使用 <ul><li> 结构，避免数字混淆
                    list_items = "".join([f"<li>{item}</li>" for item in formatted_items])
                    contrib_html = f"<ul style='margin: 0;'>{list_items}</ul>"
                else:
                    contrib_html = str(contributors) if contributors else "-"

                 # 使用正则替换 Markdown 加粗语法，因为 HTML 表格内 MD 不会自动解析
                rationale = self._md_bold_to_html(eff.get("rationale", ""))
                
                raw_evidence = eff.get("evidence", "")
                # 对证据也进行加粗转换，防止证据里有强调语法失效
                evidence_text = self._md_bold_to_html(raw_evidence)

                if "仅声称" in raw_evidence or "无实施例" in raw_evidence:
                    evidence_styled = f"<i style='color: #dc3545;'>⚠️ {evidence_text}</i>"
                else:
                    evidence_styled = evidence_text

                # Row 1: 效果 | 评分 | 贡献特征(列表) | 检索分级
                table_html += f"""
        <tr>
            <td rowspan="2" style="text-align: center; font-weight: bold; background-color: #f8f9fa;">{idx}</td>
            <td style="font-weight: bold;">{desc}</td>
            <td style="text-align: center;">{score_html}</td>
            <td>{contrib_html}</td>
            <td style="text-align: center;">{abc}</td>
        </tr>
                """

                # Row 2: 详情行 (机理 -> 证据)
                table_html += f"""
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
        </tr>
                """

            table_html += "</tbody></table>\n"
            lines.append(table_html)
        else:
            lines.append("> *未提取到明确的技术效果或评分数据。*\n")

        # --- 10. 图解说明 (Figure Explanations) ---
        lines.append("## 7. 图解说明")
        figures = data.get("figure_explanations", [])

        if not figures:
            lines.append("暂无图片分析。\n")

        for fig in figures:
            img_path = fig.get("image_path")
            img_title = fig.get("image_title", "图片")
            explanation = fig.get("image_explanation", "")
            parts = fig.get("parts_info", [])

            if img_path:
                figure_html = f"""
<figure>
    <img src="{img_path}" alt="{img_title}">
    <figcaption>{img_title}</figcaption>
</figure>
"""
                lines.append(figure_html)

            if explanation:
                lines.append(
                    f"\n**【智能解说】**\n\n{self._indent_text(explanation)}\n"
                )

            if parts:
                lines.append("\n**【可见部件清单】**\n")
                lines.append("| 标号 | 名称 | 功能/作用 |")
                lines.append("| :---: | :--- | :--- |")
                for p in parts:
                    pid = p.get("id", "-")
                    pname = p.get("name", "-")
                    pfunc = p.get("function", "-")
                    lines.append(f"| {pid} | {pname} | {pfunc} |")
                lines.append("\n")

            lines.append("\n---\n")  # 分隔线

        return "\n".join(lines)

    def _render_formal_check_section(self, check_results: Dict[str, str]) -> str:
        """
        渲染独立章节：形式缺陷检查报告
        """
        lines = []
        lines.append("# 形式缺陷审查报告\n")
        
        # 1. 附图标记一致性检查
        consistency_text = check_results.get("consistency")
        if consistency_text:
            lines.append("## 1. 附图标记一致性检查")
            lines.append(f"{consistency_text}\n")
        
        # 如果没有任何检查结果
        if not lines:
            lines.append("本次未进行形式缺陷检查。")

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
        title = biblio.get("invention_title", "未知标题")
        app_date = biblio.get("application_date", "未知")
        prio_date = biblio.get("priority_date")  # 获取优先权日

        # 确定检索截止日
        if prio_date:
            critical_date = prio_date
            prio_display = f"{prio_date}"
            note_desc = (
                f"鉴于本案主张了优先权，**现有技术的时间界限应前移至 {prio_date}**。"
            )
        else:
            critical_date = app_date
            prio_display = "无"
            note_desc = f"本案未主张优先权，**现有技术的时间界限为 {app_date}**。"

        lines.append("## 1. 检索基础信息")
        lines.append(f"- **发明名称**: {title}")
        lines.append(f"- **申请日**: {app_date}")
        lines.append(f"- **优先权日**: {prio_display}")

        # 动态提示块
        lines.append(f"> **📅 检索截止日: {critical_date}**")
        lines.append(
            f"> *注：{note_desc} 所有在此日期之前公开的文献均构成现有技术，可用于评价新颖性与创造性。*\n"
        )

        # 获取数据源
        matrix = data.get("search_matrix", [])
        plan = data.get("search_plan", {})

        # --- 2. 检索要素表 (包含分类号) ---
        lines.append("## 2. 检索要素与分类号映射表")
        lines.append("基于技术方案拆解的核心概念、多语言扩展词表及关联分类号：\n")

        if matrix:
            # 定义分块映射逻辑 (Emoji + 换行符优化显示)
            role_mapping = {
                "Subject": "Block A<br>(检索主语)",
                "KeyFeature": "Block B<br>(核心特征)",
                "Functional": "Block C<br>(功能/限定)"
            }
            
            # Markdown 表格构建：增加分类号列
            # 使用 HTML 换行符 <br> 在单元格内区分 IPC 和 CPC，或区分太长的词
            lines.append("| 检索分块 | 核心概念 | 中文扩展 | 英文扩展 | 分类号 (IPC/CPC) |")
            lines.append("| :--- | :--- | :--- | :--- | :--- |")

            for item in matrix:
                concept = item.get("concept_key", "-").replace("|", "\|")
                role_key = item.get("role", "Other")
                
                # 获取分块显示文本，如果未定义则显示原值
                block_display = role_mapping.get(role_key, f"Block ?<br>({role_key})")

                # 处理列表转字符串
                zh_list = item.get("zh_expand", [])
                en_list = item.get("en_expand", [])
                ref_list = item.get("ipc_cpc_ref", [])

                zh_str = ", ".join(zh_list) if zh_list else "-"
                en_str = ", ".join(en_list) if en_list else "-"
                class_str = "<br>".join(ref_list) if ref_list else "-"

                # 组装表格行
                lines.append(f"| **{block_display}** | **{concept}** | {zh_str} | {en_str} | {class_str} |")
            lines.append("\n")
        else:
            lines.append("> 未生成检索要素表。\n")

        # --- 3. 分步检索策略 (Strategies) ---
        lines.append("## 3. 分步检索策略构建")
        strategies = plan.get("strategies", [])

        if not strategies:
            lines.append("未生成具体的检索策略步骤。\n")

        for idx, strategy in enumerate(strategies, 1):
            s_name = strategy.get("name", f"策略 {idx}")
            s_desc = strategy.get("description", "暂无描述")
            queries = strategy.get("queries", [])

            lines.append(f"### Step {idx}: {s_name}")
            lines.append(f"> **策略逻辑**: {s_desc}\n")

            if queries:
                for q_item in queries:
                    db_name = q_item.get("db", "General")
                    step_info = q_item.get("step", "")  # 获取具体步骤描述
                    query_str = q_item.get("query", "").strip()

                    if db_name == 'Patsnap' or not query_str:
                        continue

                    # 标题格式：[数据库] 步骤描述
                    header_text = f"**[{db_name}]**"
                    if step_info:
                        header_text += f" - *{step_info}*"

                    lines.append(f"{header_text}")
                    # 使用 text 格式代码块，避免 markdown 对逻辑算符的错误高亮
                    lines.append(f"```text\n{query_str}\n```\n")
            else:
                lines.append("*本步骤无需特定检索式 (如人工浏览或语义输入)*\n")

            lines.append("---\n")  # 分隔线

        return "\n".join(lines)

    def _export_pdf(self, md_text: str, output_path: Path):
        """
        使用 Playwright 将 Markdown (转HTML后) 打印为 PDF
        """
        logger.info("Starting PDF generation...")

        # 1. Markdown -> HTML
        html_body = markdown.markdown(
            md_text,
            extensions=["tables", "fenced_code", "nl2br", "sane_lists", "extra"],
        )

        # 2. 构建完整 HTML
        full_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Patent Analysis Report</title>
            <!-- MathJax 配置: 自动识别 $...$ 和 \(...\) -->
            <script>
            window.MathJax = {{
              tex: {{
                inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
                displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']]
              }},
              svg: {{
                fontCache: 'global'
              }},
              startup: {{
                // 确保 MathJax 初始化后我们能捕获状态
                pageReady: () => {{
                  return MathJax.startup.defaultPageReady();
                }}
              }}
            }};
            </script>
            <script id="MathJax-script" async src="https://unpkg.com/mathjax@3.2.2/es5/tex-mml-chtml.js"></script>
            <style>
                {settings.PDF_CSS}
            </style>
        </head>
        <body>
            {html_body}
        </body>
        </html>
        """

        # 4. 保存临时 HTML (方便 Playwright 读取本地资源)
        temp_html_path = output_path.parent / "temp_render.html"
        try:
            temp_html_path.write_text(full_html, encoding="utf-8")

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()

                # 加载本地 HTML
                page.goto(
                    f"file://{temp_html_path.absolute()}", wait_until="networkidle"
                )

                # 智能等待 MathJax 渲染完成
                try:
                    # 步骤 A: 确保 MathJax 对象已挂载 (脚本已下载执行)
                    # 设置较短超时，如果因为网络问题没加载 MathJax，就不死等了
                    page.wait_for_function("() => window.MathJax", timeout=10000)

                    # 步骤 B: 等待 startup promise 解析
                    # page.evaluate 会自动等待 JS 函数返回的 Promise 完成
                    page.evaluate(
                        """
                        async () => {
                            if (window.MathJax && window.MathJax.startup) {
                                await window.MathJax.startup.promise;
                            }
                        }
                    """
                    )
                    logger.info("MathJax rendering promise resolved.")

                except Exception as e:
                    # 如果等待超时（可能是没有公式导致 MathJax 懒加载，或者网络问题），记录日志但继续生成
                    logger.warning(
                        f"MathJax wait skipped or failed (safe to ignore if no math): {e}"
                    )

                # 生成 PDF
                page.pdf(
                    path=str(output_path),
                    format="A4",
                    print_background=True,
                    margin={
                        "top": "2cm",
                        "bottom": "2cm",
                        "left": "1.5cm",
                        "right": "1.5cm",
                    },
                    display_header_footer=True,
                    footer_template='<div style="font-size: 10px; text-align: center; width: 100%;">Page <span class="pageNumber"></span> of <span class="totalPages"></span></div>',
                    header_template="<div></div>",
                )
                browser.close()

            logger.success(f"PDF successfully generated: {output_path}")

        except Exception as e:
            logger.error(f"PDF generation failed: {e}")
            raise
        finally:
            # 清理临时文件
            if temp_html_path.exists():
                os.remove(temp_html_path)
