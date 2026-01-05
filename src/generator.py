import os
import json
import markdown
from typing import Dict, List
from collections import defaultdict
from playwright.sync_api import sync_playwright
from openai import OpenAI
from config import settings
from loguru import logger

class ContentGenerator:
    def __init__(self, client: OpenAI, parts_db: Dict):
        self.client = client
        self.parts_db = parts_db

    def generate_patent_summary(self, full_md_content: str, filename_stem: str) -> Dict[str, str]:
        """
        利用 LLM 提取专利元数据和核心功能总结
        """
        # 截取前 2000 字符，确保覆盖摘要、权利要求书和部分说明书背景
        truncated_content = full_md_content[:2000]
        
        prompt = f"""
# Role
你是一位拥有 20 年经验的资深专利分析师。你擅长快速阅读晦涩的专利文档，并将其转化为高层管理者或工程师易读的摘要。

# Task
请阅读以下专利文档片段，提取元数据并总结核心功能。

# Input Text
{truncated_content}

# Constraints
1. **title**: 提取专利名称。
2. **number**: 提取专利公开号（通常类似 {filename_stem} 或 CN 开头）。如果文中未明确出现，使用 "{filename_stem}"。
3. **core_function**: 
   - 必须采用 **“背景痛点 + 解决方案 + 核心优势”** 的逻辑结构。
   - 这是一个桥梁检测装置，请重点关注它如何解决“人工检测难”或“污垢影响精度”的问题。
   - 字数控制在 80-120 字之间。
   - 语言风格：专业、精炼、流畅。

# Output Format (JSON Only)
{{
    "title": "专利名称字符串",
    "number": "专利号字符串",
    "core_function": "核心功能摘要字符串"
}}
"""

        try:
            res = self.client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1, # 低温度确保提取准确
                response_format={"type": "json_object"}
            )
            return json.loads(res.choices[0].message.content)
        except Exception as e:
            logger.error(f"Failed to generate summary: {e}")
            return {
                "title": filename_stem,
                "number": filename_stem,
                "core_function": "（自动总结失败，请手动补充）"
            }

    def cluster_images(self, image_meta: Dict[str, List[str]]) -> Dict[str, List[str]]:
        """
        将图片按父系统聚类 (逻辑保持不变，依然非常有效)
        """
        clusters = defaultdict(list)
        for img_path, part_ids in image_meta.items():
            if not part_ids:
                clusters["通用/未分类"].append(img_path)
                continue
            
            parent_counts = defaultdict(int)
            for pid in part_ids:
                item = self.parts_db.get(pid, {})
                parent = item.get('parent_system')
                if parent:
                    parent_counts[parent] += 1
                else:
                    parent_counts['root'] += 1
            
            if parent_counts:
                top_parent_id = max(parent_counts, key=parent_counts.get)
                if top_parent_id == 'root':
                    # 尝试找到一个有意义的名字
                    first_part_name = "组件"
                    for pid in part_ids:
                         name = self.parts_db.get(pid, {}).get('name')
                         if name:
                             first_part_name = name
                             break
                    group = f"整体/局部结构：{first_part_name}等"
                else:
                    p_name = self.parts_db.get(top_parent_id, {}).get('name', top_parent_id)
                    group = f"核心子系统：{p_name}"
            else:
                group = "其他结构展示"
            
            clusters[group].append(img_path)
        return dict(clusters)

    def generate_intro(self, group_name: str, part_ids: set) -> str:
        """
        调用 LLM 生成看图说话文本 (Prompt 经过大幅增强)
        """
        # 构建组件上下文，包含父子关系暗示
        items_desc = []
        for pid in part_ids:
            info = self.parts_db.get(pid, {})
            name = info.get('name', '未知部件')
            func = info.get('function', '未知功能')
            parent = info.get('parent_system', None)
            
            desc_str = f"- 编号 {pid} [{name}]: {func}"
            if parent:
                parent_name = self.parts_db.get(parent, {}).get('name', parent)
                desc_str += f" (属于: {parent_name})"
            items_desc.append(desc_str)
        
        context = "\n".join(items_desc)
        
        prompt = f"""
# Role
你是一名精通机械原理的技术解说员。现在的任务是为专利附图编写一段“看图说话”的图解说明。

# Context
这是一组关于 **“{group_name}”** 的图纸细节。
图中包含以下组件及其功能定义：
{context}

# Instructions
请编写一段 150-250 字的技术说明段落，要求：

1.  **视觉构建 (Visual Grounding)**：
    不要枯燥地罗列“编号1是XX，编号2是YY”。
    必须描述组件之间的**空间关系**和**连接方式**。
    *   例如：使用“位于...顶部”、“嵌套在...内部”、“通过...连接”、“延伸至...”等方位词。
    *   推断逻辑：如果 A 是电机，B 是杆，C 是齿轮，请描述“电机 A 驱动杆 B，进而带动齿轮 C 旋转”。

2.  **动态演绎 (Dynamic Flow)**：
    描述这个系统是如何**运作**的。
    *   不要只写静态结构，要写动作。例如：“当设备工作时，部件 A 推动部件 B...”

3.  **通俗化 (De-jargon)**：
    将专利生硬的语言（如“所述”、“设置有”）转化为自然的工程语言（如“该”、“装配了”）。

4.  **结构化写作**：
    *   第一句：直接说明该图展示了什么核心模块。
    *   中间：按机械传动或逻辑顺序描述细节。
    *   结尾：简述该模块实现了什么最终效果（如“从而彻底清除了底部污垢”）。

# Output
直接输出说明段落，不要包含标题或Markdown格式，不要包含“好的”、“这段描述如下”等废话。
"""
        
        try:
            res = self.client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4, # 稍微提高温度，增加描述的灵活性
            )
            return res.choices[0].message.content.strip()
        except Exception:
            return "（系统正在维护，暂无法生成该图片的智能解说，请参考下方组件表。）"

    def render_markdown(self, clusters, image_meta, output_md_path, output_pdf_path, summary_info: Dict):
        """生成 Markdown 文件 (保持结构，微调样式)"""
        
        title = summary_info.get("title", "未知专利")
        number = summary_info.get("number", "")
        core_func = summary_info.get("core_function", "暂无描述")
        
        # 使用引用块样式增强头部可读性
        header = f"""# {title}
        
**专利号**: {number}  
**核心功能**: {core_func}

---
"""
        lines = [header.strip(), ""]
        
        # 排序：将 "核心子系统" 排在前面，"其他" 排在后面
        def sort_key(k):
            if "核心子系统" in k: return 0
            if "整体" in k: return 1
            if "组件" in k: return 2
            return 99

        sorted_groups = sorted(clusters.keys(), key=sort_key)
        
        for group in sorted_groups:
            images = clusters[group]
            lines.append(f"## {group}")
            
            # 聚合该组所有组件ID用于生成文本
            group_pids = set()
            for img in images:
                group_pids.update(image_meta.get(img, []))
            
            if group_pids:
                desc = self.generate_intro(group, group_pids)
                lines.append(f"{desc}\n")
            
            # 逐图展示
            for img_abs_path in images:
                try:
                    rel_path = os.path.relpath(img_abs_path, output_md_path.parent)
                except ValueError:
                    rel_path = img_abs_path
                
                # 提取文件名作为图注
                img_name = os.path.basename(img_abs_path)
                lines.append(f"### 图示详情") 
                lines.append(f"![{img_name}]({rel_path})\n")
                
                img_pids = image_meta.get(img_abs_path, [])
                if img_pids:
                    img_pids.sort(key=lambda x: int(x) if x.isdigit() else 9999)
                    lines.append("| 编号 | 组件名称 | 功能/作用 |")
                    lines.append("| :---: | :--- | :--- |")
                    for pid in img_pids:
                        info = self.parts_db.get(pid, {})
                        # 处理可能缺失的字段，避免NoneType报错
                        p_name = info.get('name', '未命名')
                        p_func = info.get('function', '-')
                        lines.append(f"| **{pid}** | {p_name} | {p_func} |")
                    lines.append("\n")
            
            lines.append("---\n")

        full_md_text = "\n".join(lines)

        # --- 写入 Markdown ---    
        with open(output_md_path, "w", encoding="utf-8") as f:
            f.write(full_md_text)
        logger.success(f"Markdown generated: {output_md_path}")

        # --- 生成 PDF (WeasyPrint) ---
        self._export_pdf(full_md_text, output_md_path.parent, output_pdf_path)

    def _export_pdf(self, md_text, base_path, output_path):
        """
        方案：Playwright (Headless Browser)
        """
        try:
            logger.info("Converting to PDF using Playwright...")
            
            # 1. MD -> HTML
            html_body = markdown.markdown(md_text, extensions=['tables', 'fenced_code'])
            
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
            
            # 3. 保存临时 HTML 文件 (浏览器需要加载本地文件)
            temp_html_path = base_path / "temp_report.html"
            with open(temp_html_path, "w", encoding="utf-8") as f:
                f.write(full_html)
            
            # 4. 调用浏览器生成 PDF
            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                # 加载本地 HTML (使用 file:// 协议)
                page.goto(f"file://{temp_html_path.absolute()}")
                
                # 生成 PDF
                page.pdf(path=str(output_path), format="A4", margin={"top": "2cm", "bottom": "2cm", "left": "2cm", "right": "2cm"})
                browser.close()
            
            # 清理临时文件
            if temp_html_path.exists():
                os.remove(temp_html_path)
                
            logger.success(f"PDF generated: {output_path}")

        except Exception as e:
            logger.error(f"PDF generation failed: {e}")

