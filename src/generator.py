import os
import re
import json
from pathlib import Path
from typing import Dict, List, Any
from loguru import logger
from src.utils.llm import get_llm_service
from src.utils.cache import StepCache
from config import Settings

class ContentGenerator:
    def __init__(self, patent_data: Dict, parts_db: Dict, image_parts: Dict, annotated_dir: Path, cache_file: Path = None):
        self.llm_service = get_llm_service()
        self.parts_db = parts_db
        self.image_parts = image_parts
        self.annotated_dir = annotated_dir

        # 初始化缓存管理器
        self.cache = StepCache(cache_file) if cache_file else None

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
    
    def generate_report_json(self) -> Dict[str, Any]:
        """
        执行专利逻辑分析流水线。
        Flow: [Domain/Problem] -> [Solution/Title] -> [Features] -> [Verification] -> [Visuals]
        """
        logger.info(f"开始生成专利分析报告: {self.biblio.get('application_number', 'Unknown')}")

        try:
            # === Step 1: 领域定位与问题定义 ===
            # 输入：背景技术、技术效果
            # 输出：technical_field, technical_problem
            domain_problem_data = self.cache.run_step(
                "step1_domain_problem", 
                self._analyze_domain_and_problem
            )
            
            # === Step 2: 解决方案封装 ===
            # 输入：Step 1的结果、独立权利要求、发明内容
            # 输出：ai_title, ai_abstract, technical_scheme
            solution_data = self.cache.run_step(
                "step2_solution", 
                self._synthesize_solution_package, 
                domain_problem_data
            )
            
            # 合并核心逻辑数据 (Core Logic Context)
            core_logic = {**domain_problem_data, **solution_data}

            # === Step 3: 权利要求解构与特征定义 ===
            # 输入：核心逻辑、全部权利要求
            # 输出：technical_features
            features_data = self.cache.run_step(
                "step3_features", 
                self._extract_features, 
                core_logic
            )
            feature_list = features_data.get("technical_features", [])

            # === Step 4: 实施例取证与效果验证 ===
            # 输入：核心逻辑、技术特征、具体实施方式
            # 输出：technical_means, technical_effects
            verification_data = self.cache.run_step(
                "step4_verification", 
                self._verify_evidence, 
                core_logic, 
                feature_list
            )

            # 图解生成 (遍历每一张图，生成解说和部件表)
            global_context = {
                "title": solution_data.get("ai_title"),
                "problem": domain_problem_data.get("technical_problem"),
                "effects": verification_data.get("technical_effects", [])
            }
            figures_data = self.cache.run_step(
                "step5_figures", 
                self._generate_figures_analysis, 
                global_context
            )
            
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
                "technical_features": features_data.get("technical_features", []),      # 技术特征
                "technical_effects": verification_data.get("technical_effects", []),        # 技术效果
                
                # 图解详细信息
                "figure_explanations": figures_data
            }
            
            logger.success("专利分析 JSON 生成完成")
            return final_report

        except Exception as e:
            logger.error(f"生成报告过程中发生错误: {str(e)}")
            return {"error": str(e), "status": "failed"}
        
    def _format_claims_to_text(self, only_independent: bool = False) -> str:
        """
        通用辅助函数：将权利要求列表格式化为层级分明的 Markdown 文本。
        适配 claim_text/claim_type 结构。
        """
        lines = []
        # 自动生成编号 (如果原始数据没有 id 字段)
        for idx, claim in enumerate(self.claims):
            # 1. 类型过滤
            c_type_raw = claim.get('claim_type', 'dependent').lower()
            is_indep = 'independent' in c_type_raw
            
            if only_independent and not is_indep:
                continue

            # 2. 获取内容 (兼容不同字段名)
            content = claim.get('claim_text') or claim.get('content') or ""
            
            # 3. 构建标题
            # 假设 idx+1 为权利要求编号，实际项目中建议尽量使用原始编号
            claim_id = claim.get('id', str(idx + 1))
            type_label = "独立权利要求 (Independent)" if is_indep else "从属权利要求 (Dependent)"
            
            lines.append(f"### Claim {claim_id} [{type_label}]")
            lines.append(content.strip())
            lines.append("---")
            
        return "\n".join(lines)
        
    def _analyze_domain_and_problem(self) -> Dict[str, str]:
        """
        Step 1: 定位领域与定义问题。
        关键点：只看“过去(Background)”和“目标(Effect)”，不看“现在(Claims)”，以确保问题的客观性。
        """
        logger.debug("Step 1: Analyzing Domain and Problem...")

        system_prompt = """
        你是一名资深专利审查员。任务是基于‘问题-解决方案’方法（Problem-Solution Approach），
        从现有技术的缺陷中反推客观技术问题。

        # 任务说明
        ### 1. 领域与问题分析
        请根据用户提供的专利数据（IPC、领域描述、背景技术、有益效果），完成以下分析：

        #### A. 标准化技术领域 (technical_field)
        - **指令**：结合 IPC 代码的含义和文本描述，修正为标准的 IPC 分类层级术语。
        - **格式**：[一级行业] - [二级核心技术]。
        - **示例**:
            - *Bad*: 本发明涉及一种用于家庭烹饪的装置。
            - *Good*: 消费电子 - 智能温控烹饪设备
            - *Bad*: 一种基于深度学习的图像处理方法。
            - *Good*: 计算机视觉 - 语义分割算法

        #### B. 客观技术问题 (technical_problem)
        - **逻辑链**：
            1. 目标是什么？(看有益效果)
            2. 为什么现有技术做不到？(看背景技术中的痛点，如：计算量大、结构干涉、材料不耐腐蚀)
            3. **定义问题**：本申请要解决的核心障碍是什么？
        - **避坑指南**：
            - **禁止**出现“本发明通过...”字样（只看过去和目标，不看现在的方案）。
            - **必须**具体化。不要说“效率低”，要说“因采用串行处理架构导致的数据吞吐效率低”。

        # 输出要求
        1. 严格遵守 JSON 格式输出。
        2. 不要输出任何 Markdown 标记（如 ```json），只输出纯 JSON 字符串。

        # JSON Schema
        {
            "technical_field": "string",
            "technical_problem": "string"
        }

        # 待分析数据如下：
        """

        user_content = f"""
        【IPC 分类号】: {json.dumps(self.list_ipc, ensure_ascii=False)}
        *(请参考 IPC 代码含义来校准技术领域)*
        
        【原始领域描述】
        {self.text_field}

        【背景技术 (现有技术的缺陷)】
        {self.text_background[:3000] if self.text_background else "（未提供详细背景，请基于常识和下文目标反推）"}

        【预期达到的有益效果 (Target)】
        {self.text_effect if self.text_effect else "（未明确列出，需推断）"}
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

        # 筛选出独立权利要求并格式化为 Markdown
        indep_claims_text = self._format_claims_to_text(only_independent=True)

        system_prompt = """
        你是一名顶级科技期刊的编辑。你的任务是将晦涩的法律文档（专利权利要求）重写为高信息密度的技术摘要和工程方案。
        你的读者是该领域的技术专家，不要使用法律套话。

        # 任务说明
        请利用用户提供的【前序分析结论】和【核心素材】，完成以下三项撰写任务：

        ### A. 核心技术标题 (ai_title)
        - **指令**：参考原发明名称，生成一个精准的技术主题词。
        - **公式**：[核心算法/结构] + [动词] + [应用场景]。
        - **示例 (Few-Shot)**:
            - *Original*: 一种用于风力发电机的叶片故障检测方法
            - *Refined*: 基于多尺度时频分析的风机叶片早期裂纹监测
            - *Original*: 一种显示装置
            - *Refined*: 采用Micro-LED阵列的高动态范围(HDR)显示驱动架构

        ### B. 智能摘要 (ai_abstract)
        - **任务**：撰写一段 300字左右 的高密度摘要。
        - **结构要求**：
            1. **领域限定** (1句): "本发明涉及[输入数据中的技术领域]领域。" (请直接引用输入数据)
            2. **背景切入** (1句): "针对[...的问题]，本方案..." (参考输入数据中的技术问题)
            3. **技术内核** (3-4句): 提取独权步骤。**关键约束**：如果文中包含具体的**数字参数**（温度、比例、阈值公式）、**特定材料**或**算法名称**，必须保留！这是技术专家的关注点。
            4. **性能闭环** (1句): "最终实现了[...的具体指标提升]。"
        - **负向约束 (Negative Constraints)**: 禁止使用 "所述"、"其特征在于" 等法律词汇。

        ### C. 技术方案概要 (technical_scheme)
        - **任务**：将独立权利要求翻译为“工程实施手册”。
        - **多独权处理逻辑**：
            - 输入数据中每个以 `### Claim X` 开头的区块代表一个独立的技术方案。
            - 如果存在多个 `### Claim` 区块，（例如 Claim 1 是装置，Claim 7 是方法），必须分条目描述。
            - **格式示例**：
                "1) 装置主体：由加载块和环带组成，加载块通过...；\n2) 计算方法：建立柔轮与轴承的几何模型，通过CAE分析求解..."
        - **逻辑重组**：如果是方法，按步骤描述；如果是装置，按连接关系描述。
        - **改写示例**:
            - *Claim Input*: "一种装置，其特征在于包括部件A，所述部件A连接部件B..."
            - *Output*: "系统主体由部件A和部件B构成。部件A通过法兰与部件B刚性连接，起支撑作用..." (转化为描述性语言)

        # 输出要求
        1. 必须严格遵守 JSON 格式。
        2. 不要输出 Markdown 标记。

        # JSON Schema
        {
            "ai_title": "string",
            "ai_abstract": "string",
            "technical_scheme": "string"
        }
        """

        # Context 严选：只提供解决问题所需的 独权 和 Summary
        user_content = f"""
        # 1. 前序分析结论
        【已确定的技术领域】: {problem_context.get('technical_field')}
        【待解决的技术问题】: {problem_context.get('technical_problem')}

        # 2. 核心素材
        【原发明名称 (Original Title)】
        {self.text_title}

        【独立权利要求 (Independent Claims)】
        *** 请基于以下文本提取核心技术方案 ***
        {indep_claims_text}

        【发明内容 (Summary of Invention)】
        {self.text_summary[:3000]}

        【原始摘要 (Reference)】
        {self.text_abstract}
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

        # 1. 格式化权利要求 (比 JSON dump 更易于语义理解)
        claims_text = self._format_claims_to_text()

        system_prompt = """
        你是一名资深的专利实质审查员（Patent Examiner）。
        你的核心任务是基于‘三步法’（Problem-Solution Approach）对权利要求书进行解构。
        你需要精准识别出哪些是现有的‘前序特征’，哪些是真正承载发明点的‘区别技术特征’。

        # 分析指令 (Step-by-Step Reasoning)
        请严格遵循以下思维路径对用户提供的【审查背景】和【权利要求书】进行分析：

        ### Step 1: 锁定独立权利要求 (Scope Definition)
        - 找到编号最小的独立权利要求（通常是 Claim 1）。
        - **范围界定**：以“其特征在于/characterized in that”为界。
            - 之前的特征默认为“前序特征”（除非该特征显然是本发明的核心改进点，属于撰写错误）。
            - 之后的特征默认为“特征部分”。
        - **扩展扫描**：快速扫描从属权利要求。如果从属权中出现了**对核心技术问题有重大贡献**的具体参数、算法或结构细节，也应将其提取为独立特征。

        ### Step 2: 粒度拆解与标准化 (Granularity & Normalization)
        - 将长句拆解为独立的技术手段（Feature）。
        - **命名规范**：使用标准工程术语（如用“第一传动齿轮”代替“那个转动的轮子”）。
        - **合并同类项**：如果 Claim 1 定义了上位概念（如“紧固件”），Claim 2 定义了下位概念（如“螺栓”），**优先保留下位概念**（如果它是解决问题的关键），或者保留上位概念并在描述中备注。

        ### Step 3: 属性判定 (Classification)
        对每个提取的特征进行分类：
        - **is_distinguishing (是否区别特征)**:
            - 判定标准：
                - 判据 A：通常位于独立权利要求的“特征部分”。
                - 判据 B（核心）：该特征是否**技术性地**贡献于解决【待解决的技术问题】？
            - 范围修正：如果是从属权利要求中的新增特征，且解决了进一步的技术问题，也应标记为 True。
            - **注意**：如果一个特征虽然在“特征部分”，但仅是通用的连接件或支撑件（如“普通的螺丝”、“常规的电源线”、“外壳”、“机架”），且未做特殊限定，通常标记为 False。
        - **claim_source (来源)**:
            - 如果特征首次出现在独立权 -> "independent"。
            - 如果特征仅作为对独立权的进一步限定出现在从属权 -> "dependent"。

        ### Step 4: 负向过滤 (Negative Filter)
        - 保留功能性限定结构（如“用于...的数据处理模块”、“配置为...的钩爪”）。这些是合法的技术特征。
        - 剔除非技术性的效果描述（如“为了通过测试”、“为了降低成本”），只保留“结构/步骤”。
        - 剔除无创造性的通用连接词（如“通过螺丝固定”）。

        # 输出要求
        1. 严格遵守 JSON 格式。
        2. 在 `rationale` 字段中简要说明为什么你将其判定为 True 或 False。

        # JSON Schema
        {
            "technical_features": [
                {
                    "name": "特征名称",
                    "description": "原文中的定义描述",
                    "is_distinguishing": true,
                    "claim_source": "independent",
                    "rationale": "简短的判定理由，辅助逻辑自检"
                }
            ]
        }
        """

        user_content = f"""
        # 1. 审查背景 (Examination Context)
        【待解决的技术问题】: {core_logic.get('technical_problem')}
        【技术方案核心】: {core_logic.get('technical_scheme')}

        # 2. 权利要求书 (Claims Text)
        {claims_text}
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

        # --- 将特征列表格式化为 Markdown 表格，便于 LLM 阅读 ---
        header = "| 特征名称 (name) | 是否区别特征 | 权利要求来源 |"
        separator = "| :--- | :--- | :--- |"
        rows = []
        for f in feature_list:
            # 防止名称中包含 '|' 破坏表格结构
            safe_name = str(f.get('name', '')).replace('|', '\\|')
            # 确保布尔值转为字符串显示，或者使用 'True'/'False'
            is_dist = str(f.get('is_distinguishing', False))
            claim_src = str(f.get('claim_source', ''))
            rows.append(f"| {safe_name} | {is_dist} | {claim_src} |")
        
        features_context = "\n".join([header, separator] + rows)
        # -----------------------------------------------------------

        system_prompt = """
        你是一名精通技术验证的法医级分析师。你的任务是将‘声称的技术效果’与‘实施例中的客观证据’进行对账。
        你需要解释技术机理，并用数据说话，验证方案的可行性。

        # 分析任务 (Analysis Tasks)

        ### A. 技术机理深度解析 (technical_means)
        - **目标**：解释 Why it works?
        - **核心要求**：
            1. 请撰写一段连贯的、符合工程逻辑的段落，解释**区别技术特征**是如何通过物理原理（力学、热学、电磁学等）协同工作，从而解决输入数据中指定的【待解决的技术问题】的。
            2. **识别逻辑**：请关注表格中 **“是否区别特征”列为 True** 的那些特征。
        - **注意**：对于前序部分（Preamble）的通用部件，除非它们参与了核心互动，否则无需详细解释。
        - **风格**：逻辑严密，避免空洞的套话。

        ### B. 效果归因与证据核查 (technical_effects)
        - **核心逻辑**：Effect -> Caused by Feature -> Proven by Evidence.
        - **排序规则 (CRITICAL)**：
            必须严格按照**重要性递减**排序：
            1.  **第一梯队（核心创新）**：表格中 **“是否区别特征”列为 True** 的特征带来的效果。这是证明创造性的关键。
            2.  **第二梯队（具体优化）**：表格中 **“权利要求来源”列为 'dependent'** 的特征带来的进一步有益效果。
            3.  **第三梯队（基础功能）**：表格中 **“是否区别特征”列为 False** 且 **“权利要求来源”列为 'independent'** (前序特征) 带来的基础效果。

        - **字段填写规范**：
        - `effect`: 效果描述（如“降低了测试数据的方差”）。
        - `source_feature_name`: **必须**完全匹配输入表格第一列的 `name`，禁止自造新词。
        - `feature_type`: **严格基于表格列值判断**，禁止主观臆断。判断逻辑如下：
            - 若 “是否区别特征”列为 `True` -> 填 "Distinguishing Feature" (区别特征)。
            - 若 “是否区别特征”列为 `False` 且 “权利要求来源”列为 `independent` -> 填 "Preamble Feature" (前序特征)。
            - 若 “权利要求来源”列为 `dependent` -> 填 "Dependent Feature" (从权特征)。
        - `evidence`: **证据提取**。
            - *优选*：定量数据（如“表2显示磨损量减少了30%”）。
            - *次选*：定性描述（如“实施例中提到，相比于图1的现有技术，震动明显减弱”）。
            - *若无*：填“仅有定性描述，无具体实验数据”。

        # 输出要求
        1. 严格遵守 JSON 格式。
        2. 不输出 Markdown 标记。

        # JSON Schema
        {
            "technical_means": "string",
            "technical_effects": [
                { 
                    "effect": "string", 
                    "source_feature_name": "string", 
                    "feature_type": "Distinguishing Feature" | "Preamble Feature" | "Dependent Feature", 
                    "evidence": "string" 
                }
            ]
        }
        """

        user_content = f"""
        # 1. 分析背景 (Context)
        【待解决的技术问题】: {core_logic.get('technical_problem')}
        【待验证的技术效果】: {self.text_effect}

        # 2. 特征列表约束 (Constraints)
        **注意：后续分析必须严格基于下表中的“特征名称”列：**
        {features_context}

        # 3. 证据库 (Source Material)
        【具体实施方式 (Description of Embodiments)】: 
        *** 请在以下文本中寻找证据 ***
        {self.text_details[:8000]} 
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

        # 容错：防止 LLM 返回非预期结构
        effects_list = result.get("technical_effects", [])
        if not isinstance(effects_list, list):
            effects_list = []

        effects_list.sort(
            key=lambda x: (
                type_priority.get(x.get("feature_type"), 0), 
                len(x.get("evidence", ""))
            ), 
            reverse=True
        )

        result["technical_effects"] = effects_list
        
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

                # 去重并升序排列
                part_ids = sorted(set(part_ids))

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

        system_prompt = """
        # 角色设定
        你是一位顶尖的专利可视化分析师。你的受众是**非本技术领域的专利审查员**。
        审查员面临大量的阅读压力，你的任务是**降低他们的认知负荷**，通过一段通俗、逻辑严密的解说，让他们一眼看懂这张图的核心含义。

        # 思考步骤 (请在内心执行，无需输出)
        1. **锚点对齐**：首先锁定图中的【视觉线索】，确认关键部件在图中的具体位置，构建空间坐标系。
        2. **意图解码**：结合【全局认知】，问自己：申请人为什么要放这张图？
            - *如果是结构图：* 哪个部件是解决【客观问题】的“金钥匙”？
            - *如果是数据图：* 曲线的哪个“拐点”或“极值”证明了【预期效果】？
        3. **逻辑串联**：用一条看不见的线（力流、数据流、时间流）将静态部件串联起来。

        # 生成策略 (根据图片类型选择)
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

        # 写作规范 (必须严格遵守)
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

        # 输出指令
        直接输出最终的解说段落，不要包含任何标题、Markdown标记或“好的”等客套话。
        """

        user_content = f"""
        # 1. 全局认知 (Global Knowledge - Shared Context)
        在分析图片前，请先理解本专利的核心逻辑，这将作为你**解读图片意图的唯一指引**：
        - **核心发明点**：{global_context.get('title')}
        - **要解决的客观问题**：{global_context.get('problem')}
        - **预期达到的效果**：
        {effects_str}
        - **全局部件参考库 (Global Reference)**：
        如果在解释逻辑时需要提到**图中未标注、未画出或漏检**的关联部件，请在此表中查找其标准名称和标号。
        {global_parts}

        # 2. 本图输入数据 (Specific Image Data)
        - **图号与标题**：{label} - {caption}
        - **视觉线索 (Visual Clues)**：
          图片经过了机器视觉标注，请将图中的**显眼文字（蓝色/红色文本）、数字标号或指示箭头**视为最高优先级的锚点。
          这些视觉标记对应以下部件信息（未在图中明确指出的部件请勿强行描述）：
          {local_parts}
          **注意：机器识别可能存在漏检。如果图中有明显的核心结构（如画面中心的连接件、受力件）未在此列表中，请务必结合上方的【全局部件参考库】进行逻辑推断和补全。**
        - **局部描述 (Context)**：
          {text_context}
        """

        try:
            logger.info(f"正在进行视觉思考分析: {label} ({os.path.basename(image_path)})")

            content = self.llm_service.analyze_image_with_thinking(
                image_path=image_path,
                system_prompt=system_prompt,
                user_prompt=user_content
            )
            return content.strip()
        except Exception as e:
            logger.warning(f"图片解说生成失败 {label}: {e}")
            return f"图{label}展示了{caption}的示意图。{local_parts.replace('- ', '')[:50]}..."
