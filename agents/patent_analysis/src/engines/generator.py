import os
import re
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from loguru import logger
from config import settings
from agents.common.utils.concurrency import submit_with_current_context
from agents.common.utils.llm import get_llm_service
from agents.common.utils.cache import StepCache


class ContentGenerator:
    LOGIC_PARALLEL_WORKERS = 2

    def __init__(
        self,
        patent_data: Dict,
        parts_db: Dict,
        image_parts: Dict,
        annotated_dir: Path,
        cache_file: Path = None,
    ):
        self.llm_service = get_llm_service()
        self.parts_db = parts_db
        self.image_parts = image_parts
        self.annotated_dir = annotated_dir
        self.figure_parallel_workers = max(
            1, int(getattr(settings, "VLM_MAX_WORKERS", 4) or 4)
        )

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
        self.text_effect = self.description.get("technical_effect", "")
        self.list_ipc = self.biblio.get("ipc_classifications", [])

        # 2. 用于概括方案：看摘要、发明内容
        self.text_title = self.biblio.get("invention_title", "")
        self.text_abstract = self.biblio.get("abstract", "")
        self.text_summary = self.description.get("summary_of_invention", "")

        # 3. 用于验证细节：看具体实施方式
        self.text_details = self.description.get("detailed_description", "")

    def _run_cached(
        self,
        key: str,
        func,
        *args,
        **kwargs,
    ):
        if not self.cache:
            return func(*args, **kwargs)
        return self.cache.run_step(key, func, *args, **kwargs)

    def _compose_core_report(
        self,
        domain_problem_data: Dict[str, Any],
        solution_data: Dict[str, Any],
        background_data: Dict[str, Any],
        features_data: Dict[str, Any],
        verification_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        main_fig = self.biblio.get("abstract_figure")
        if not main_fig and self.drawings:
            main_fig = self.drawings[0].get("file_path")

        return {
            "ai_title": solution_data.get("ai_title", self.biblio.get("invention_title")),
            "ai_abstract": solution_data.get("ai_abstract", self.biblio.get("abstract", "")),
            "abstract_figure": (
                main_fig.replace("images", "annotated_images") if main_fig else None
            ),
            "technical_field": domain_problem_data.get("technical_field"),
            "claim_subject_matter": features_data.get("claim_subject_matter", ""),
            "technical_problem": domain_problem_data.get("technical_problem"),
            "technical_scheme": solution_data.get("technical_scheme"),
            "technical_means": verification_data.get("technical_means"),
            "background_knowledge": background_data.get("background_knowledge", []),
            "technical_features": features_data.get("technical_features", []),
            "technical_effects": verification_data.get("technical_effects", []),
        }

    def _build_global_context(self, report_core_json: Dict[str, Any]) -> Dict[str, Any]:
        feature_list = report_core_json.get("technical_features", [])
        if not isinstance(feature_list, list):
            feature_list = []
        feature_tree_str = (
            self._build_feature_menu_str(feature_list)
            if feature_list
            else "（未提取到技术特征）"
        )

        return {
            "title": report_core_json.get("ai_title"),
            "problem": report_core_json.get("technical_problem"),
            "effects": report_core_json.get("technical_effects", []),
            "feature_tree": feature_tree_str,
            "raw_features": feature_list,
        }

    def generate_core_report_json(self) -> Dict[str, Any]:
        """
        生成不包含附图讲解的核心分析结果。
        """
        logger.info(
            f"开始生成 AI 分析核心报告: {self.biblio.get('application_number', '未知')}"
        )

        try:
            domain_problem_data = self._run_cached(
                "domain_problem",
                self._analyze_domain_and_problem,
            )

            solution_data = self._run_cached(
                "solution_package",
                self._synthesize_solution_package,
                domain_problem_data,
            )

            core_logic = {**domain_problem_data, **solution_data}

            with ThreadPoolExecutor(
                max_workers=self.LOGIC_PARALLEL_WORKERS,
                thread_name_prefix="report",
            ) as executor:
                future_background = submit_with_current_context(
                    executor,
                    self._run_cached,
                    "background_knowledge",
                    self._generate_background_knowledge,
                    core_logic,
                )
                future_features = submit_with_current_context(
                    executor,
                    self._run_cached,
                    "features",
                    self._extract_features,
                    core_logic,
                )
                background_data = future_background.result()
                features_data = future_features.result()

            feature_list = features_data.get("technical_features", [])
            verification_data = self._verify_evidence(
                core_logic,
                feature_list,
            )

            final_report = self._compose_core_report(
                domain_problem_data=domain_problem_data,
                solution_data=solution_data,
                background_data=background_data,
                features_data=features_data,
                verification_data=verification_data,
            )

            logger.success("AI 分析核心 JSON 生成完成")
            return final_report

        except Exception as e:
            logger.error(f"生成核心报告过程中发生错误: {str(e)}")
            return {"error": str(e), "status": "failed"}

    def generate_figure_explanations(
        self,
        report_core_json: Optional[Dict[str, Any]] = None,
        global_context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        仅生成附图讲解数据。
        """
        context = global_context or self._build_global_context(report_core_json or {})
        return self._run_cached(
            "figures_analysis",
            self._generate_figures_analysis,
            context,
        )

    def generate_report_json(self) -> Dict[str, Any]:
        """
        兼容入口：先生成核心报告，再补全附图讲解。
        """
        report_core_json = self.generate_core_report_json()
        if report_core_json.get("status") == "failed":
            return report_core_json

        try:
            figures_data = self.generate_figure_explanations(report_core_json)
            final_report = dict(report_core_json)
            final_report["figure_explanations"] = figures_data
            logger.success("AI 分析 JSON 生成完成")
            return final_report
        except Exception as e:
            logger.error(f"生成附图讲解过程中发生错误: {str(e)}")
            return {"error": str(e), "status": "failed"}

    def _format_claims_to_text(self, only_independent: bool = False) -> str:
        """
        通用辅助函数：将权利要求列表格式化为层级分明的 Markdown 文本。
        适配 claim_text/claim_type/parent_claim_ids 结构。
        """
        lines = []
        for idx, claim in enumerate(self.claims):
            c_type_raw = str(claim.get("claim_type", "dependent")).lower()
            is_indep = "independent" in c_type_raw

            if only_independent and not is_indep:
                continue

            content = claim.get("claim_text") or claim.get("content") or ""
            claim_id = str(claim.get("claim_id", "")).strip() or str(idx + 1)

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

            if is_indep:
                type_label = "独立权利要求 (Independent)"
            else:
                parents_str = ", ".join(parent_ids) if parent_ids else "未知"
                type_label = f"从属权利要求 (Dependent, 引用 Claim {parents_str})"

            lines.append(f"### Claim {claim_id} [{type_label}]")
            lines.append(content.strip())
            lines.append("---")

        return "\n".join(lines)

    def _analyze_domain_and_problem(self) -> Dict[str, str]:
        """
        定位领域并定义问题。
        """
        logger.debug("正在分析技术领域与技术问题...")

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

        return self.llm_service.invoke_text_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            task_kind="core_summary_generation",
            temperature=0.1,
        )

    def _synthesize_solution_package(
        self, problem_context: Dict[str, str]
    ) -> Dict[str, str]:
        """
        封装解决方案。
        """
        logger.debug("正在封装解决方案...")

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

        return self.llm_service.invoke_text_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            task_kind="core_summary_generation",
            temperature=0.1,
        )

    def _generate_background_knowledge(
        self, core_logic: Dict[str, str]
    ) -> Dict[str, List[Dict[str, str]]]:
        """
        生成背景知识与术语解释。
        """
        logger.debug("正在生成背景知识...")

        system_prompt = """
        你是一名 **科学传播专家** (Science Communicator)，专门负责协助非本领域的专利审查员理解复杂的前沿技术。
        你的任务是扫描专利文本，识别出阻碍理解的“核心概念”、“生僻术语”或“缩写”，并提供**通俗易懂（Explain Like I'm 5）**的解释。

        # 任务说明
        1. **筛选词条**：
           - 请从提供的【专利内容】中，挑选 **3-6个** 最关键的、非大众通用的技术名词。
           - **优先选择**：具体的算法名称（如“卡尔曼滤波”）、特定材料（如“共晶焊料”）、行业缩写（如“MIMO”、“IGBT”）或特定物理效应。
           - **剔除**：过于基础的词汇（如“电脑”、“螺丝”、“互联网”）或纯法律词汇（如“实施例”）。

        2. **撰写解释**：
           - **Definition (定义)**：用一句话准确定义该术语。
           - **Analogy (通俗类比)**：**这是必须的**。使用生活中的例子来打比方。
           - **Context (本案联系)**：简要说明该术语在本专利中起什么作用。

        # 示例 (Few-Shot)
        *   **术语**: PID控制
            *   **定义**: 比例-积分-微分控制器，一种通用的工业控制算法。
            *   **类比**: 就像开车控制油门，既看当前速度(P)，也看过去累积的误差(I)，还预测未来的趋势(D)，以此保持车速平稳。
            *   **本案联系**: 用于控制加热器的温度恒定。
        
        *   **术语**: 卷积神经网络 (CNN)
            *   **定义**: 一种专门处理网格数据（如图像）的深度学习模型。
            *   **类比**: 就像通过一个小窗口（卷积核）在图片上滑动，提取局部特征（如边缘、纹理），最后拼凑出整体图案。
            *   **本案联系**: 用于识别监控画面中的异常入侵行为。

        # 输出要求
        1. 严格遵守 JSON 格式。
        2. 不要输出 Markdown 标记。
        3. 确保 content 不为空。

        # JSON Schema
        {
            "background_knowledge": [
                {
                    "term": "术语名称",
                    "definition": "学术定义",
                    "analogy": "生活化类比 (必填)",
                    "context_in_patent": "在本专利中的具体用途"
                }
            ]
        }
        """

        # 准备上下文：不仅需要核心逻辑，还需要一些具体的描述文本来挖掘术语
        # 截取 description 的前 5000 字符，通常包含背景和术语定义
        context_text = self.description.get("detailed_description", "")[:5000]
        if not context_text:
            context_text = self.description.get("summary_of_invention", "")

        user_content = f"""
        【技术领域】: {core_logic.get('technical_field')}
        【核心方案】: {core_logic.get('technical_scheme')}
        
        【待扫描的专利文本片段】:
        {context_text}
        
        【权利要求片段 (寻找核心名词)】:
        {self._format_claims_to_text(only_independent=True)}
        """

        return self.llm_service.invoke_text_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            task_kind="core_summary_generation",
            temperature=0.3,  # 稍微增加一点创造性，以便生成生动的类比
        )

    def _extract_features(self, core_logic: Dict[str, Any]) -> Dict[str, Any]:
        """
        权利要求分析。
        """
        logger.debug("正在分析权利要求结构...")

        # 1. 格式化权利要求 (比 JSON dump 更易于语义理解)
        claims_text = self._format_claims_to_text()

        system_prompt = """
        你是一名资深的专利实质审查员（Patent Examiner）。
        你的核心任务是基于‘三步法’（Problem-Solution Approach）对权利要求书进行解构。
        你需要精准识别出权利要求的“保护主题”，并区分哪些是现有的‘前序特征’，哪些是真正承载发明点的‘区别技术特征’。

        # 分析指令 (Step-by-Step Reasoning)
        请严格遵循以下思维路径对用户提供的【审查背景】和【权利要求书】进行分析：

        ### Step 1: 提取保护主题 (Subject Matter Extraction)
        - **目标**：从独立权利要求（通常是 Claim 1）的开头，提取该发明保护的核心产品或方法名称。
        - **位置**：通常位于 "一种..." 之后，"其特征在于..." 之前。
        - **处理规则**：
            1. 去除通用的量词前缀（如“一种”）。
            2. 保留完整的技术定语（如“矿用救援WSN节点...”）。
        - **对应关系**：这是检索策略中的 **Block A (核心产品)**。
        - **示例**：
            - 原文："一种基于深度学习的图像处理方法，其特征在于..." -> 提取："基于深度学习的图像处理方法"
            - 原文："一种矿用救援WSN节点抗灾变性能测试系统，其特征在于..." -> 提取："矿用救援WSN节点抗灾变性能测试系统"

        ### Step 2: 锁定独立权利要求 (Scope Definition)
        - 找到编号最小的独立权利要求（通常是 Claim 1）。
        - **界定边界**：以“其特征在于/characterized in that”为界。
            - 之前的特征默认为“前序特征”（Preamble）。
            - 之后的特征默认为“特征部分”（Characterizing portion）。
        - **扩展扫描**：快速扫描从属权利要求。如果从属权中出现了**对核心技术问题有重大贡献**的具体参数、算法或结构细节，也应将其提取为独立特征。

        ### Step 3: 粒度拆解与标准化 (Granularity & Normalization)
        - **拆解**：将长难句拆解为单一的技术特征（Feature）。
        - **命名规范**：使用标准工程术语（如用“第一传动齿轮”代替“那个转动的轮子”）。
        - **合并同类项**：如果 Claim 1 提到了“电机”，Claim 2 提到了“伺服电机”，且 Claim 2 是对 Claim 1 的具体化，在提取 Claim 1 特征时仅需提取“电机”。

        ### Step 4: 属性判定 (Classification)
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

        ### Step 5: 负向过滤 (Negative Filter)
        - 保留功能性限定结构（如“用于...的数据处理模块”、“配置为...的钩爪”）。这些是合法的技术特征。
        - 剔除非技术性的效果描述（如“为了通过测试”、“为了降低成本”），只保留“结构/步骤”。
        - 剔除无创造性的通用连接词（如“通过螺丝固定”）。

        ### 输出格式 (JSON Only)
        必须严格输出标准的 JSON 对象，**严禁使用 Markdown 代码块 (```json)**，严禁包含任何解释性文字。结构如下：
        {
            "claim_subject_matter": "提取的保护主题名称",
            "technical_features": [
                {
                    "name": "特征名称",
                    "description": "原文中的定义描述",
                    "is_distinguishing": true,
                    "claim_id": "该特征所属的具体权利要求编号（例如 '1', '2'。必须准确提取）",
                    "claim_source": "independent",
                    "rationale": "必须遵循格式：'[权X] <位置标记> - <逻辑判定>'。\n1. [权X]: 指明来源权利要求编号。\n2. <位置标记>: 标记为 '前序部分'、'特征部分' 或 '从属限定'。\n3. <逻辑判定>: 说明该特征是否贡献于解决【待解决的技术问题】，或是否为公知常识。"
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

        response = self.llm_service.invoke_text_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            task_kind="claim_feature_reasoning",
            temperature=0.0,  # 保持零温度，追求最严谨的逻辑
        )

        return response

    def _build_feature_numbered_items(
        self, feature_list: List[Dict]
    ) -> List[Tuple[str, Dict[str, Any]]]:
        features_by_claim: Dict[str, List[Dict[str, Any]]] = {}
        for feature in feature_list:
            if not isinstance(feature, dict):
                continue
            claim_id = str(feature.get("claim_id", "")).strip() or "1"
            features_by_claim.setdefault(claim_id, []).append(feature)

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

        numbered_items: List[Tuple[str, Dict[str, Any]]] = []
        for claim_id in ordered_claim_ids:
            for feature_idx, feature in enumerate(features_by_claim.get(claim_id, []), start=1):
                numbered_items.append((f"{claim_id}.{feature_idx}", feature))
        return numbered_items

    def _build_claim_parents_map(self) -> Dict[str, List[str]]:
        claim_parents_map: Dict[str, List[str]] = {}
        for idx, claim in enumerate(self.claims):
            c_dict = claim.model_dump() if hasattr(claim, "model_dump") else claim
            if not isinstance(c_dict, dict):
                continue
            claim_id = str(c_dict.get("claim_id", "")).strip() or str(idx + 1)
            parent_ids_raw = c_dict.get("parent_claim_ids", [])
            if isinstance(parent_ids_raw, list):
                parent_ids = [
                    str(parent_id).strip()
                    for parent_id in parent_ids_raw
                    if str(parent_id).strip()
                ]
            elif parent_ids_raw:
                parent_ids = [str(parent_ids_raw).strip()]
            else:
                parent_ids = []
            claim_parents_map[claim_id] = parent_ids
        return claim_parents_map

    def _build_feature_menu_str(self, feature_list: List[Dict]) -> str:
        """
        构建带有依赖拓扑关系的特征树上下文。
        """
        claim_parents_map = self._build_claim_parents_map()

        features_by_claim: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
        ordered_claim_ids: List[str] = []
        for feature_no, feature in self._build_feature_numbered_items(feature_list):
            claim_id = str(feature.get("claim_id", "")).strip() or "1"
            if claim_id not in features_by_claim:
                features_by_claim[claim_id] = []
                ordered_claim_ids.append(claim_id)
            features_by_claim[claim_id].append((feature_no, feature))

        feature_tree_lines: List[str] = []
        for claim_id in ordered_claim_ids:
            claim_features = features_by_claim.get(claim_id, [])
            if not claim_features:
                continue

            parents = claim_parents_map.get(claim_id, [])
            if feature_tree_lines:
                feature_tree_lines.append("")

            if not parents:
                feature_tree_lines.append(
                    f"▶ [Claim {claim_id}] (独立权利要求 / 根节点):"
                )
                indent = "  "
            else:
                parent_str = ", ".join(parents)
                feature_tree_lines.append(
                    f"↳ [Claim {claim_id}] (从属权利要求，引用 Claim {parent_str}):"
                )
                indent = "    "

            for feature_no, feature in claim_features:
                name = str(feature.get("name", "unknown")).strip() or "unknown"
                status = (
                    "★区别特征"
                    if feature.get("is_distinguishing")
                    else "前序/从权常规特征"
                )
                feature_tree_lines.append(f"{indent}- [{feature_no}] {name} ({status})")

        return "\n".join(feature_tree_lines)

    def _generate_technical_means(
        self, core_logic: Dict[str, Any], feature_list: List[Dict]
    ) -> Dict[str, str]:
        logger.debug("正在生成 technical_means...")
        feature_menu_str = self._build_feature_menu_str(feature_list)

        system_prompt = """
        你是一名以“技术深度”和“逻辑严谨”著称的高级专利审查员。
        你的任务是基于**TCS（技术贡献评分）模型**和**第一性原理**，揭示该发明“如何从根本上起作用”。

        ### 核心指令：引用规范 (Citation Protocol)
        为了确保逻辑链条清晰，你在撰写 `technical_means` 时：
        1.  **必须引用编号**：提到任何来自【特征菜单】的特征时，必须带上其编号。
        2.  **格式要求**：请使用 Markdown 加粗格式：`**特征名称** [编号]`。
        3.  *示例*：
            *   *Bad:* "通过双气室结构降低了噪音..."
            *   *Good:* "通过 **双气室结构** [1.3] 增加了气体膨胀路径，配合 **吸音棉** [2.1] 的多孔耗散机制..."

        # 揭示技术机理 (The "Black Box" Revelation)
        **字段**: `technical_means`
        **指令**：请撰写一段约 200 字的深度技术综述。

        **核心思维模型**：
        请采用 **“IPO + 变换”** 的叙事结构：
        1.  **Input (困境输入)**：原始信号/能量面临什么具体的混沌、损耗或冲突？
        2.  **Process (关键变换)**：**★区别特征** 具体引入了什么物理定律、数学算法或控制逻辑，对输入进行了何种“变换”（调制、解耦、映射、补偿）？
        3.  **Output (秩序输出)**：这种变换如何直接导向了【核心技术问题】的解决？

        **写作要求**：
        1.  **严禁结构罗列**：绝对禁止写成“A连接B，B固定在C上”的说明书摘要。我们要看的是“活的机理”，不是“死的结构”。
        2.  **第一性原理视角**：
            -   *机械/物理类*：谈论力的传递路径、热阻的改变、流场的重构。
            -   *电学/控制类*：谈论信噪比的提升、反馈回路的收敛性、阻抗匹配。
            -   *算法/软件类*：谈论特征空间的映射、熵的减少、计算复杂度的降维。
        3.  **结合拓扑关系阐述协同**：请仔细阅读上下文中的【特征归属树】。优先解释根节点（独立权利要求）中的★区别特征如何建立基础机制，再顺着引用树说明从属权中的特征如何对该机制进行放大、适配或优化。
        4.  **必须引用编号**：提及特征时，**必须**严格使用 `**特征名称** [编号]` 格式。
        5.  **聚焦区别特征**：机理描述的重心必须落在标记为 `[★区别特征]` 的项上，说明它们是如何“四两拨千斤”地改变了现有技术的局限。

        **范例对比**：
        *   *Low Quality (表象罗列)*:
            "本发明包括 **振动传感器** [1.1] 和 **控制器** [1.2]。传感器安装在轴承座上，采集信号传给控制器，控制器进行FFT分析，如果超过阈值就报警。"
            *(评语：这是小学生水平的看图说话，没有解释“为什么能解决隐匿故障”。)*

        *   *High Quality (机理洞察)*:
            "针对早期轴承故障信号极易被背景噪声淹没的【核心问题】，本发明并未采用传统的时域阈值判定，而是引入了 **自适应共振解调算法** [1.3](★区别特征)。从信息论角度看，该算法利用 **包络检波器** [2.1] 将高频载波中的低频故障冲击特征（信息熵高的部分）进行非线性映射，实质上是在频域上对信噪比进行了‘放大’。配合 **多级带通滤波器** [2.2] 的级联作用，成功将微弱的微伏级故障特征从强干扰背景中剥离，实现了对早期微裂纹的精准捕捉。"

        # 输出格式 (JSON Only)
        必须严格输出标准的 JSON 对象，**严禁使用 Markdown 代码块 (```json)**，严禁包含任何解释性文字。结构如下：
        {
            "technical_means": "基于...原理，利用 **特征A** [1.1] 实现了..."
        }
        """

        user_content = f"""
        # 1. 核心逻辑锚点 (Anchor)
        【待解决的核心技术问题 (The Pain Point)】: {core_logic.get('technical_problem')}
        【技术方案概览 (The Solution)】: {core_logic.get('technical_scheme')}

        # 2. 特征归属树 (Feature Dependency Tree - Strict Selection)
        {feature_menu_str}

        # 3. 待验证的声称效果 (Claimed Effects)
        {self.text_effect if self.text_effect else "（原文未集中描述效果，请基于下文实施例反推）"}

        # 4. 事实数据库 (Embodiments & Experiments)
        {self.text_details[:12000]}
        """

        response = self.llm_service.invoke_text_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            task_kind="technical_means_generation",
            temperature=0.2,
        )
        if isinstance(response, Dict):
            return {"technical_means": str(response.get("technical_means", "") or "")}
        return {"technical_means": ""}

    def _evaluate_technical_effects(
        self, core_logic_with_means: Dict[str, Any], feature_list: List[Dict]
    ) -> Dict[str, List[Dict[str, Any]]]:
        logger.debug("正在评估 technical_effects...")
        feature_menu_str = self._build_feature_menu_str(feature_list)

        system_prompt = """
        你是一名以“技术深度”和“逻辑严谨”著称的高级专利审查员。
        你的任务是基于 TCS（技术贡献评分）模型，对申请人声称的效果进行创造性审计。

        ### 核心指令：引用规范 (Citation Protocol)
        为了确保逻辑链条清晰，你在撰写 `rationale` 时：
        1.  **必须引用编号**：提到任何来自【特征菜单】的特征时，必须带上其编号。
        2.  **格式要求**：请使用 Markdown 加粗格式：`**特征名称** [编号]`。

        # 效果验证与 TCS 评分 (The Strict Audit)
        **字段**: `technical_effects`
        **指令**：对申请人声称的每一个效果进行“创造性审计”。

        #### 评分标准
        请像最挑剔的审查员一样打分，**严禁分数通胀**。

        - **5分[核心/必要 (Vital) -> 对应检索块 Block B]**:
          *定义*：解决【核心技术问题】的“阿基米德支点”（即区别技术特征）。
          *判定*：如果移除这些特征，发明是否会立即退化为现有技术或完全失效？如果是，给5分。**（独立节点，无依存）**
          *审查员内心戏*："这是本发明的灵魂，没有它，这篇专利就不成立。"

        - **4分[关键使能 (Enabler) -> 对应检索块 Block C-核心]**:
          *定义*：为了让 5分特征 落地而必须克服的二次技术障碍（如兼容性、接口适配、特定场景可靠性）的解决方案。
          *判定*：通常是高价值的从属权利要求，与核心特征存在强协同。**必须明确它是为哪一个 5分特征 服务的**。
          *审查员内心戏*："虽然不是最基础的发明点，但没有这个具体支撑，核心构思只是无法落地的空中楼阁。"

        - **3分[优化/有益 (Improver) -> 对应检索块 Block C-可选]**:
          *定义*：提供额外技术增益的非必要改进。涉及成本、良率、便利性或次要性能的具体提升。
          *判定*：通常是一般性的从属权利要求。它具有具体的技术限定，并非毫无新意，但并非解决核心问题的唯一路径。**必须明确它依附于哪一个 5分/4分特征**。
          *审查员内心戏*："这是锦上添花的特定优化设计，在面对无效宣告时，可以作为退板防守的权利要求。"

        - **1-2分[常规/前序 (Generic/Preamble) -> 对应检索块 Block A]**:
          *定义*：前序特征、应用场景、公知常识、标准件的常规用途，或仅仅是引入计算机/自动化带来的必然结果。
          *判定*：说明书中一笔带过的常规结构，或者没有产生超出预期的协同效果的通用部件。**（独立背景节点，无依存）**
          *审查员内心戏*："这只是行业惯用的背景部件和基础环境，没有任何实质性的技术贡献。"

        #### 验证逻辑 (Evidence & Rationale)
        1.  **Contributing Features**:
            -   必须根据上下文中的【特征归属树】选择特征。
            -   **约束 A**：选取的特征组合中，**必须包含至少一个标记为 (★区别特征) 的项**，除非该效果完全由现有技术产生（此时TCS分值应低于3分）。
            -   **约束 B**：输出 JSON 时，请**只输出特征名称**，不要包含编号以及"(...)" 状态标记。
            -   *示例*：菜单项 "[1.1] 自适应滤波算法 (★区别特征)" -> 输出应为 "自适应滤波算法"。
        2.  **dependent_on (依存追踪)**：
            -   请严格参考【特征归属树】中的“引用关系（Claim X 引用 Claim Y）”。
            -   如果是 4分 或 3分 效果（通常由从属权利要求中的特征产生），必须顺着树向上追溯，填入其所依附的上级（优先独立权）核心特征名称列表（如果是多重依赖，请全部列出）。
            -   如果是 5分 或 1-2分 效果（它们属于独立的根节点或背景节点），请严格填入空数组。
        3.  **Evidence (实锤)**：
            -   **一级证据（最佳）**：定位到具体的**实验数据对比**、图表（Figure X）或具体的**实施例参数**（如“温度控制在50-60度”）。
            -   **二级证据（次之）**：具体的逻辑推演描述。
            -   **无证据**：如果文中只有“具有...优点”的空话，填入“仅声称，无实施例支持”。
        4.  **Rationale (逻辑链)**：
            -   使用“特征 -> 机制 -> 效果”的句式。
            -   **严格遵守引用规范**：必须写成 `**特征名称** [编号]` 的形式。
            -   例如：“双气室结构(特征)增加了气体膨胀路径(机制)，从而降低了排气噪音(效果)。”

        # 输出格式 (JSON Only)
        必须严格输出标准的 JSON 对象，**严禁使用 Markdown 代码块 (```json)**，严禁包含任何解释性文字。结构如下：
        {
            "technical_effects": [
                {
                    "effect": "精炼的效果描述",
                    "tcs_score": 5,
                    "contributing_features": ["特征A", "特征B"],
                    "dependent_on": [],
                    "evidence": "实施例3：数据显示误报率从5%降至0.1%...",
                    "rationale": "**特征A** [1.1] 建立了...机制，解决了..."
                },
                {
                    "effect": "防止强干扰下发散",
                    "tcs_score": 4,
                    "contributing_features": ["特征C"],
                    "dependent_on": ["特征A", "特征B"],
                    "evidence": "...",
                    "rationale": "为了配合 **特征A** [1.1] 和 **特征B** [1.2] 的协同，**特征C** [2.1] 提供了..."
                }
            ]
        }
        """

        user_content = f"""
        # 1. 核心逻辑锚点 (Anchor)
        【待解决的核心技术问题 (The Pain Point)】: {core_logic_with_means.get('technical_problem')}
        【技术方案概览 (The Solution)】: {core_logic_with_means.get('technical_scheme')}

        # 2. 前置推演出的技术机理 (Pre-derived Technical Means)
        *** 仅作为审计推理上下文，不可与证据字段混淆 ***
        {core_logic_with_means.get('technical_means', '')}

        # 3. 待验证的声称效果 (Claimed Effects)
        {self.text_effect if self.text_effect else "（原文未集中描述效果，请基于下文实施例反推）"}

        # 4. 特征归属树 (Feature Dependency Tree - Strict Selection)
        *** 必须从此列表中选择 contributing_features ***
        {feature_menu_str}

        # 5. 事实数据库 (Embodiments & Experiments)
        *** 请在此区域挖掘一级证据 (数据/参数/具体行为) ***
        {self.text_details[:12000]}
        """

        response = self.llm_service.invoke_text_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            task_kind="technical_effect_verification",
            temperature=0.0,
        )

        if isinstance(response, Dict) and isinstance(response.get("technical_effects"), list):
            effects = response.get("technical_effects", [])
            effects.sort(key=lambda x: x.get("tcs_score", 0), reverse=True)
            return {"technical_effects": effects}
        return {"technical_effects": []}

    def _verify_evidence(
        self, core_logic: Dict[str, Any], feature_list: List[Dict]
    ) -> Dict[str, Any]:
        """
        TCS 贡献度评分与证据验证（两阶段串行）。
        """
        logger.debug("正在执行 TCS 技术贡献分析...")

        technical_means_data = self._run_cached(
            "technical_means",
            self._generate_technical_means,
            core_logic,
            feature_list,
        )
        technical_means = ""
        if isinstance(technical_means_data, Dict):
            technical_means = str(technical_means_data.get("technical_means", "") or "")

        core_logic_with_means = {
            **core_logic,
            "technical_means": technical_means,
        }
        technical_effects_data = self._run_cached(
            "technical_effects",
            self._evaluate_technical_effects,
            core_logic_with_means,
            feature_list,
        )
        technical_effects = []
        if isinstance(technical_effects_data, Dict):
            raw_effects = technical_effects_data.get("technical_effects", [])
            if isinstance(raw_effects, list):
                technical_effects = raw_effects

        return {
            "technical_means": technical_means,
            "technical_effects": technical_effects,
        }

    def _generate_figures_analysis(self, global_context: Dict) -> List[Dict[str, Any]]:
        """
        图解生成。按图号聚合 drawings，同图号多图只生成一份解说文本。
        """
        grouped_drawings: Dict[str, List[Dict[str, Any]]] = {}
        for drawing in self.drawings:
            if not isinstance(drawing, dict):
                continue
            label = str(drawing.get("figure_label", "")).strip()
            if not label:
                continue
            grouped_drawings.setdefault(label, []).append(drawing)

        grouped_items: List[Tuple[str, List[Dict[str, Any]]]] = list(grouped_drawings.items())
        if not grouped_items:
            return []

        results: List[Optional[Dict[str, Any]]] = [None] * len(grouped_items)
        first_label, first_items = grouped_items[0]
        results[0] = self._build_single_figure_result(first_label, first_items, global_context)

        remaining_items = grouped_items[1:]
        if remaining_items:
            max_workers = min(self.figure_parallel_workers, len(remaining_items))
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="figures") as executor:
                future_map = {
                    submit_with_current_context(
                        executor, self._build_single_figure_result, label, items, global_context
                    ): idx
                    for idx, (label, items) in enumerate(remaining_items, start=1)
                }
                for future in as_completed(future_map):
                    idx = future_map[future]
                    results[idx] = future.result()

        return [item for item in results if item is not None]

    def _build_single_figure_result(
        self, label: str, grouped_items: List[Dict[str, Any]], global_context: Dict
    ) -> Dict[str, Any]:
        caption = ""
        file_paths: List[str] = []
        seen_paths = set()

        for item in grouped_items:
            item_caption = str(item.get("caption", "")).strip()
            if item_caption and not caption:
                caption = item_caption

            path = str(item.get("file_path", "")).strip()
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)
            file_paths.append(path)

        image_abs_paths: List[str] = []
        display_image_paths: List[str] = []
        for file_path in file_paths:
            filename = os.path.basename(file_path)
            if not filename:
                continue

            display_image_paths.append(file_path.replace("images", "annotated_images"))
            target_path = self.annotated_dir / filename
            if target_path.exists():
                image_abs_paths.append(str(target_path))
            else:
                logger.warning(f"未找到标注图文件，跳过该图片: {target_path}")

        part_ids: List[str] = []
        for file_path in file_paths:
            filename = os.path.basename(file_path)
            part_ids.extend(self.image_parts.get(filename, []))

        local_parts_context_str = ""
        parts_table_data: List[Dict[str, Any]] = []
        local_part_ids_for_context: List[str] = []

        if part_ids:
            temp_desc_list = []
            part_ids = sorted(set(part_ids), key=self._natural_part_id_key)
            raw_features = global_context.get("raw_features", [])
            if not isinstance(raw_features, list):
                raw_features = []

            for pid in part_ids:
                pid_key = self._normalize_part_id(pid)
                if not pid_key:
                    continue

                info = self.parts_db.get(pid_key)
                if not info:
                    continue

                name = info.get("name") or "未知"
                func = info.get("function") or "未知功能"
                hierarchy = info.get("hierarchy")
                spatial = info.get("spatial_connections") or "未提及"
                motion = info.get("motion_state") or "未提及"
                attributes = info.get("attributes") or "未提及"
                matched_feature = self._match_distinguishing_feature(name, raw_features)
                feature_status = ""
                matched_feature_name = ""
                matched_claim_source = ""
                if matched_feature:
                    matched_feature_name = str(matched_feature.get("name", "")).strip()
                    matched_claim_source = str(
                        matched_feature.get("claim_source", "")
                        or matched_feature.get("claim_id", "未知权项")
                    ).strip()
                    feature_status = f" 🔥[核心特征|权{matched_claim_source}]"

                local_part_ids_for_context.append(pid_key)
                temp_desc_list.append(
                    f"- {name}({pid_key}){feature_status}: 功能={func}；层级={hierarchy or '未提及'}；"
                    f"空间连接={spatial}；运动状态={motion}"
                )
                parts_table_data.append(
                    {
                        "id": pid_key,
                        "name": name,
                        "function": func,
                        "hierarchy": hierarchy,
                        "spatial_connections": spatial,
                        "motion_state": motion,
                        "attributes": attributes,
                        "is_distinguishing_feature": bool(matched_feature),
                        "matched_feature_name": matched_feature_name,
                        "matched_feature_source": matched_claim_source,
                    }
                )
            local_parts_context_str = (
                "\n".join(temp_desc_list) if temp_desc_list else "（该图未识别到具体部件标号）"
            )
        else:
            local_parts_context_str = "（该图未识别到具体部件标号）"

        related_parts_context_str = self._build_related_parts_context(local_part_ids_for_context)
        image_explanation = self._generate_single_figure_caption(
            label,
            caption,
            local_parts_context_str,
            related_parts_context_str,
            global_context,
            image_abs_paths,
        )
        return {
            "image_paths": display_image_paths,
            "image_title": f"{label} {caption}".strip(),
            "image_explanation": image_explanation,
            "parts_info": parts_table_data,
        }

    @staticmethod
    def _normalize_part_id(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        return re.sub(r"[^a-zA-Z0-9]", "", raw).lower()

    def _natural_part_id_key(self, value: Any) -> List[Any]:
        normalized = self._normalize_part_id(value)
        target = normalized or str(value or "")
        return [int(s) if s.isdigit() else s for s in re.split(r"(\d+)", target)]

    @staticmethod
    def _normalize_text_for_match(value: Any) -> str:
        raw = str(value or "").strip().lower()
        if not raw:
            return ""
        return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]", "", raw)

    def _match_distinguishing_feature(
        self, part_name: str, raw_features: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        part_norm = self._normalize_text_for_match(part_name)
        if len(part_norm) < 2:
            return None

        for feat in raw_features:
            if not isinstance(feat, dict) or not feat.get("is_distinguishing"):
                continue

            feat_name = str(feat.get("name", "")).strip()
            feat_norm = self._normalize_text_for_match(feat_name)
            if len(feat_norm) < 2:
                continue

            if self._is_substring_match_reliable(part_norm, feat_norm):
                return feat
        return None

    @staticmethod
    def _is_substring_match_reliable(part_norm: str, feat_norm: str) -> bool:
        if not part_norm or not feat_norm:
            return False
        if part_norm == feat_norm:
            return True
        if feat_norm not in part_norm and part_norm not in feat_norm:
            return False

        shorter, longer = (
            (feat_norm, part_norm) if len(feat_norm) <= len(part_norm) else (part_norm, feat_norm)
        )
        if len(shorter) <= 2:
            return longer.startswith(shorter) or longer.endswith(shorter)
        if len(longer) - len(shorter) <= 1:
            return True
        if longer.startswith(shorter) or longer.endswith(shorter):
            return True
        return len(shorter) >= 4

    def _build_related_parts_context(self, local_part_ids: List[str]) -> str:
        if not local_part_ids:
            return "（无可用的父子关联部件参考）"

        normalized_local_ids = []
        seen_local = set()
        for pid in local_part_ids:
            pid_key = self._normalize_part_id(pid)
            if (
                pid_key
                and pid_key not in seen_local
                and isinstance(self.parts_db.get(pid_key), dict)
            ):
                normalized_local_ids.append(pid_key)
                seen_local.add(pid_key)

        if not normalized_local_ids:
            return "（无可用的父子关联部件参考）"

        related_ids = set(normalized_local_ids)

        # 直接父级
        for pid in normalized_local_ids:
            info = self.parts_db.get(pid, {})
            if not isinstance(info, dict):
                continue
            parent_id = self._normalize_part_id(info.get("hierarchy"))
            if parent_id and parent_id in self.parts_db:
                related_ids.add(parent_id)

        # 直接子级
        for other_pid, other_info in self.parts_db.items():
            if not isinstance(other_info, dict):
                continue
            other_key = self._normalize_part_id(other_pid)
            parent_id = self._normalize_part_id(other_info.get("hierarchy"))
            if other_key and parent_id in normalized_local_ids:
                related_ids.add(other_key)

        lines = []
        for pid in sorted(related_ids, key=self._natural_part_id_key):
            info = self.parts_db.get(pid, {})
            if not isinstance(info, dict):
                continue
            name = info.get("name") or "未知部件"
            func = info.get("function") or "未提及"
            hierarchy = info.get("hierarchy") or "未提及"
            spatial = info.get("spatial_connections") or "未提及"
            motion = info.get("motion_state") or "未提及"
            lines.append(
                f"- 标号 {pid} ({name}): 功能={func}；层级={hierarchy}；"
                f"空间连接={spatial}；运动状态={motion}"
            )

        return "\n".join(lines) if lines else "（无可用的父子关联部件参考）"

    def _generate_single_figure_caption(
        self,
        label: str,
        caption: str,
        local_parts: str,
        related_parts_context: str,
        global_context: Dict,
        image_paths: List[str],
    ) -> str:
        """
        生成单张图片的“看图说话”
        """

        effects_list = global_context.get("effects", []) or []
        formatted_effects: List[str] = []

        for effect_item in effects_list[:6]:
            if isinstance(effect_item, dict):
                effect_text = str(effect_item.get("effect", "")).strip() or "未知效果"
                features = effect_item.get("contributing_features", [])
                if isinstance(features, list):
                    feature_text = ", ".join(
                        str(feature).strip() for feature in features if str(feature).strip()
                    )
                else:
                    feature_text = str(features).strip()

                if feature_text:
                    formatted_effects.append(
                        f"- {effect_text} (实现手段: {feature_text})"
                    )
                else:
                    formatted_effects.append(f"- {effect_text}")
            elif str(effect_item).strip():
                formatted_effects.append(f"- {str(effect_item).strip()}")

        effects_str = "\n".join(formatted_effects) if formatted_effects else "- （未提取到明确技术效果）"
        feature_tree = global_context.get("feature_tree") or "（未提供特征树）"
        local_parts_str = (
            local_parts.strip()
            if isinstance(local_parts, str) and local_parts.strip()
            else "（机器未能在本图中提取到带有确定标号的部件，请完全依赖你的视觉和全局上下文进行推理）"
        )

        system_prompt = f"""
        # 角色设定
        你是一位服务于国家知识产权局的资深专利可视化审查员。你的唯一任务是：基于提供的【客观事实上下文】，准确提取专利附图中具有实质性技术贡献的信息，并将其与【权利要求特征树】进行映射解说。
        输出必须是极其专业、高信息密度的工程师陈述，拒绝任何口语化、主观评价或冗余过渡语。

        # 核心指令 (Production Rules)

        ## 1. 绝对的视觉忠诚 (反幻觉机制)
        - 你只能描述【图中真实可见】或【基于视觉线索与上下文能确凿推导】的结构/交互。
        - **退化原则**：如果这是一张背景技术图、或图中完全没有体现【🔥核心区别特征】，请客观陈述图中展示的常规架构或基础流程，**绝对禁止**把未画出的核心特征强行塞入解说中。

        ## 2. 动态分析策略 (按图类型自适应)
        请首先在内心判断该图属于以下哪种类型，并严格采用对应的解剖视角：
        - [机械/装置/剖面图]：聚焦物理拓扑与传动/流体路径。解释动力/流体如何输入，经过 `🔥` 部件发生了何种空间约束或物理变换。
        - [电子电路/系统框图]：聚焦信号流向与控制逻辑。解释模块间的数据耦合关系，以及 `🔥` 部件对信号进行了何种处理（如放大、映射、降噪）。
        - [算法流程/时序图]：跳过常规的“开始/初始化”，直击发生数据质变或逻辑重定向的【关键步骤】（特别是 `🔥` 对应的步骤）。
        - [数据图表/对比波形]：严禁泛泛而谈。必须指出横纵坐标含义，并指出图中的哪个极值、突变点或对比差异，直接印证了专利的【预期核心效果】。

        ## 3. 焦点法则 (二八定律)
        - 输入的部件清单中，带有 `🔥` 标记的是机器初筛的“核心特征”。
        - 若图中包含 `🔥` 部件，请将 70% 的篇幅用于详细推演其运作机理。对于外壳、螺栓、常规总线等支撑性部件，一笔带过或忽略。

        ## 4. 强制格式与文风 (Strict Output Formatting)
        - **命名规范**：提及部件时，必须且只能使用 `部件名(标号)` 的格式，如 `减速齿轮(12)`。图中可见但无标号的部件仅用 `部件名`。严禁自行编造标号。
        - **纯净文本**：输出必须是一段连贯的正文（150-250字左右）。**绝对禁止**使用任何 Markdown 语法（如 `**`、`#`、`-`、换行符 `\n`）。
        - **客观语态**：禁止使用“本图展示了”、“我们可以看到”、“巧妙地”等主观废话。直接以技术实体作主语（例：“输入轴(1)带动偏心轮(2)旋转，基于该偏心运动触发...”）。

        # 叙事结构模板 (内在逻辑，请融合成一段话)
        1. [架构定位]：用极其简练的一句话，说明该图在整个系统特征树（独立权/从属权）中所处的逻辑位置与形态视角。
        2. [机理推演]：顺着能量流/信息流的走向，动态推演 `🔥核心部件` 的运行机制。
        3. [效果闭环]：一语中的，说明上述机制如何直击痛点，促成了某项客观技术效果。

        ================ 专利全局静态知识库 (Global Context) ================
        以下是本专利的全局基准信息，请将其作为解析所有附图的思想纲领：

        ### [第一层：全局技术锚点] (解说落脚点)
        - 技术主题：{global_context.get('title', '未知主题')}
        - 待解决的客观问题：{global_context.get('problem', '未知问题')}
        - 预期达成的核心效果：
        {effects_str}

        ### [第二层：权利要求特征树] (理解系统层级与依赖)
        【提示】：此树状结构展示了发明的逻辑层级。请参考此树，理解下文 `🔥` 部件在宏观系统中的地位（是根节点的核心，还是叶子节点的优化）。
        {feature_tree}
        ====================================================================
        """

        user_content = f"""
        请根据上述《专利全局静态知识库》的指引，为当前目标专利图生成专业机理解说。

        ================ 本图动态实证线索 (Local Context) ================
        ### [第三层：本图实证线索] (解说的直接依据)
        - 图号及原注：{label} {caption}
        - 机器提取的本图部件清单 (视觉焦点)：
          【注意】：`🔥` 代表核心特征。若列表为“未提取到部件”，请完全依赖你的视觉进行专业推演。
          {local_parts_str}
        - 周边隐藏部件参考库 (仅用于理解关联，若图中未画出，禁止描写)：
          {related_parts_context}
        ================================================================

        请直接输出最终的解说段落正文，不要包含任何多余的解释、前缀或 Markdown 标记：
        """

        try:
            if not image_paths:
                raise ValueError("image_paths is empty")

            basenames = ", ".join(os.path.basename(path) for path in image_paths)
            logger.info(f"正在进行视觉思考分析: {label} ({basenames})")

            if len(image_paths) == 1:
                content = self.llm_service.invoke_vision_image(
                    image_path=image_paths[0],
                    system_prompt=system_prompt,
                    user_prompt=user_content,
                    task_kind="vision_single_figure_explain",
                )
            else:
                multi_image_prompt = (
                    user_content
                    + "\n\n[多图融合指令] 以上为同一图号下的多个视角或局部截图。"
                    + "请融合所有图片中的一致信息，仅输出一段纯文本解说。"
                )
                content = self.llm_service.invoke_vision_images(
                    image_paths=image_paths,
                    system_prompt=system_prompt,
                    user_prompt=multi_image_prompt,
                    task_kind="vision_multi_figure_synthesis",
                    temperature=0.2,
                )

            content = str(content).strip()
            content = re.sub(r"[*#`_]", "", content)
            content = re.sub(r"\s*\n+\s*", "", content).strip()
            if content:
                return content
            raise ValueError("empty image_explanation from model response")
        except Exception as e:
            logger.warning(f"图片解说生成失败 {label}: {e}")
            final_label = label if str(label).startswith("图") else f"图{label}"
            return f"{final_label}展示了{caption}的相关示意结构。具体部件的空间连接关系与运行机理请参考实施例的详细说明。"
