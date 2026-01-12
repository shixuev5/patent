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
        # 1. 用于定义问题：看背景和目标
        self.text_field = self.description.get("technical_field", "")
        self.text_background = self.description.get("background_art", "")
        self.text_effect =  self.description.get("technical_effect", "")
        self.list_ipc = self.biblio.get("ipc_classifications", [])

        # 2. 用于概括方案：看摘要、发明内容
        self.text_title = self.biblio.get("invention_title", "")
        self.text_abstract = self.biblio.get("abstract", "")
        self.text_summary = self.description.get("summary_of_invention", "")

        # 3. 用于验证细节：看具体实施方式
        self.text_details = self.description.get("detailed_description", "")

        # 提取独立权利要求
        self.independent_claims = [
            c["claim_text"] for c in self.claims if c.get("claim_type") == "independent"
        ]
    
    def generate_report_json(self) -> Dict[str, Any]:
        """
        执行专利逻辑分析流水线。
        Flow: [Domain/Problem] -> [Solution/Title] -> [Implementation/Verification] -> [Visuals]
        """
        logger.info(f"开始生成专利分析报告: {self.biblio.get('application_number', 'Unknown')}")

        try:
            # === Step 1: 领域定位与问题定义 ===
            # 输入：背景技术、技术效果
            # 输出：technical_field, technical_problem
            domain_problem_data = self._analyze_domain_and_problem()
            
            # === Step 2: 解决方案封装 ===
            # 输入：Step 1的结果、独立权利要求、发明内容
            # 输出：ai_title, ai_abstract, technical_scheme
            solution_data = self._synthesize_solution_package(domain_problem_data)
            
            # 合并核心逻辑数据 (Core Logic Context)
            core_logic = {**domain_problem_data, **solution_data}

            # === Step 3: 权利要求解构与特征定义 ===
            # 输入：核心逻辑、全部权利要求
            # 输出：technical_features
            features_data = self._extract_features(core_logic)
            feature_list = features_data.get("technical_features", [])

            # === Step 4: 实施例取证与效果验证 ===
            # 输入：核心逻辑、技术特征、具体实施方式
            # 输出：technical_means, technical_effects
            verification_data = self._verify_evidence(core_logic, feature_list)


            # 图解生成 (遍历每一张图，生成解说和部件表)
            global_context = {
                "title": solution_data.get("ai_title"),
                "problem": domain_problem_data.get("technical_problem"),
                "effects": verification_data.get("technical_effects", [])
            }
            figures_data = self._generate_figures_analysis(global_context)
            
            # 组装最终 JSON
            # 寻找主图：通常是第一张图，或者摘要附图
            main_fig = self.biblio.get("abstract_figure")
            if not main_fig and self.drawings:
                main_fig = self.drawings[0].get("file_path")

            final_report = {
                # 基础信息
                "ai_title": solution_data.get("ai_title", self.biblio.get("invention_title")),
                "ai_abstract": solution_data.get("ai_abstract", self.biblio.get("abstract", "")),
                "abstract_figure": main_fig.replace('images', 'annotated_images')  if main_fig else None,
                
                # 核心逻辑六要素
                "technical_field": domain_problem_data.get("technical_field"),              # 技术领域     
                "technical_problem": domain_problem_data.get("technical_problem"),          # 技术问题
                "technical_scheme": solution_data.get("technical_scheme"),                  # 技术方案
                "technical_means": verification_data.get("technical_means"),                # 技术手段
                "technical_features": verification_data.get("technical_features", []),      # 技术特征
                "technical_effects": verification_data.get("technical_effects", []),        # 技术效果
                
                # 图解详细信息
                "figure_explanations": figures_data
            }
            
            logger.success("专利分析 JSON 生成完成")
            return final_report

        except Exception as e:
            logger.error(f"生成报告过程中发生错误: {str(e)}")
            return {"error": str(e), "status": "failed"}
        
    def _analyze_domain_and_problem(self) -> Dict[str, str]:
        """
        Step 1: 定位领域与定义问题。
        关键点：只看“过去(Background)”和“目标(Effect)”，不看“现在(Claims)”，以确保问题的客观性。
        """
        logger.debug("Step 1: Analyzing Domain and Problem...")

        system_prompt = (
            "你是一名资深专利审查员。任务是基于‘问题-解决方案’方法（Problem-Solution Approach），"
            "从现有技术的缺陷中反推客观技术问题。\n"
            "输出必须是严格的 JSON 格式。"
        )

        # Context 严选：排除 Claims，防止用方案来定义问题
        user_content = f"""
        # 1. 
        【IPC 分类号】: {json.dumps(self.list_ipc, ensure_ascii=False)} 
        *(请参考 IPC 代码含义来校准技术领域)*

        【原始领域描述】
        {self.text_field}

        【背景技术 (现有技术的缺陷)】
        {self.text_background[:3000] if self.text_background else "（未提供详细背景，请基于常识和下文目标反推）"}

        【预期达到的有益效果 (Target)】
        {self.text_effect if self.text_effect else "（未明确列出，需推断）"}

        # 2. 分析任务
        ### A. 标准化技术领域 (technical_field)
        - **指令**：结合 IPC 代码的含义和文本描述，修正为标准的 IPC 分类层级术语。
        - **格式**：[一级行业] - [二级核心技术]。
        - **示例**:
          - *Bad*: 本发明涉及一种用于家庭烹饪的装置。
          - *Good*: 消费电子 - 智能温控烹饪设备
          - *Bad*: 一种基于深度学习的图像处理方法。
          - *Good*: 计算机视觉 - 语义分割算法

        ### B. 客观技术问题 (technical_problem)
        - **逻辑链**：
          1. 目标是什么？(看有益效果)
          2. 为什么现有技术做不到？(看背景技术中的痛点，如：计算量大、结构干涉、材料不耐腐蚀)
          3. **定义问题**：本申请要解决的核心障碍是什么？
        - **避坑指南**：
          - **禁止**出现“本发明通过...”字样。
          - **必须**具体化。不要说“效率低”，要说“因采用串行处理架构导致的数据吞吐效率低”。

        # 3. 输出 JSON Schema
        {{
            "technical_field": "string",
            "technical_problem": "string"
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

    def _synthesize_solution_package(self, problem_context: Dict[str, str]) -> Dict[str, str]:
        """
        Step 2: 封装解决方案。
        关键点：结合 Step 1 确定的“问题”，将“独权”转化为易读的方案和标题。
        """
        logger.debug("Step 2: Synthesizing Solution Package...")

        system_prompt = (
            "你是一名顶级科技期刊的编辑。你的任务是将晦涩的法律文档（专利权利要求）重写为"
            "高信息密度的技术摘要和工程方案。你的读者是该领域的技术专家，不要使用法律套话。"
            "请严格按照 JSON 格式输出。"
        )

        # Context 严选：只提供解决问题所需的 独权 和 Summary
        user_content = f"""
        # 1. 前序分析结论
        【已确定的技术领域】: {problem_context.get('technical_field')}
        【待解决的技术问题】: {problem_context.get('technical_problem')}

        # 2. 核心素材
        【原发明名称 (Original Title)】
        {self.text_title}

        【独立权利要求 (Independent Claims)】
        {json.dumps(self.independent_claims, ensure_ascii=False)}

        【发明内容 (Summary of Invention)】
        {self.text_summary[:3000]}

        【原始摘要 (Reference)】
        {self.text_abstract}

        # 3. 分析任务
        ### A. 核心技术标题 (ai_title)
        - **指令**：参考原发明名称，生成一个精准的技术主题词。
        - **公式**：[核心算法/结构] + [动词] + [应用场景]。
        - **Few-Shot Examples**:
          - *Original*: 一种用于风力发电机的叶片故障检测方法
          - *Refined*: 基于多尺度时频分析的风机叶片早期裂纹监测
          - *Original*: 一种显示装置
          - *Refined*: 采用Micro-LED阵列的高动态范围(HDR)显示驱动架构

        ### B. 智能摘要 (ai_abstract)
        - **任务**：撰写一段 300字左右 的高密度摘要。
        - **结构要求**：
          1. **领域限定** (1句): "本发明涉及{problem_context.get('technical_field')}领域。"
          2. **背景切入** (1句): "针对[...的问题]，本方案..."
          3. **技术内核** (3-4句): 提取独权步骤。**关键约束**：如果文中包含具体的**数字参数**（温度、比例、阈值公式）、**特定材料**或**算法名称**，必须保留！这是技术专家的关注点。
          4. **性能闭环** (1句): "最终实现了[...的具体指标提升]。"
        - **Negative Constraints**:
          - 禁止使用 "所述"、"其特征在于" 等法律词汇。

        ### C. 技术方案概要 (technical_scheme)
        - **任务**：将独立权利要求翻译为“工程实施手册”。
        - **多独权处理逻辑**：
          - 检查输入的独立权利要求数量。
          - 如果有多个独立权利要求（例如 Claim 1 是装置，Claim 7 是方法），必须分条目描述。
          - **格式示例**：
            "1) 装置主体：由加载块和环带组成，加载块通过...；\n2) 计算方法：建立柔轮与轴承的几何模型，通过CAE分析求解..."
        - **逻辑重组**：如果是方法，按步骤描述；如果是装置，按连接关系描述。
        - **Example**:
          - *Claim Input*: "一种装置，其特征在于包括部件A，所述部件A连接部件B..."
          - *Output*: "系统主体由部件A和部件B构成。部件A通过法兰与部件B刚性连接，起支撑作用..." (转化为描述性语言)

        # 4. 输出 JSON Schema
        {{
            "ai_title": "string",
            "ai_abstract": "string",
            "technical_scheme": "string"
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
    
    def _extract_features(self, core_logic: Dict[str, Any]) -> Dict[str, Any]:
        """
        Step 3: 权利要求分析。
        目标：从审查员视角提取“区别技术特征”，剔除公知常识。
        """
        logger.debug("Step 3: Analyzing Claims structure...")

        system_prompt = (
            "你是一名资深的专利实质审查员。你的核心能力是基于‘三步法’（Problem-Solution Approach）"
            "准确识别‘区别技术特征’（Distinguishing Technical Features）。\n"
            "你需要透过权利要求的法律措辞，识别出真正对技术问题做出贡献的核心结构或步骤。"
        )

        user_content = f"""
        # 1. 审查背景 (Examination Basis)
        【待解决的技术问题】: {core_logic.get('technical_problem')}
        【技术方案概要】: {core_logic.get('technical_scheme')}

        # 2. 权利要求书 (Claims)
        {json.dumps(self.claims, ensure_ascii=False)}

        # 3. 分析任务：构建技术特征列表 (BOM)
        请扫描所有权利要求，提取技术特征，并按照以下严格标准进行分类和清洗：

        ### A. 筛选与过滤规则 (CRITICAL)
        1.  **剔除“公知常识”与“通用部件”**：
            -   **坚决剔除**：未做特殊限定的通用支撑件（如“机架”、“底座”）、通用连接件（如“螺栓”、“导线”）、通用驱动源（如“市电”、“普通电机”）或通用控制模块（如“控制器”、“PC机”）。
            -   **例外情况**：只有当这些部件具有**特殊的结构改进**（例如“具有蜂窝状吸能结构的机架”）并直接有助于解决上述【技术问题】时，才可保留。
        2.  **独权二分法**：
            -   仔细阅读独立权利要求。
            -   **Pre-amble (前序部分)**：描述现有技术或通用环境的部分（通常在“其特征在于”之前）。
            -   **Characterizing Portion (特征部分)**：描述本发明核心改进的部分（通常在“其特征在于”之后）。
            -   重点提取特征部分内容。

        ### B. 字段填写指南
        -   `name`: 特征名称。必须精炼、标准（例如使用“弧面加载块”而非“那个带有弧面的块”）。
        -   `description`: 简要说明该特征在权利要求中的定义。
        -   `claim_source`:
            -   `independent`: 该特征**首次出现**在独立权利要求中。
            -   `dependent`: 该特征**首次出现**在从属权利要求中。
        -   `is_distinguishing`: 
            -   `true`: **区别特征**。该特征位于独立权利要求的“特征部分” (Characterizing Portion)，是解决核心技术问题的关键改进。
            -   `false`: **非区别特征**。包括独立权利要求的“前序部分” (Preamble Portion，如通用底座、常规电机)，或所有的从属权利要求特征。

        ### C. 质量自检 (Self-Correction)
        -   在输出前，请对每个 `is_distinguishing=true` 的特征进行**“删减测试”**：如果删掉这个特征，核心技术原理是否还能成立？如果还能成立（例如删掉机架，系统只是没法固定但原理不变），则该特征不是 Distinguishing。

        # 4. 输出 JSON Schema
        {{
            "technical_features": [
                {{ "name": "string", "description": "string", "is_distinguishing": boolean, "claim_source": "independent" | "dependent" }}
            ]
        }}
        """

        return self.llm_service.chat_completion_json(
            model=Settings.LLM_MODEL_REASONING,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=0.0 # 保持零温度，追求最严谨的逻辑
        )
    
    def _verify_evidence(self, core_logic: Dict[str, Any], feature_list: List[Dict]) -> Dict[str, Any]:
        """
        Step 4: 证据挖掘。
        目标：基于已定义的特征，去实施例中找证据，并解释机理。
        """
        logger.debug("Step 4: Mining evidence from embodiments...")

        # 将特征列表序列化，作为约束条件
        features_context = json.dumps(
            [{
                'name': f['name'], 
                'is_distinguishing': f['is_distinguishing'],  # 逻辑核心：是否为区别特征
                'claim_source': f.get('claim_source')         # 定位辅助：来自独权还是从权
            } for f in feature_list], 
            ensure_ascii=False
        )

        system_prompt = (
            "你是一名精通技术验证的法医级分析师。你的任务是将‘声称的技术效果’与‘实施例中的客观证据’进行对账。"
            "你需要解释技术机理，并用数据说话，验证方案的可行性。"
        )

        user_content = f"""
        # 1. 已确定的技术特征 (Input Constraints)
        **注意：在后续分析中，你提到的所有特征名称必须严格取自下表，禁止自造新词。**
        {features_context}

        # 2. 审查材料
        【待验证的技术效果】: {self.text_effect}
        【具体实施方式 (证据库)】: 
        ***重点阅读区域***
        {self.text_details[:8000]} 

        # 3. 分析任务
        ### A. 技术机理深度解析 (technical_means)
        - **问题**：Why it works? 
        - **核心要求**：
          1. 请撰写一段连贯的、符合工程逻辑的段落，解释**区别技术特征 (即列表中 is_distinguishing为true 的项)** 是如何通过物理原理（力学、热学、电磁学等）协同工作，从而解决“{core_logic.get('technical_problem')}”的。
          2. **负向约束 (CRITICAL)**：生成的文本必须是自然的工程技术语言。**严禁在正文中出现 "is_distinguishing=true"、"claim_source"、"JSON" 或 "布尔值" 等代码层面的变量名称。
        - **注意**：对于前序部分（Preamble）的通用部件，除非它们参与了核心互动，否则无需详细解释。
        - **风格**：逻辑严密，避免空洞的套话。

        ### B. 效果归因与证据核查 (technical_effects)
        - **核心逻辑**：Effect -> Caused by Feature -> Proven by Evidence.
        - **排序规则 (CRITICAL)**：
          必须严格按照**重要性递减**排序：
          1.  **第一梯队（核心创新）**：由 `is_distinguishing=true` 的特征带来的效果。这是证明创造性的关键。
          2.  **第二梯队（具体优化）**：由 `claim_source="dependent"` 的从权特征带来的进一步有益效果。
          3.  **第三梯队（基础功能）**：由 `is_distinguishing=false` 且 `claim_source="independent"` (前序特征) 带来的基础效果。
        
        - **字段填写规范**：
          - `effect`: 效果描述（如“降低了测试数据的方差”）。
          - `source_feature_name`: **必须**完全匹配上述“已确定的技术特征”中的 `name`。
          - `feature_type`: **严格基于输入数据判断**，禁止主观臆断。判断逻辑如下：
            - 如果 `is_distinguishing` 为 true，填 "Distinguishing Feature" (区别特征)。
            - 如果 `is_distinguishing` 为 false 且 `claim_source` 为 "independent"，填 "Preamble Feature" (前序特征)。
            - 如果 `claim_source` 为 "dependent"，填 "Dependent Feature" (从权特征)。
          - `evidence`: **证据提取**。
            - *优选*：定量数据（如“表2显示磨损量减少了30%”）。
            - *次选*：定性描述（如“实施例中提到，相比于图1的现有技术，震动明显减弱”）。
            - *若无*：填“仅有定性描述，无具体实验数据”。

        # 4. 输出 JSON Schema
        {{
            "technical_means": "string",
            "technical_effects": [
                {{ "effect": "string", "source_feature_name": "string", "feature_type": "Distinguishing Feature" | "Preamble Feature" | "Dependent Feature", "evidence": "string" }}
            ]
        }}
        """

        result = self.llm_service.chat_completion_json(
            model=Settings.LLM_MODEL_REASONING,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
            temperature=0.1
        )

        type_priority = {
            "Distinguishing Feature": 3, # 最重要
            "Dependent Feature": 2,      # 其次（因为可能含有具体的优选参数）
            "Preamble Feature": 1        # 最不重要
        }

        result["technical_effects"].sort(
            key=lambda x: (
                type_priority.get(x.get("feature_type"), 0), 
                len(x.get("evidence", ""))
            ), 
            reverse=True
        )
        
        return result
        
    def _generate_figures_analysis(self, global_context: Dict) -> List[Dict[str, Any]]:
        """
        Step 3: 图解生成。
        遍历 drawings，为每一张图生成：AI解说 + 结构化部件表。
        """
        results = []

        # 预处理：建立段落索引，方便查找图片引用的上下文
        paragraphs = self.text_details.split("\n")

        # 构建全局部件索引字符串 (用于 Prompt 查阅)
        all_parts_summary = []
        for pid, info in self.parts_db.items():
            name = info.get('name', '未知部件')
            all_parts_summary.append(f"{pid}: {name}")
        global_parts_str = "\n".join(all_parts_summary)

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

            # 构建当前图片的局部部件上下文 (用于识别图里有什么)
            local_parts_context_str = ""
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
                local_parts_context_str = "\n".join(temp_desc_list)
            else:
                local_parts_context_str = "（该图未识别到具体部件标号）"

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
                label, caption, local_parts_context_str, global_parts_str, related_text, global_context, image_abs_path
            )

            # 4. 存入结果
            results.append({
                "image_path": file_path.replace('images', 'annotated_images'),
                "image_title": f"{label} {caption}".strip(),
                "image_explanation": image_explanation,
                "parts_info": parts_table_data # 结构化部件数据（可能为空列表）
            })

        return results

    def _generate_single_figure_caption(self, label: str, caption: str, local_parts: str, global_parts: str, text_context: str, global_context: Dict, image_path: str) -> str:
        """
        生成单张图片的“看图说话”
        """

        effects_str = "\n".join([f"- {e['effect']}: {e['source_feature_name']}" for e in global_context.get('effects', [])[:3]])

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
          {local_parts}
          **注意：机器识别可能存在漏检。如果图中有明显的核心结构（如画面中心的连接件、受力件）未在此列表中，请务必结合下方的【全局部件参考库】进行逻辑推断和补全。**
        - **局部描述 (Context)**：
          {text_context}
        - **全局部件参考库 (Global Reference)**：
          如果在解释逻辑时需要提到**图中未标注、未画出或漏检**的关联部件，请在此表中查找其标准名称和标号。
          {global_parts}

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
        - **引用标准**：
          提及**任何**部件时，必须严格遵循 `名称(标号)` 格式。
          - **情况A (明确标记)**：直接使用【视觉线索】中的信息。
          - **情况B (逻辑补全)**：若核心部件在图中清晰可见但未被机器识别（无标号），或者在逻辑链中必不可少（即使未画出），**必须**查阅【全局部件参考库】并正确标注其标准标号。
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
            return f"图{label}展示了{caption}的示意图。{local_parts.replace('- ', '')[:50]}..."
