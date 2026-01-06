import os
import json
import markdown
from typing import Dict, List, Any
from playwright.sync_api import sync_playwright
from openai import OpenAI
from config import settings
from loguru import logger

class ContentGenerator:
    def __init__(self, client: OpenAI, patent_data: Dict, parts_db: Dict, image_parts: Dict):
        self.client = client
        self.parts_db = parts_db
        self.image_parts = image_parts

        # 预处理数据，方便后续调用
        self.biblio = patent_data.get("bibliographic_data", {})
        self.claims = patent_data.get("claims", [])
        self.description = patent_data.get("description", {})
        self.drawings = patent_data.get("drawings", [])
        
        # 提取关键文本块 (为了节省 Token 并提高准确度，提前做简单的清洗)
        self.text_background = self.description.get("background_art", "")
        self.text_summary = self.description.get("summary_of_invention", "")
        self.text_details = self.description.get("detailed_description", "")

        # 提取独立权利要求
        self.independent_claims = [
            c["claim_text"] for c in self.claims if c.get("claim_type") == "independent"
        ]
    
    def generate_report_json(self) -> Dict[str, Any]:
        """
        执行全流程分析，返回包含5要素的结构化字典。
        """
        logger.info(f"开始生成专利分析报告: {self.biblio.get('application_number', 'Unknown')}")

        try:
            # Step 1: 宏观逻辑分析 (问题 & 方案 & 预期效果)
            macro_data = self._analyze_macro_logic()
            
            # Step 2: 微观细节填充 (特征 & 手段 & 验证效果)
            # 将 Step 1 的结果传给 Step 2 作为 Context
            micro_data  = self._analyze_micro_details(macro_data)

            # 3. 图解生成 (遍历每一张图，生成解说和部件表)
            figures_data = self._generate_figures_analysis()
            
            # 4. 组装最终 JSON
            # 寻找主图：通常是第一张图，或者摘要附图
            abstract_figure = self.biblio.get("abstract_figure")
            if not abstract_figure and self.drawings:
                abstract_figure = self.drawings[0].get("file_path")

            final_report = {
                # 基础信息
                "ai_title": macro_data.get("ai_title", self.biblio.get("invention_title")),
                "abstract": self.biblio.get("abstract", ""),
                "abstract_figure": abstract_figure.replace('images', 'annotated_images'),
                
                # 核心逻辑五要素
                "technical_problem": macro_data.get("technical_problem"),
                "technical_scheme": macro_data.get("technical_scheme"), 
                "technical_means": micro_data.get("technical_means"), 
                "technical_features": micro_data.get("technical_features", []),
                "technical_effects": micro_data.get("technical_effects", []),
                
                # 图解详细信息
                "figure_explanations": figures_data
            }
            
            logger.success("专利分析 JSON 生成完成")
            return final_report

        except Exception as e:
            logger.error(f"生成报告过程中发生错误: {str(e)}")
            return {"error": str(e), "status": "failed"}
        
    def _analyze_macro_logic(self) -> Dict[str, Any]:
        """
        阶段一：分析技术问题、技术方案概括。
        目标受众：专利审查员/技术专家。
        输入：背景技术 + 发明内容(部分) + 独权
        """
        logger.debug("正在执行阶段一：宏观逻辑分析 (审查员模式)...")

        # System Prompt: 设定为协助审查员快速抓住重点的角色
        system_prompt = (
            "你是一位经验丰富的专利审查员助理。你的任务是协助审查员快速理解专利的核心实质。"
            "请基于‘问题-解决方案’的逻辑（Problem-Solution Approach），客观、精准地提炼信息。"
            "严禁使用营销、夸张或非技术性的描述语言。"
            "请以严格的 JSON 格式输出。"
        )

        user_content = f"""
        # 1. 待分析文本
        【背景技术片段】
        {self.text_background[:2500] if self.text_background else "（背景技术为空，请根据发明内容推断现有技术的缺陷）"}

        【发明内容片段】
        {self.text_summary[:2500]}

        【独立权利要求】
        {json.dumps(self.independent_claims, ensure_ascii=False)}

        # 2. 分析任务
        请分析上述材料，为审查员提取以下三个核心要素：

        ### A. 核心技术标签 (ai_title)
        - **目标**：生成一个简练的**技术主题词**，帮助审查员一眼识别该专利的**核心创新点**。
        - **要求**：
          1. **高度凝练**：[技术领域] + [核心区别特征]。
          2. **去冗余**：去掉“一种”、“的方法”、“系统”等通用词，除非对理解至关重要。
          3. **风格**：中性、专业。
          4. **字数**：20字以内。
        - **示例**：
          - 原标题：一种基于神经网络的风机叶片故障检测方法
          - 优化后：基于神经网络的风机叶片故障检测
          - 更好(如果核心是剪枝算法)：神经网络剪枝优化的叶片故障检测

        ### B. 客观技术问题 (technical_problem)
        - **目标**：准确界定发明要解决的**客观技术问题**。
        - **思考逻辑**：
          1. 现有技术（Closest Prior Art）存在什么具体缺陷？（如：计算量大、信号信噪比低、结构强度不足）。
          2. 这个缺陷导致了什么直接后果？
        - **输出要求**：直接陈述技术缺陷，不要包含“本发明旨在解决”等套话。
        - **示例**：现有声发射监测方法受环境噪声影响大，导致微小磨损的特征提取困难，响应滞后。

        ### C. 技术方案概要 (technical_scheme)
        - **目标**：用**工程语言**重述独立权利要求，阐明**技术手段**如何解决上述问题。
        - **要求**：
          1. **实质重述**：概括独立权利要求的步骤或结构，去除“所述”、“其特征在于”等法律词汇。
          2. **逻辑关联**：体现“手段 -> 功能”的逻辑。
          3. **精准度**：保留关键的技术参数或特定算法名称（如果独权里有），因为这些通常是创造性的来源。

        # 3. 输出格式
        仅输出 JSON 对象：
        {{
            "ai_title": "...",
            "technical_problem": "...",
            "technical_scheme": "..."
        }}
        """

        return self._call_llm(system_prompt, user_content)
    

    def _analyze_micro_details(self, macro_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        阶段二：分析技术特征、技术手段、具体效果。
        目标受众：专利审查员/技术专家。
        输入：阶段一结果 + 全部权利要求 + 具体实施方式(截断)
        """
        logger.debug("正在执行阶段二：微观细节提取 (审查员模式)...")

        # 截断策略：保留前 6000 字符。
        # 优化建议：如果有条件，最好使用 embedding 检索包含 "原理"、"实验"、"数据" 关键词的段落
        details_snippet = self.text_details[:6000]

        # System Prompt: 设定为精通“权利要求解释”和“技术验证”的专家
        system_prompt = (
            "你是一位精通机械/电子/算法原理的专利技术专家。你的任务是将法律化的权利要求"
            "与具体化的实施方式相结合，进行深度的技术拆解。"
            "你需要识别核心创新点，并从物理或逻辑层面解释其运作机理。"
            "请以严格的 JSON 格式输出。"
        )

        user_content = f"""
        # 1. 输入上下文
        【宏观逻辑（问题与方案）】
        {json.dumps(macro_context, ensure_ascii=False)}

        【权利要求书 (Claims)】
        {json.dumps(self.claims, ensure_ascii=False)}

        【具体实施方式片段 (Embodiments)】
        {details_snippet}

        # 2. 分析任务
        请结合上述材料，完成以下深度分析：

        ### A. 技术特征拆解 (technical_features)
        - **目标**：构建技术方案的“BOM表”或“流程节点”。
        - **要求**：
          1. **必要特征**：必须包含独立权利要求中的所有限定特征，标记为 `is_essential: true`。
          2. **区别/优选特征**：提取从属权利要求或实施例中对解决问题起关键作用的改进点，标记为 `false`。
          3. **描述**：将“权利要求语言”翻译为“具体工程描述”（例如：将“第一连接件”描述为“通过螺栓固定的法兰盘”）。
        
        ### B. 技术手段/机理深度解析 (technical_means)
        - **目标**：解释**“为什么”**这些特征能解决前述的【技术问题】。
        - **思考逻辑**：
          1. 核心区别特征（Novelty）是如何工作的？
          2. 涉及什么物理原理（如杠杆、热传导）、化学反应或算法逻辑（如FFT变换、阈值判定）？
          3. **因果链**：描述 "结构/步骤 -> 机理 -> 结果" 的完整链条。
        - **篇幅**：200-300字，形成一段逻辑连贯的技术短文。

        ### C. 技术效果与证据 (technical_effects)
        - **目标**：提取支撑创造性的**实证数据**或**具体结论**。
        - **优先级**：
          1. **定量数据**（最高）：如“效率提升15%”、“响应时间缩短至0.02s”。
          2. **定性结论**（次之）：如“显著降低了误报率”。
        - **关联性**：必须指出该效果是由哪个特征带来的（Source Feature）。

        # 3. 输出格式
        仅输出 JSON 对象：
        {{
            "technical_features": [
                {{
                    "name": "特征名称 (如: 空窗密度)",
                    "description": "工程化描述 (如: 基于0.02s时间窗的高通滤波信号脉冲计数)",
                    "is_essential": true  // 独权特征为true，从权/实施例为false
                }},
                ...
            ],
            "technical_means": "...",
            "technical_effects": [
                {{
                    "effect": "效果描述 (优先提取数据)",
                    "source_feature": "对应特征 (如: 0.02s短时间窗)"
                }},
                ...
            ]
        }}
        """

        return self._call_llm(system_prompt, user_content)
    
    def _generate_figures_analysis(self) -> List[Dict[str, Any]]:
        """
        Step 3: 图解生成。
        遍历 drawings，为每一张图生成：AI解说 + 结构化部件表。
        """
        results = []
        
        # 预处理：建立段落索引，方便查找图片引用的上下文
        paragraphs = self.text_details.split("\n")

        for drawing in self.drawings:
            file_path = drawing.get("file_path")
            label = drawing.get("figure_label", "") # 如 "图1"
            caption = drawing.get("caption", "")    # 如 "系统流程图"
            
            part_ids = []
            if file_path:
                # 提取文件名进行匹配 (忽略路径差异)
                filename = os.path.basename(file_path)
                part_ids = self.image_parts.get(filename, [])
            
            # 构建部件上下文
            parts_context_str = ""
            parts_table_data = [] # 用于 JSON 输出的结构化数据
            
            if part_ids:
                temp_desc_list = []
                for pid in part_ids:
                    # 兼容 pid 是 int 或 str 的情况
                    info = self.parts_db.get(str(pid)) or self.parts_db.get(int(pid))
                    if info:
                        name = info.get('name', '未知')
                        func = info.get('function', '未知功能')
                        # 为 Prompt 准备的文本
                        temp_desc_list.append(f"- 标号 {pid} ({name}): {func}")
                        # 为 JSON 准备的数据
                        parts_table_data.append({
                            "id": pid,
                            "name": name,
                            "function": func
                        })
                parts_context_str = "\n".join(temp_desc_list)
            else:
                parts_context_str = "（该图未识别到具体部件标号，可能是流程图、数据曲线图或整体结构图）"

            # 2. 获取说明书上下文
            # 查找包含 "图1" 或 "Fig.1" 的段落
            related_text = ""
            search_key = label.replace(" ", "") # 去空格匹配
            if search_key:
                # 取相关度最高的前 3 个段落
                matches = [p.strip() for p in paragraphs if search_key in p.replace(" ", "")]
                related_text = "\n".join(matches[:3])
            
            if not related_text:
                related_text = caption # 降级使用标题

            # 3. 生成图片解说
            image_explanation = self._generate_single_figure_caption(
                label, caption, parts_context_str, related_text
            )

            # 4. 存入结果
            results.append({
                "image_path": file_path.replace('images', 'annotated_images'),
                "image_title": f"{label} {caption}".strip(),
                "image_explanation": image_explanation,
                "parts_info": parts_table_data # 结构化部件数据
            })
            
        return results

    def _generate_single_figure_caption(self, label: str, caption: str, parts_context: str, text_context: str) -> str:
        """
        生成单张图片的“看图说话”
        """
        system_prompt = (
            "你是一位拥有10年经验的专利技术解说专家。你的特长是将晦涩的专利图纸和文字，"
            "转化为直观、逻辑流畅的工程技术解说词。"
        )
        
        user_prompt = f"""
        # 1. 待分析对象
        - **图号与标题**：{label} - {caption}
        - **包含部件 (Visual Clues)**：
        {parts_context if parts_context else "（未检测到具体部件标号，可能是流程图、数据图或整体概览图）"}

        # 2. 原文参考 (Context)
        {text_context}

        # 3. 生成指令
        请根据图片类型采用不同的解说策略，编写一段 **150字左右** 的解说段落：

        **场景 A：如果是机械/物理结构图（存在部件标号）**
        - **空间构建**：拒绝清单式罗列（如“1是A，2是B”）。必须描述部件的**装配关系**（如“嵌套于”、“连接至”、“支撑着”）。
        - **动态逻辑**：描述**力的传递**或**动作序列**（如“电机(1)驱动齿轮(2)旋转，从而带动...”）。
        - **标号规范**：提及具体部件时，**必须**在名称后紧跟括号和标号，格式为 `部件名(ID)`。

        **场景 B：如果是流程图/算法图/数据曲线**
        - **逻辑流转**：描述步骤的先后顺序、数据的处理流程或判断逻辑。
        - **数据含义**：如果是曲线图，解释横纵坐标代表什么，以及曲线变化说明了什么结论（如“随着X增加，Y呈现指数级下降，证明了...”）。

        # 4. 写作要求
        - **风格去噪**：将“所述”、“设置有”替换为“该”、“位于”、“包含”等自然语言。
        - **结构清晰**：
          1. 开篇：一句话概括该图展示的核心模块或原理。
          2. 中段：按逻辑/空间/时间顺序展开细节。
          3. 结尾：(可选) 点明该设计带来的最终效果。
        - **严禁**：不要使用 Markdown 标题（如 ###），不要出现“如图所示”、“好的”等废话。

        # 5. 输出
        直接输出生成的解说段落。
        """

        try:
            res = self.client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3, # 降低温度，减少幻觉，保证准确性
            )
            return res.choices[0].message.content.strip()
        except Exception as e:
            logger.warning(f"图片解说生成失败 {label}: {e}")
            return f"图{label}展示了{caption}的示意图。{parts_context.replace('- ', '')[:50]}..."

    
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

