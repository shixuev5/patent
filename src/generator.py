import os
import re
import json
from pathlib import Path
from typing import Dict, List, Any
from loguru import logger
from src.llm import get_llm_service
from config import Settings

class ContentGenerator:
    def __init__(self, patent_data: Dict, parts_db: Dict, image_parts: Dict, annotated_dir: Path):
        self.llm_service = get_llm_service()
        self.parts_db = parts_db
        self.image_parts = image_parts
        self.annotated_dir = annotated_dir

        # 预处理数据，方便后续调用
        self.biblio = patent_data.get("bibliographic_data", {})
        self.claims = patent_data.get("claims", [])
        self.description = patent_data.get("description", {})
        self.drawings = patent_data.get("drawings", [])
        
        # 提取关键文本块 (为了节省 Token 并提高准确度，提前做简单的清洗)
        self.text_background = self.description.get("background_art", "")
        self.text_summary = self.description.get("summary_of_invention", "")
        self.text_effect =  self.description.get("technical_effect", "")
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
            global_context = {
                "title": macro_data.get("ai_title"),
                "problem": macro_data.get("technical_problem"),
                "effects": micro_data.get("technical_effects", [])
            }
            figures_data = self._generate_figures_analysis(global_context)
            
            # 4. 组装最终 JSON
            # 寻找主图：通常是第一张图，或者摘要附图
            abstract_figure = self.biblio.get("abstract_figure")
            if not abstract_figure and self.drawings:
                abstract_figure = self.drawings[0].get("file_path")

            final_report = {
                # 基础信息
                "ai_title": macro_data.get("ai_title", self.biblio.get("invention_title")),
                "ai_abstract": macro_data.get("ai_abstract", self.biblio.get("abstract", "")),
                "abstract_figure": abstract_figure.replace('images', 'annotated_images'),
                
                # 核心逻辑五要素
                "technical_problem": macro_data.get("technical_problem"),   # 技术问题
                "technical_scheme": macro_data.get("technical_scheme"),     # 技术方案
                "technical_means": micro_data.get("technical_means"),       # 技术手段
                "technical_features": micro_data.get("technical_features", []),   # 技术特征
                "technical_effects": micro_data.get("technical_effects", []),     # 技术效果
                
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
        logger.debug("正在执行阶段一：宏观逻辑分析...")

        # System Prompt: 设定为协助审查员快速抓住重点的角色
        system_prompt = (
            "你是一位经验丰富的专利审查员助理。你的任务是协助审查员快速理解专利的核心实质。"
            "请基于‘问题-解决方案’的逻辑（Problem-Solution Approach），客观、精准地提炼信息。"
            "请以严格的 JSON 格式输出。"
        )

        user_content = f"""
        # 1. 待分析文本
        【背景技术 (Background Art)】
        {self.text_background[:2500] if self.text_background else "（背景技术为空，请根据发明内容推断现有技术的缺陷）"}

        【发明内容-技术方案 (Technical Solution)】
        {self.text_summary[:2500]}

        【发明内容-有益效果 (Beneficial Effects)】
        {self.text_effect if self.text_effect else "（文中未明确单独列出有益效果，请根据技术方案推断）"}

        【独立权利要求 (Independent Claims)】
        {json.dumps(self.independent_claims, ensure_ascii=False)}

        # 2. 分析任务
        请分析上述材料，为审查员提取以下四个核心要素：

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

        ### A2. 智能摘要 (ai_abstract)
        - **目标**：生成一段高信息密度的技术摘要。
        - **核心要求**：
          1. **直接切入**：禁止使用“本发明涉及...”、“公开了...”等废话作为开头。直接描述“针对[现有问题]，提出一种基于[核心手段]的[方案名称]。”
          2. **参数为王**：如果权利要求中包含具体的温度、比例、算法公式等关键参数，必须保留在摘要中。
          3. **效果闭环**：结尾必须说明该方案带来的核心指标提升（如效率提升20%）。

        ### B. 客观技术问题 (technical_problem)
        - **核心逻辑**：基于“有益效果”反推“客观问题”。
        - **思考路径**：
          1. **看效果**：阅读【有益效果】，找出本发明最核心的性能提升点（如：响应速度、精度、成本）。
          2. **找差异**：对比【背景技术】，确定现有技术因缺乏什么手段而无法达到上述效果。
          3. **定问题**：将“现有技术的具体缺陷”转化为“本发明要解决的任务”。
        - **要求**：
          - **具体化**：拒绝泛泛而谈（如“解决效率低的问题”）。必须指出是**什么原因**导致的效率低（如“解决因XX算法复杂度高导致的计算效率低问题”）。
          - **客观性**：不要直接复制申请人声称的“主观问题”（有时申请人会夸大），要基于技术事实总结。
        - **格式范例**：
          - *差*：解决风机监测难的问题。
          - *好*：解决现有声发射监测技术在强背景噪声下特征提取困难，导致对早期磨损响应滞后的问题。

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
            "ai_abstract": "...",
            "technical_problem": "...",
            "technical_scheme": "..."
        }}
        """

        return self.llm_service.chat_completion_json(
            model=Settings.LLM_MODEL_REASONING,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=0.1
        )
    

    def _analyze_micro_details(self, macro_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        阶段二：分析技术特征、技术手段、具体效果。
        目标受众：专利审查员/技术专家。
        输入：阶段一结果 + 全部权利要求 + 具体实施方式(截断) + 声称的技术效果
        """
        logger.debug("正在执行阶段二：微观细节提取...")

        system_prompt = (
            "你是一位精通技术验证的专利审查专家。你的核心任务是进行‘特征-效果’的归因分析。"
            "你需要验证申请人声称的效果是否有实施例数据支撑，并找出产生该效果的关键技术特征。"
            "请以严格的 JSON 格式输出。"
        )

        user_content = f"""
        # 1. 输入上下文
        【宏观逻辑框架】
        {json.dumps(macro_context, ensure_ascii=False)}

        【权利要求书 (Claims)】
        {json.dumps(self.claims, ensure_ascii=False)}

        【具体实施方式 (Embodiments)】
        {self.text_details}

        【申请人声称的有益效果 (Target Effects)】
        {self.text_effect}

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
        - **约束**：严禁简单重复 `technical_scheme` 的步骤流程。必须侧重于解释背后的物理原理、算法逻辑或因果机制。
        - **篇幅**：200-300字，形成一段逻辑连贯的技术短文。

        ### C. 效果归因与验证 (technical_effects)
        - **目标**：将【声称的有益效果】与【技术特征】和【实施例数据】进行关联。
        - **操作步骤**：
          1. 阅读【声称的有益效果】，列出核心优点（如：响应快、抗干扰）。
          2. **找原因 (Source)**：在【权利要求】中寻找，是哪个具体特征（结构/步骤）导致了这个优点？
          3. **找证据 (Evidence)**：在【具体实施方式】中寻找，是否有实验数据、仿真结果或对比案例支持这个优点？如果实施方式中仅重复了定性描述而无具体数据/案例，请填写“仅有定性描述，无实验数据支持”。

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
            "technical_means": "本技术方案的核心创新在于引入空窗密度和峰度作为特征值，以解决风机齿轮箱中噪声干扰导致的监测滞后问题。其核心机制在于...",
            "technical_effects": [
                {{
                    "effect": "高响应速度",
                    "source_feature": "空窗密度特征提取",
                    "evidence": "图7对比显示，空窗时间分辨率达到0.02s，优于传统RMS方法的1s响应时间。"
                }},
                {{
                    "effect": "结构强度高",
                    "source_feature": "加强筋结构",
                    "evidence": "仅有定性描述，无实验数据支持"  <-- 注意这种负面反馈
                }}
                ...
            ]
        }}
        """

        return self.llm_service.chat_completion_json(
            model=Settings.LLM_MODEL_REASONING,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=0.1
        )
    
    def _generate_figures_analysis(self, global_context: Dict) -> List[Dict[str, Any]]:
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

            # 处理图片路径
            image_abs_path = None
            if file_path:
                # 提取文件名 (忽略原始的 images/ 前缀)
                filename = os.path.basename(file_path)
                # 拼接标注图片目录的绝对路径
                target_path = self.annotated_dir / filename
                
                if target_path.exists():
                    image_abs_path = str(target_path)
                else:
                    logger.warning(f"Annotated image not found: {target_path}, skipping vision analysis.")

            # 匹配图片 OCR 识别部件
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
            clean_label = label.replace(" ", "") # 去空格匹配

            if clean_label:
                pattern = re.compile(rf"{clean_label}(?!\d)")

                matches = [p.strip() for p in paragraphs if pattern.search(p.replace(" ", ""))]
                matches.sort(key=len, reverse=True)
                related_text = "\n---\n".join(matches[:2])

            if not related_text:
                related_text = caption # 降级使用标题

            # 3. 生成图片解说
            image_explanation = self._generate_single_figure_caption(
                label, caption, parts_context_str, related_text, global_context, image_abs_path
            )

            # 4. 存入结果
            results.append({
                "image_path": file_path.replace('images', 'annotated_images'),
                "image_title": f"{label} {caption}".strip(),
                "image_explanation": image_explanation,
                "parts_info": parts_table_data # 结构化部件数据（可能为空列表）
            })

        return results

    def _generate_single_figure_caption(self, label: str, caption: str, parts_context: str, text_context: str, global_context: Dict, image_path: str) -> str:
        """
        生成单张图片的“看图说话”
        """

        effects_str = "\n".join([f"- {e['effect']}: {e['source_feature']}" for e in global_context.get('effects', [])[:3]])

        vlm_prompt = f"""
        # 角色设定
        你是一位顶尖的专利可视化分析师。你的受众是**非本技术领域的专利审查员**。
        审查员面临大量的阅读压力，你的任务是**降低他们的认知负荷**，通过一段通俗、逻辑严密的解说，让他们一眼看懂这张图的核心含义。

        # 0. 全局认知 (Global Knowledge)
        在分析图片前，请先理解本专利的核心逻辑，这将作为你**解读图片意图的唯一指引**：
        - **核心发明点**：{global_context.get('title')}
        - **要解决的客观问题**：{global_context.get('problem')}
        - **预期达到的效果**：
        {effects_str}

        # 1. 输入数据
        - **图号与标题**：{label} - {caption}
        - **视觉线索 (Visual Clues)**：
          图片经过了机器视觉标注，请将图中的**显眼文字（蓝色/红色文本）、数字标号或指示箭头**视为最高优先级的锚点。
          这些视觉标记对应以下部件信息（未在图中明确指出的部件请勿强行描述）：
          {parts_context}
        - **局部描述 (Context)**：
          {text_context}

        # 2. 思考步骤 (请在内心执行，无需输出)
        1. **锚点对齐**：首先锁定图中的【视觉线索】，确认关键部件在图中的具体位置，构建空间坐标系。
        2. **意图解码**：结合【全局认知】，问自己：申请人为什么要放这张图？
           - *如果是结构图：* 哪个部件是解决【客观问题】的“金钥匙”？
           - *如果是数据图：* 曲线的哪个“拐点”或“极值”证明了【预期效果】？
        3. **逻辑串联**：用一条看不见的线（力流、数据流、时间流）将静态部件串联起来。

        # 3. 生成策略 (根据图片类型选择)
        **[类型 A：机械/结构/装置图]**
        - **拒绝清单体**：严禁写成“1是A，2是B”的说明书列表。
        - **关注交互与传递**：描述力的传递、流体的走向或动作的触发。例如：“电机(1)输出的扭矩，经由减速器(2)放大后，驱动...”
        - **空间方位**：使用“位于...上游”、“紧贴于...内壁”等词建立清晰的空间感。

        **[类型 B：流程图/时序图/算法逻辑]**
        - **关注变化**：描述数据流经各步骤时发生了什么“质变”。例如：“原始信号经过降噪(S1)后变得纯净，随即进入判定模块(S2)...”
        - **因果链条**：强调步骤之间的逻辑必要性。

        **[类型 C：数据曲线/仿真图/对比图]**
        - **不仅读轴，更要读点**：不要只描述趋势，必须指出**“关键转折点”**或**“最大差异区”**。
        - **数据翻译**：将抽象的坐标轴含义翻译为具体性能。例如：“纵坐标的下降并不只代表数值减小，更意味着系统延迟的显著降低。”

        # 4. 写作规范 (必须严格遵守)
        - **三段式微结构**：
          1. **一句话定位**：开篇直接点明这张图展示了本发明的哪个核心模块、原理或实验结果。
          2. **动态推演**：中间部分按顺序描述工作过程、受力传递或数据变化规律。
          3. **价值闭环**：结尾必须显式关联到【全局认知】中的某项效果（如“这一设计直接解决了[客观问题]...”）。
        - **语言风格**：
          - **通俗化**：用“起到...作用”代替“被配置为”。
          - **规范引用**：提及部件时必须使用 `名称(标号)` 格式，如 `传动轴(12)`。
        - **篇幅**：控制在 150-200 字之间，紧凑且高密度。

        # 5. 输出指令
        直接输出最终的解说段落，不要包含任何标题、Markdown标记或“好的”等客套话。
        """

        try:
            logger.info(f"正在进行视觉思考分析: {label} ({os.path.basename(image_path)})")

            content = self.llm_service.analyze_image_with_thinking(
                image_path=image_path,
                prompt=vlm_prompt
            )
            return content.strip()
        except Exception as e:
            logger.warning(f"图片解说生成失败 {label}: {e}")
            return f"图{label}展示了{caption}的示意图。{parts_context.replace('- ', '')[:50]}..."
