import os
import json
import markdown
from typing import Dict
from playwright.sync_api import sync_playwright
from openai import OpenAI
from config import settings
from loguru import logger

class ContentGenerator:
    def __init__(self, client: OpenAI, patent_data: Dict, parts_db: Dict):
        self.client = client
        self.patent_data = patent_data
        self.parts_db = parts_db

        # 预处理数据，方便后续调用
        self.biblio = patent_data.get("bibliographic_data", {})
        self.claims = patent_data.get("claims", [])
        self.description = patent_data.get("description", {})
        
        # 提取关键文本块 (为了节省 Token 并提高准确度，提前做简单的清洗)
        self.text_background = self.description.get("background_art", "")
        self.text_summary = self.description.get("summary_of_invention", "")
        self.text_details = self.description.get("detailed_description", "")

        # 提取独立权利要求
        self.independent_claims = [
            c["claim_text"] for c in self.claims if c.get("claim_type") == "independent"
        ]
    
    def generate_analysis(self) -> Dict[str, Any]:
        """
        执行全流程分析，返回包含5要素的结构化字典。
        """
        logger.info(f"开始分析专利: {self.biblio.get('application_number', 'Unknown')}")

        try:
            # Step 1: 宏观逻辑分析 (问题 & 方案 & 预期功效)
            macro_result = self._analyze_macro_logic()
            
            # Step 2: 微观细节填充 (特征 & 手段 & 验证功效)
            # 将 Step 1 的结果传给 Step 2 作为 Context
            final_result = self._analyze_micro_details(macro_result)
            
            logger.info("专利分析完成")
            return final_result

        except Exception as e:
            logger.error(f"分析过程中发生错误: {str(e)}")
            return {
                "technical_problem": "Analysis Failed",
                "technical_scheme": "Analysis Failed",
                "technical_features": [],
                "technical_means": "Analysis Failed",
                "technical_effects": []
            }
        
    def _analyze_macro_logic(self) -> Dict[str, Any]:
        """
        阶段一：分析技术问题、技术方案概括。
        输入：背景技术 + 发明内容(部分) + 独权
        """
        logger.debug("正在执行阶段一：宏观逻辑分析...")

        system_prompt = (
            "你是一位资深专利审查员。请基于提供的专利片段，忽略具体实施细节，"
            "仅从宏观逻辑上分析该发明的核心逻辑。请以严格的 JSON 格式输出。"
        )

        user_content = f"""
        【背景技术】
        {self.text_background[:2000]} 

        【发明内容摘要】
        {self.text_summary[:2000]}

        【独立权利要求】
        {json.dumps(self.independent_claims, ensure_ascii=False)}

        请分析并提取：
        1. technical_problem: 现有技术存在的客观缺陷或痛点（详细描述）。
        2. technical_scheme_summary: 用通俗的技术语言概括本发明的核心解决方案（不要直接照抄权利要求）。
        3. intended_effect: 发明声称要达到的主要宏观效果。

        输出格式示例：
        {{
            "technical_problem": "...",
            "technical_scheme_summary": "...",
            "intended_effect": "..."
        }}
        """

        return self._call_llm(system_prompt, user_content)
    

    def _analyze_micro_details(self, macro_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        阶段二：分析技术特征、技术手段、具体功效。
        输入：阶段一结果 + 全部权利要求 + 具体实施方式(截断)
        """
        logger.debug("正在执行阶段二：微观细节提取...")

        # 截断详细描述以防止Token溢出 (保留前 5000 字符通常包含了核心原理)
        # 对于更复杂的场景，建议使用 Embedding 检索相关段落
        details_snippet = self.text_details[:6000]

        system_prompt = (
            "你是一位技术专家。基于已知的核心方案，深入挖掘专利的技术细节。"
            "重点解释技术原理（手段）和验证数据。请以严格的 JSON 格式输出。"
        )

        user_content = f"""
        已确定的【核心逻辑框架】：
        {json.dumps(macro_context, ensure_ascii=False)}

        请结合以下【具体实施方式】和【权利要求】，完善分析：
        
        【权利要求书】
        {json.dumps(self.claims, ensure_ascii=False)}

        【具体实施方式片段】
        {details_snippet}

        任务：
        1. technical_features: 将技术方案拆解为关键的技术特征列表（包括结构组件、算法步骤、参数限定）。
           - 必须包含特征名称 (name) 和具体描述 (description)。
           - 标记是否为核心必要特征 (is_essential)。
        2. technical_means: 深度解析【技术手段】。
           - 解释核心特征是如何利用自然规律解决【technical_problem】的？
           - 重点寻找“原理”、“通过...实现”、“机制”等描述。
           - 如果涉及算法（如公式），请简要说明其物理意义。
        3. technical_effects: 提取具体的【技术功效】。
           - 寻找实施方式中的实验数据、对比结果或定性描述。
           - 尝试将功效与对应的特征关联起来。

        最终输出 JSON 结构：
        {{
            "technical_problem": "继承自上一步，可微调",
            "technical_scheme": "继承自上一步，可微调",
            "technical_features": [
                {{"name": "...", "description": "...", "is_essential": true}},
                ...
            ],
            "technical_means": "...",
            "technical_effects": [
                {{"effect": "...", "source_feature": "..."}},
                ...
            ]
        }}
        """

        return self._call_llm(system_prompt, user_content)
    
    def _call_llm(self, system_prompt: str, user_content: str) -> Dict[str, Any]:
        """
        封装 LLM 调用，包含重试逻辑和 JSON 解析清洗。
        """
        try:
            response = self.client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                temperature=0.1,
                response_format={"type": "json_object"}  # 强制 JSON 模式
            )
            content = response.choices[0].message.content
            return json.loads(content)
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            raise e

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

    def render_markdown(self, image_meta, output_md_path, output_pdf_path):
        """生成 Markdown 文件 (保持结构，微调样式)"""
        
        # 使用引用块样式增强头部可读性
        header = f"""

"""
        lines = [header.strip(), ""]
        
        # 排序：将 "核心子系统" 排在前面，"其他" 排在后面
        def sort_key(k):
            if "核心子系统" in k: return 0
            if "整体" in k: return 1
            if "组件" in k: return 2
            return 99

        
        for group in sorted_groups:
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

