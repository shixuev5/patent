import os
import markdown
from pathlib import Path
from typing import Dict, Any, Optional
from loguru import logger
from playwright.sync_api import sync_playwright

from config import settings

class ReportRenderer:
    def __init__(self):
        pass

    def render(self, report_data: Dict[str, Any], search_data: Optional[Dict[str, Any]], md_path: Path, pdf_path: Path):
        """
        主入口：组装分析报告和检索策略，生成 MD 和 PDF
        """
        logger.info("Starting rendering process...")

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
        
        # 3. 写入 .md 文件
        try:
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(full_md_content, encoding="utf-8")
            logger.success(f"Markdown report generated: {md_path}")
        except Exception as e:
            logger.error(f"Failed to write markdown: {e}")
            return

        # 4. 导出 .pdf 文件
        self._export_pdf(full_md_content, pdf_path)


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
        lines.append(f"{abstract}\n")

        # --- 3. 主图 ---
        # 检查主图是否存在
        main_fig = data.get("abstract_figure")
        if main_fig:
            lines.append(f"![Main Figure]({main_fig})\n")

        # --- 4. 技术问题 ---
        lines.append("## 1. 现有技术问题")
        problem = data.get("technical_problem", "未提取到技术问题")
        lines.append(f"{problem}\n")

        # --- 4.5 技术方案 ---
        lines.append("## 2. 技术方案概要")
        scheme = data.get("technical_scheme", "未提取到技术方案")
        lines.append(f"{scheme}\n")

        # --- 5. 技术手段 (Technical Means) ---
        lines.append("## 3. 核心技术手段")
        means = data.get("technical_means", "未提取到技术手段")
        lines.append(f"{means}\n")

        # 5.1 技术特征列表
        features = data.get("technical_features", [])
        if features:
            features.sort(key=lambda x: x.get("is_essential", False), reverse=True)

            lines.append("### 关键技术特征")
            # Markdown 表格头
            lines.append("| 特征名称 | 详细描述 | 核心特征 |")
            lines.append("| :--- | :--- | :---: |")
            for feat in features:
                name = feat.get("name", "-")
                desc = feat.get("description", "-").replace("\n", " ") # 表格内不能换行
                is_essential = "✅" if feat.get("is_essential") else ""
                lines.append(f"| {name} | {desc} | {is_essential} |")
            lines.append("\n")

        # --- 6. 技术效果 (Technical Effects) ---
        lines.append("## 4. 技术效果")
        effects = data.get("technical_effects", [])
        if effects:
            for idx, eff in enumerate(effects, 1):
                desc = eff.get("effect", "")
                src = eff.get("source_feature", "")
                evidence = eff.get("evidence", "") # 获取证据字段

                lines.append(f"**{idx}. {desc}**")
                
                # 使用引用列表格式展示归因和证据
                if src or evidence:
                    if src:
                        lines.append(f"> - **归因特征**: {src}")
                    if evidence:
                        # 简单的颜色标记或加粗，让证据更显眼
                        lines.append(f"> - **验证证据**: {evidence}")
                    lines.append("") # 增加空行，保证 Markdown 渲染间距
        else:
            lines.append("未提取到具体效果描述。\n")

        # --- 7. 图解说明 (Figure Explanations) ---
        lines.append("## 5. 图解说明")
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
                lines.append(f"\n**【AI 解说】**\n\n{explanation}\n")

            if parts:
                lines.append("\n**【部件清单】**\n")
                lines.append("| 标号 | 名称 | 功能/作用 |")
                lines.append("| :---: | :--- | :--- |")
                for p in parts:
                    pid = p.get("id", "-")
                    pname = p.get("name", "-")
                    pfunc = p.get("function", "-")
                    lines.append(f"| {pid} | {pname} | {pfunc} |")
                lines.append("\n")

            lines.append("\n---\n") # 分隔线

        return "\n".join(lines)


    def _render_search_section(self, data: Dict[str, Any]) -> str:
        """
        渲染第二部分：检索策略
        """
        lines = []
        lines.append("# 专利审查检索策略建议书\n")

        # 获取数据源
        matrix = data.get("search_matrix", [])
        plan = data.get("search_plan", {})
        
        # --- 1. 检索要素表 (包含分类号) ---
        lines.append("## 1. 检索要素与分类号映射表")
        lines.append("基于技术方案拆解的核心概念、多语言扩展词表及关联分类号：\n")
        
        if matrix:
            # Markdown 表格构建：增加分类号列
            # 使用 HTML 换行符 <br> 在单元格内区分 IPC 和 CPC，或区分太长的词
            lines.append("| 核心概念 (Key Concept) | 中文扩展 (CNTXT) | 英文扩展 (VEN) | 分类号 (IPC/CPC) |")
            lines.append("| :--- | :--- | :--- | :--- |")
            
            for item in matrix:
                concept = item.get("concept_key", "-").replace("|", "\|")
                
                # 处理列表转字符串
                zh_list = item.get("zh_expand", [])
                en_list = item.get("en_expand", [])
                ipc_list = item.get("ipc", [])
                cpc_list = item.get("cpc", [])

                zh_str = ", ".join(zh_list) if zh_list else "-"
                en_str = ", ".join(en_list) if en_list else "-"
                
                # 构建分类号单元格，使用 HTML 换行使 IPC 和 CPC 分行显示
                class_parts = []
                if ipc_list:
                    class_parts.append(f"**IPC**: {', '.join(ipc_list)}")
                if cpc_list:
                    class_parts.append(f"**CPC**: {', '.join(cpc_list)}")
                
                class_str = "<br>".join(class_parts) if class_parts else "-"

                # 组装表格行
                lines.append(f"| **{concept}** | {zh_str} | {en_str} | {class_str} |")
            lines.append("\n")
        else:
            lines.append("> 未生成检索要素表。\n")

        # --- 2. 分步检索策略 (Strategies) ---
        lines.append("## 2. 分步检索策略构建")
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
                    query_str = q_item.get("query", "").strip()
                    
                    if not query_str:
                        continue

                    # 为每个数据库生成独立的代码块
                    # 使用 text/plain 避免 markdown 错误的高亮逻辑算符
                    lines.append(f"**[{db_name}]** 检索式参考:")
                    lines.append(f"```text\n{query_str}\n```\n")
            else:
                lines.append("*本步骤无需特定检索式 (如人工浏览或语义输入)*\n")
            
            lines.append("---\n") # 分隔线

        return "\n".join(lines)
    

    def _export_pdf(self, md_text: str, output_path: Path):
        """
        使用 Playwright 将 Markdown (转HTML后) 打印为 PDF
        """
        logger.info("Starting PDF generation...")
        
        # 1. Markdown -> HTML
        html_body = markdown.markdown(
            md_text, 
            extensions=['tables', 'fenced_code', 'nl2br', 'sane_lists']
        )
        
        # 2. 构建完整 HTML
        full_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
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
                page.goto(f"file://{temp_html_path.absolute()}", wait_until="networkidle")
                
                # 生成 PDF
                page.pdf(
                    path=str(output_path),
                    format="A4",
                    print_background=True,
                    margin={"top": "2cm", "bottom": "2cm", "left": "1.5cm", "right": "1.5cm"},
                    display_header_footer=True,
                    footer_template='<div style="font-size: 10px; text-align: center; width: 100%;">Page <span class="pageNumber"></span> of <span class="totalPages"></span></div>',
                    header_template='<div></div>'
                )
                browser.close()
            
            logger.success(f"PDF successfully generated: {output_path}")
            
        except Exception as e:
            logger.error(f"PDF generation failed: {e}")
        finally:
            # 清理临时文件
            if temp_html_path.exists():
                os.remove(temp_html_path)