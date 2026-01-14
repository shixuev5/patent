import concurrent.futures
from pathlib import Path
from typing import Dict, Any, List
from loguru import logger
from src.utils.llm import get_llm_service
from src.utils.cache import StepCache
from config import settings

class SearchStrategyGenerator:
    def __init__(self, patent_data: Dict, report_data: Dict, cache_file: Path = None):
        self.llm_service = get_llm_service()
        self.patent_data = patent_data
        self.report_data = report_data

        # 初始化缓存管理器
        self.cache = StepCache(cache_file) if cache_file else None

        self.base_ipcs = self.patent_data.get("bibliographic_data", {}).get("ipc_classifications", [])

    def generate_strategy(self) -> Dict[str, Any]:
        """
        主入口：执行两阶段检索策略生成
        """
        logger.info("开始构建检索策略...")

        # 初始化默认返回结构
        search_matrix = []
        strategy_plan = {"strategies": []}

        # Stage 1: 检索要素表 (Search Matrix) - 深度扩展
        def _execute_stage1():
            matrix_context = self._build_matrix_context()
            return self._build_search_matrix(matrix_context)

        # 使用通用方法运行 Step 1
        search_matrix = self.cache.run_step("step1_matrix", _execute_stage1)
        
        if not search_matrix:
            logger.warning("Search Matrix 为空，后续策略生成可能受限。")
        
        # Stage 2: 检索式构建 (Query Formulation) - 分库分治
        query_context = self._build_query_context(search_matrix)

        # 定义任务映射
        tasks_map = {
            "step2_cntxt": self._generate_cntxt_strategies,
            "step2_ven": self._generate_ven_strategies,
            "step2_npl": self._generate_npl_strategies
        }

        results_map = {}
        futures_map = {}
        
        # 使用 ThreadPoolExecutor 进行并行调用
        # max_workers=3 对应三个独立的数据库策略生成任务
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            for key, func in tasks_map.items():
                if self.cache.get(key):
                    logger.info(f"Step [{key}]: 跳过 (命中缓存)")
                    results_map[key] = self.cache.get(key)
                else:
                    logger.info(f"Step [{key}]: 提交并行任务...")
                    # 提交任务
                    futures_map[executor.submit(func, query_context)] = key

            # 2. 等待并行任务完成并保存缓存
            for future in concurrent.futures.as_completed(futures_map):
                key = futures_map[future]
                try:
                    res = future.result()
                    results_map[key] = res
                    # 线程安全地写入缓存
                    self.cache.save(key, res)
                except Exception as e:
                    logger.error(f"Step [{key}] failed: {e}")
                    results_map[key] = []

        # 合并所有策略
        combined_strategies = self._merge_strategies(
            results_map.get("step2_cntxt", []),
            results_map.get("step2_ven", []),
            results_map.get("step2_npl", [])
        )
        
        # 包装为最终对象
        strategy_plan = {"strategies": combined_strategies}

        self._inject_semantic_strategy(strategy_plan)

        # 合并结果
        return {
            "search_matrix": search_matrix, # 对应你要求的中文、中文扩展、英文翻译
            "search_plan": strategy_plan    # 具体的检索步骤和分析
        }
    
    def _merge_strategies(self, *strategy_lists: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        辅助函数：合并来自不同源的策略列表。
        逻辑：
        1. 保持输入列表的顺序 (例如 CNTXT -> VEN -> NPL)。
        2. 如果策略名称 (name) 相同，则将后续的 queries 追加到已有策略中。
        3. 如果策略名称不同，则视为新策略添加。
        """
        merged_map = {}
        
        # 遍历所有传入的策略列表（按参数顺序）
        for strategy_list in strategy_lists:
            for strategy in strategy_list:
                name = strategy.get("name")
                if not name:
                    continue
                
                if name not in merged_map:
                    # 首次遇到该名称：初始化新的策略对象
                    # 注意：构造新字典以避免引用污染
                    merged_map[name] = {
                        "name": name,
                        "description": strategy.get("description", ""), # 保留首次出现的描述
                        "queries": list(strategy.get("queries", []))    # 复制查询列表
                    }
                else:
                    # 再次遇到该名称：仅合并 queries
                    current_queries = merged_map[name]["queries"]
                    new_queries = strategy.get("queries", [])
                    if new_queries:
                        current_queries.extend(new_queries)
        
        # 返回合并后的列表
        return list(merged_map.values())

    def _build_matrix_context(self) -> str:
        """
        阶段一上下文：构建全维度的技术理解环境
        引入“技术三要素”和“区别特征”的详细描述，为检索要素提取提供上下文。
        """
        biblio = self.patent_data.get("bibliographic_data", {})
        report = self.report_data
        
        # 提取区别特征，并附带类型（装置/方法）和描述，这对关键词扩展至关重要
        # 专家思路：如果是装置特征，侧重结构名词；如果是方法特征，侧重动作动词。
        distinguishing_features = []
        for f in report.get("technical_features", []):
            if f.get("is_distinguishing", False) is True:
                f_name = f.get("name", "未命名特征")
                f_desc = f.get("description", "无描述")
                f_rationale = f.get("rationale", "无详细依据")
                f_source = f.get("claim_source", "unknown")
                
                # 格式构建：[名称] (来源) -> 描述 -> 核心依据
                feature_str = (
                    f"- 【核心特征】{f_name} ({f_source})\n"
                    f"   描述: {f_desc}\n"
                    f"   地位: {f_rationale}"
                )
                distinguishing_features.append(feature_str)

        # 提取技术效果，用于构建功能性检索词
        effects = []
        for e in report.get("technical_effects", []):
            if e.get("feature_type", "") == "Distinguishing Feature":
                effect_desc = e.get('effect', '')
                source_feat = e.get('source_feature_name', '整体方案')

                effects.append(f"- 【核心效果】{effect_desc} (源于: {source_feat})")

        return f"""
        [发明名称] {biblio.get('invention_title')}
        
        [IPC参考] {', '.join(self.base_ipcs[:3])}

        [技术领域 (Subject - Block A 宽泛背景)]
        {report.get('technical_field', '未定义')}

        [保护主题 (Subject - Block A 核心定义)]
        {report.get("claim_subject_matter", "未定义")}

        [技术问题 (Problem)]
        {report.get('technical_problem', '未定义')}

        [技术手段 (Solution Overview)]
        {report.get('technical_means', '未定义')}

        [核心区别特征 (Key Features - Block B - 检索重点)]
        {chr(10).join(distinguishing_features)}

        [预期技术效果 (Effects - Block C - 辅助筛选)]
        {chr(10).join(effects)}
        """
    
    def _build_query_context(self, matrix: List[Dict]) -> str:
        """
        阶段二上下文：专注逻辑组装
        将 Matrix 重组为逻辑块 (Subject, KeyFeature, Functional)，
        并结合申请人信息，为 LLM 提供清晰的“积木”。
        """
        biblio = self.patent_data.get("bibliographic_data", {})
        
        # 1. 对 Matrix 进行逻辑分组 (Grouping)
        # 这样 LLM 就能知道谁是“主语”，谁是“核心”，谁是“修饰”
        grouped_matrix = {
            "Subject": [],     # 块 A: 技术领域/基础产品
            "KeyFeature": [],  # 块 B: 核心区别特征 (重点)
            "Functional": [],  # 块 C: 效果/功能/问题
            "Other": []
        }

        for item in matrix:
            role = item.get('role', 'Other')            
            # 格式化单个要素
            content = (
                f"   - [ID: {item['concept_key']}]\n"
                f"     ZH: {', '.join(item.get('zh_expand', [])[:5])}\n" # 限制长度防Token溢出
                f"     EN: {', '.join(item.get('en_expand', [])[:5])}\n"
                f"     IPC/CPC: {', '.join(item.get('ipc_cpc_ref', []))}"
            )
            grouped_matrix[role].append(content)

        # 2. 构建文本块
        matrix_text = []
        if grouped_matrix["Subject"]:
            matrix_text.append(f"【Block A - 技术领域 (Subject)】:\n" + "\n".join(grouped_matrix["Subject"]))
        
        if grouped_matrix["KeyFeature"]:
            matrix_text.append(f"【Block B - 核心创新点 (KeyFeatures - 必须出现在检索式中)】:\n" + "\n".join(grouped_matrix["KeyFeature"]))
            
        if grouped_matrix["Functional"]:
            matrix_text.append(f"【Block C - 功能与效果 (Functional - 用于组词或验证)】:\n" + "\n".join(grouped_matrix["Functional"]))

        formatted_matrix = "\n\n".join(matrix_text)

        # 3. 处理申请人和发明人 (用于追踪检索)
        applicants = ', '.join([a['name'] for a in biblio.get('applicants', [])][:3])
        inventors = ', '.join(biblio.get('inventors', [])[:3])

        return f"""
        【检索要素积木 (Building Blocks)】:
        {formatted_matrix}

        【背景约束 (Constraints)】:
        - 现有 IPC (Base IPCs): {', '.join(self.base_ipcs[:3])}
        - 申请人 (Assignee): {applicants}
        - 发明人 (Inventor): {inventors}
        """

    def _build_search_matrix(self, context: str) -> List[Dict]:
        """
        阶段一：基于专家策略构建检索要素表 (Search Matrix)
        """
        logger.info("[SearchAgent] Step 1: Building Search Matrix with Expert Strategy...")
        
        system_prompt = """
        你是一位拥有 20 年经验的专利检索专家。请基于提供的技术交底信息，拆解技术方案并构建**检索要素表（Search Matrix）**。

        ### 你的核心任务：
        请采用 **ABC 块检索策略 (Block Search Strategy)** 提取 4-6 个核心检索要素：
        1.  **Block A - 技术领域 (Subject)**: (1-2 个) 
            - **首选来源**：参考上下文中的 **[保护主题]**，确定最准确的基础产品名称或方法载体（如“显示面板”、“图像处理方法”）。
            - **次选来源**：参考 **[技术领域]** 获取更宽泛的应用场景。
        2.  **Block B - 核心创新点 (Key Features)**: (2-3 个, **核心**) 必须源于“核心区别特征”。关注具体的结构构造、材料组分或独特的方法步骤。
        3.  **Block C - 功能/效果/修饰 (Functional)**: (1-2 个) 提取关键的技术效果（如“散热”）或功能限定（如“可拆卸”）。

        ### 扩展原则 (Expansion Rules)：
        对于每个提取的要素，必须进行全方位的“检索语言翻译”：
        
        1.  **中文扩展 (CN)**:
            - 语域转换 (关键): 必须同时覆盖 **口语/俗称** (如“手机”、“猫眼”) 和 **专利法律/学术术语** (如“移动终端”、“光电窥孔”、“图像采集装置”)。
            - 缩略语与全称: 包含常见的中文简称及混合写法 (如“无人机” <-> “UAV” <-> “无人驾驶飞行器”)。
            - 结构-功能互换: 
                - 若是结构词，扩展其功能描述 (如“弹簧” -> “弹性件”、“偏置部件”)。
                - 若是功能词，扩展其常见实现载体 (如“显示” -> “屏幕”、“面板”、“显示器”)。
            - 适度上位化: 仅扩展至**本领域通用**的上位概念，禁止无限泛化 (如“锂电池”可扩展为“二次电池”，但不可扩展为“能源设备”)。
        2.  **英文扩展 (EN)**:
            - 使用 **Patentese (专利法律英语)**。如 `plurality`, `configured to`, `assembly`, `means` 等专业表达。
            - 强制使用截词符: 如 `sensor+` (涵盖 sensors, sensory), `configur+`。
            - 词性变化: 动词/名词形式必须同时包含 (e.g., `mix+` for mixing, mixer, mixture)。
            - 美式/英式拼写: 如 `colo?r`, `alumini?um` (若支持正则) 或列出两种拼写。
        3.  **分类号 (IPC/CPC)**:
            - 维度区分:
                - 对于 **Block A (技术领域)**: 提供 **应用类** 分类号 (如: 车辆 B60)。
                - 对于 **Block B/C (功能部件)**: 提供 **功能类** 分类号 (如: 电池 H01M, 数据处理 G06F)。
            - 精度控制:
                - 默认级别：**大组 (Main Group)** (如 `H01M 10/00`)。
                - 仅当且仅当上下文中有明确的具体技术术语（如“锂离子”）时，才精确到 **小组 (Subgroup)** (如 `H01M 10/0525`)。
            - 格式: 严格使用标准格式，包含斜杠和空格 (如 `G06F 17/30`, `H04W 72/04`)。

        ### 输出格式 (JSON List Only)
        必须严格输出标准的 JSON 列表，**严禁使用 Markdown 代码块 (```json)**，严禁包含任何解释性文字。结构如下：
        [
          {
            "concept_key": "核心概念词 (如: 柔性屏幕)",
            "role": "KeyFeature",           // 选项: Subject, KeyFeature, Functional
            "feature_type": "Apparatus",    // 选项: Apparatus (侧重名词), Method (侧重动词)
            "zh_expand": ["柔性屏幕", "柔性显示器", "可折叠屏", "柔性面板", "AMOLED"], 
            "en_expand": ["flex+ screen", "flex+ display", "foldable panel", "bendable display"],
            "ipc_cpc_ref": ["G09F 9/30"]
          },
          ...
        ]
        """
        
        response = self.llm_service.chat_completion_json(
            model=settings.LLM_MODEL_REASONING,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context}
            ]
        )
        
        # 确保返回的是列表
        return response if isinstance(response, list) else []

    def _generate_cntxt_strategies(self, context: str) -> List[Dict]:
        """
        Step 2.1: 生成 CNTXT 中文检索策略
        侧重：基于 Block A/B/C 的逻辑组装、S/P/nD 精确算符、申请人清洗
        """
        logger.info("[SearchAgent] Generating CNTXT Strategies...")
        
        system_prompt = """
        你是一位**中国国家知识产权局 (CNIPA) 的资深审查员**。
        你的任务是基于提供的【检索要素积木 (Building Blocks)】，编写适用于 **CNTXT (中国专利全文数据库)** 的专业检索式。

        ### 输入数据说明 (积木定义)
        你将看到以下逻辑块，请严格按照各块的**角色定义**进行组合：
        1. **【Block A (Subject)】**: 技术领域/基础产品（如：无人机、轴承）。作为检索式的**基础主语**。
        2. **【Block B (KeyFeature)】**: 核心创新点/区别特征（如：折叠旋翼、陶瓷涂层）。这是检索式的**必须限制条件**。
        3. **【Block C (Functional)】**: 功能/效果（如：散热、减震）。主要用于扩展检索或实施例验证。
        4. **【背景约束】**: 包含申请人、发明人和 IPC 分类号。

        ### CNTXT 语法铁律
        1. **逻辑算符**: 仅使用 `AND`, `OR`, `NOT`。严禁使用 `+`, `-`。
        2. **位置算符 (核心武器)**:
           - `S` (同句): 最强位置限定。必须用于 **Block A (主语) 与 Block B (结构类特征)** 的组合。例: `(活塞 S 螺旋槽)`。
           - `nD` (词距): 灵活限定。用于 **Block B 中较长的修饰语** 内部组装。例: `(神经网络 5D 训练)`。
           - `P` (同段): 宽松限定。用于 **Block A (主语) 与 Block B/C (不同组件/功能)** 的组合。
           - **禁止**在全文(`/TXT`)中直接使用 `AND` 连接通用词，必须用 `P`或`S`。
        3. **字段**: `/TI` (名称), `/AB` (摘要), `/CLMS` (权利要求), `/TXT` (全文), `/PA` (申请人), `/IN` (发明人)。
        4. **长复合词拆解策略**：
            - 当 Block A 或 Block B 中出现长复合名词（如“谐波减速器柔性轴承试验台”），**严禁直接全词检索**。
            - 必须将其拆解为 **[修饰词]**、**[核心名词]**、**[功能/后缀]**。
            - 先用 `nD` 将 [修饰词] 与 [核心名词] 紧密连接，再用 `S` 将上述组合与 [功能/后缀] 连接。
            - *示例*: `((谐波 OR 柔性) 5D (轴承)) S (试验 OR 测试)`。

        ### 检索策略生成流程 (必须严格按步骤执行)

        #### Step 1: 追踪检索 (Inventor/Assignee Trace)
        - **核心原则**: 宁滥勿缺。除非申请人是巨头，否则**严禁使用技术关键词**进行限定。
        
        - **Query 1.1 (名称清洗与变体)**:
            - **CNTXT**: 使用申请人(PA)的**核心词**（去除 "Co., Ltd", "股份公司" 等后缀）。
            - 语法: `/PA=(核心词1 OR 核心词2) OR /IN=(发明人姓名)`
        
        - **Query 1.2 (巨头降噪策略)**:
          - 仅当申请人是知名大企业时，为了避免结果过多，使用 **IPC 小类 (Subclass, 4位)** 进行限定。
          - **绝对禁止**在此阶段使用具体的“结构关键词”，防止术语不一致导致的漏检。
          - 语法: `/PA=... AND (IPC前4位)/IPC`
           
        #### Step 2: 精确检索 (Precision) - [Block A + Block B 组合]
        - **目标**: 针对 X 类文献（新颖性），利用核心特征进行“切片打击”。
        - **原则**: 所有的词必须包含 `zh_expand` 扩展词。

        - **Query 2.1 (权利要求核心 / Claim Focus)**:
            - **逻辑**: 锁定最核心的创新结构。通常位于权利要求 1 中。
            - **组合**: **Block A (主语)** + **Block B (核心特征)**。
            - **语法**: 
                - 若 Block B 是结构词: `((Block A 扩展) S (Block B 扩展))/CLMS`
                - 若 Block B 是参数/长修饰: `((Block A 扩展) 10D (Block B 扩展))/CLMS`
            - *示例*: `((减速器 OR 减速机) S (柔性轴承))/CLMS`

        - **Query 2.2 (标题摘要突击 / Title-Abstract Focus)**:
            - **逻辑**: 如果特征出现在标题或摘要，必定是发明重点。
            - **组合**: **Block A** + **Block B**。
            - **语法**: `((Block A 扩展) AND (Block B 扩展))/TI/AB`
            - *注意*: 仅在此字段范围较小的情况下允许使用 `AND`。

        - **Query 2.3 (实施例复现 / Embodiment Re-enactment)**:
            - **逻辑**: 模拟说明书中具体实施方式的段落描述。
            - **组合**: **Block A** + **Block B** + **Block C (可选)**。
            - **语法**: `((Block A 扩展) P (Block B 扩展) P (Block C 扩展))/TXT`
            - *说明*: 使用 `P` 算符确保三者出现在同一自然段，模拟技术方案的完整描述。
           
        #### Step 3: 扩展检索 (Expansion) - [Block B/C + IPC 跨类]
        - **目标**: 针对 Y 类文献（创造性），寻找跨领域的启示。

        - **Query 3.1 (窄词宽类 / Specific Term + Broad Class)**:
            - **逻辑**: 忽略 Block A (主语)，只查 Block B (独特点) 在该 IPC 大类下的应用。
            - **组合**: **Block B (仅特征)** + **IPC (前4位)**。
            - **语法**: `((Block B 扩展)/TXT) AND (IPC前4位)/IPC`
            - *场景*: 当创新点是“一种特殊的密封结构”用在“泵”上时，此式可搜到该结构用在“阀”上的现有技术。

        - **Query 3.2 (功能泛化 / Functional Generalization)**:
            - **逻辑**: 忽略具体的 Block B (结构)，改查 Block C (功能/效果)。
            - **组合**: **Block A (主语)** + **Block C (功能)** + **IPC**。
            - **语法**: `((Block A 扩展) S (Block C 扩展)) AND (IPC)/IPC`
            - *场景*: 用“吸震”代替“橡胶垫”进行检索。

        ### 输出格式 (JSON List Only)
        必须严格输出标准的 JSON 列表，**严禁使用 Markdown 代码块 (```json)**，严禁包含任何解释性文字。结构如下：
        [
            {
                "name": "追踪检索",
                "description": "基于申请人/发明人的排查",
                "queries": [
                    { "db": "CNTXT", "step": "PA_Trace", "query": "..." }
                ]
            },
            {
                "name": "精确检索",
                "description": "Block A与Block B的结构化组合",
                "queries": [
                    { "db": "CNTXT", "step": "Claim_Focus", "query": "..." },
                    { "db": "CNTXT", "step": "TI_AB_Focus", "query": "..." },
                    { "db": "CNTXT", "step": "FullText_P_Logic", "query": "..." }
                ]
            },
            {
                "name": "扩展检索",
                "description": "基于Block C的功能性或跨领域检索",
                "queries": [
                    { "db": "CNTXT", "step": "KeyFeature_BroadIPC", "query": "..." },
                    { "db": "CNTXT", "step": "Functional_Search", "query": "..." }
                ]
            }
        ]
        """
        
        response = self.llm_service.chat_completion_json(
            model=settings.LLM_MODEL_REASONING,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context}
            ]
        )

        return response if isinstance(response, list) else []
    
    def _generate_ven_strategies(self, context: str) -> List[Dict]:
        """
        Step 2.2: 生成 VEN 英文检索策略 (中文 Prompt)
        侧重：基于 Block A/B/C 的逻辑组装、Patentese (专利英语)、截词符、拼音转换
        """
        logger.info("[SearchAgent] Generating VEN Strategies...")

        system_prompt = """
        你是一位**精通全球专利数据库 (VEN/Derwent/WIPO) 的资深检索专家** (如 USPTO 或 EPO 审查员)。
        你的任务是基于提供的【检索要素积木 (Building Blocks)】，编写高质量的**英文检索式**。
        
        ### 输入数据说明 (积木定义)
        请利用 Context 中提供的 `EN` (英文扩展词) 字段：
        1. **【Block A (Subject)】**: 技术领域/基础产品 (e.g., UAV, Bearing)。
        2. **【Block B (KeyFeature)】**: 核心创新点/区别特征 (e.g., Folding Wing, Ceramic Coating)。
        3. **【Block C (Functional)】**: 功能/效果 (e.g., Heat Dissipation, Damping)。

        ### VEN 数据库核心语法规范 (必须严格遵守)
        1.  **语言要求**：检索式内容必须全部为**英文** (包含拼音)。
        2.  **截词符 (查全率核心)**：
            -   **必须使用 `+`**：表示无限截断，覆盖复数、动名词。例: `detect+` (detect, detector, detecting)。
            -   **必须使用 `?`**：覆盖英美拼写差异。例: `alumin?um` (aluminium/aluminum), `mold` vs `mould` -> `mo?ld`.
        3.  **逻辑与位置算符**:
            -   `S` (Same Sentence): 同句算符。用于 **Block A (主语)** 与 **Block B (结构)** 的紧密结合。
            -   `P` (Same Paragraph): 同段算符。用于组合松散的技术组件。
            -   `/TI`, `/AB`, `/CLM`, `/DESC` (说明书), `/PA` (申请人), `/IN` (发明人).
        4.  **专利英语 (Patentese) 风格**:
            -   使用 `compris+` 而非 `include`.
            -   使用 `plurality` 而非 `many`.
            -   使用 `apparatus` 或 `device` 覆盖具体设备。

        ### 策略生成任务 (Step-by-Step)

        #### 1. [Trace] 追踪检索
        -   **场景**: 申请人/发明人追踪。
        -   **难点**: 中文名在海外通常使用拼音，或特定的英文商号。
        -   **动作**:
            -   将中文名转换为 **拼音 (Pinyin)**。
            -   如果 Context 中提供了英文商号 (如 DJI, BYD)，必须使用。
            -   *语法*: `(PA=(English_Name OR Pinyin) AND IN=(Pinyin))/PA/IN`

        #### 2. [Precision] 精确检索 (Block A + Block B)
        -   **场景**: X类文献（新颖性）排查。
        -   **策略 2.1: 权利要求突击 (Claim Focus)**:
            -   利用 `S` 算符锁定 Block A 与 Block B 在同一句中的描述。
            -   *语法*: `((Block A EN_Terms) S (Block B EN_Terms))/CLM`
            -   *示例*: `((drone+ OR UAV) S (fold+ S wing+))/CLM`
        -   **策略 2.2: 标题摘要组合 (Title/Abstract)**:
            -   最直观的特征组合。
            -   *语法*: `((Block A EN_Terms) AND (Block B EN_Terms))/TI/AB`
        -   **策略 2.3: 实施例复现 (Description/Full Text)**:
            -   利用 `P` 算符模拟实施例段落。
            -   *语法*: `((Block A EN_Terms) P (Block B EN_Terms) P (Block C EN_Terms))/DESC`

        #### 3. [Expansion] 扩展检索 (Block B/C + CPC/IPC)
        -   **场景**: Y类文献（创造性）及跨领域搜索。
        -   **策略 3.1: 核心特征跨类 (Broad Class)**:
            -   忽略 Block A (主语)，仅搜索 Block B (特征) + 分类号。
            -   优先使用 CPC (如果 Context 中有)，否则使用 IPC (前4位)。
            -   *语法*: `((Block B EN_Terms) AND (CPC OR IPC))/IPC/CPC`
        -   **策略 3.2: 功能性检索 (Functional)**:
            -   搜索 Block C (功能词) 替代 Block B (结构词)。
            -   *语法*: `((Block A EN_Terms) AND (Block C EN_Terms))/TI/AB/CLM`

        ### 输出格式 (JSON List Only)
        必须严格输出标准的 JSON 列表，**严禁使用 Markdown 代码块 (```json)**，严禁包含任何解释性文字。结构如下：
        [
            {
                "name": "追踪检索",
                "description": "申请人/发明人拼音及英文变体排查",
                "queries": [
                    { 
                        "db": "VEN", 
                        "step": "申请人与发明人组合追踪", 
                        "query": "PA=(Huawei OR Hua Wei) AND IN=(Ren Zhengfei)" 
                    }
                ]
            },
            {
                "name": "精确检索",
                "description": "基于Block A/B组合的精准打击(Patentese)",
                "queries": [
                    { 
                        "db": "VEN", 
                        "step": "权利要求同句检索(S算符)",
                        "query": "((piston+ S groove+) S (seal+))/CLM" 
                    },
                    { 
                        "db": "VEN", 
                        "step": "标题摘要特征组合",
                        "query": "((drone+ OR UAV) AND (fold+ S wing+))/TI/AB" 
                    }
                ]
            },
            {
                "name": "扩展检索",
                "description": "基于CPC分类号的跨领域检索",
                "queries": [
                    {
                        "db": "VEN",
                        "step": "核心特征加分类号跨类搜索",
                        "query": "((unique_feature+) AND (B64C))/IPC"
                    }
                ]
            }
        ]
        """

        response = self.llm_service.chat_completion_json(
            model=settings.LLM_MODEL_REASONING,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context}
            ]
        )
        
        return response if isinstance(response, list) else []

    def _generate_npl_strategies(self, context: str) -> List[Dict]:
        """
        Step 2.3: 生成 NPL 非专利文献策略
        侧重：学术术语翻译、去专利化、CNKI/Scholar 专用语法
        """
        logger.info("[SearchAgent] Generating NPL Strategies...")

        system_prompt = """
        你是一位 **CNKI / Google Scholar 的资深学术检索专家**。
        你的任务是将专利检索要素（Building Blocks）“翻译”为学术检索式。

        ### 输入数据说明 (积木定义)
        1. **【Block A (Subject)】**: 研究对象/应用场景 (e.g., UAV, Tumor Detection)。
        2. **【Block B (KeyFeature)】**: 核心技术手段/算法/材料 (e.g., YOLOv5, Graphene, PID Control)。
        3. **【Block C (Functional)】**: 优化目标/实验指标 (e.g., Accuracy, Latency, BER)。

        ### 核心任务：去专利化 (De-Patentize) & 学术化
        专利喜欢用通用词，论文喜欢用具体词。请执行以下**翻译**：
        1. **通用结构 -> 具体硬件**: 
            - "Control unit" -> "MCU", "FPGA", "DSP", "ARM".
            - "Storage means" -> "DDR", "Flash", "Database".
        2. **通用方法 -> 具体算法/模型**:
            - "Machine learning" -> "CNN", "Transformer", "SVM", "ResNet".
            - "Optimization" -> "Genetic Algorithm", "Simulated Annealing".
        3. **通用材料 -> 化学式/学名**:
            - "Conductive material" -> "Graphene", "CNT", "Ag Nanowire".

        ### 数据库语法规范
        1. **结构化数据库 (CNKI, WanFang)**:
            - 支持布尔逻辑 (`AND`, `OR`).
            - 字段: `TI` (篇名), `KY`/`KW` (关键词), `AB` (摘要).
            - *CNKI语法*: `TI=('词A' * '词B')` (注: * 表示 AND).
        2. **非结构化引擎 (Google Scholar)**:
            - **不支持** `TI=` 或 `KY=`.
            - 使用自然语言关键词组合。
            - **强制短语**使用双引号 `""`. (e.g., `"Deep Learning"`).
            - 使用 `intitle:` 指令 (可选).

        ### 策略生成流程

        #### 1. [Title/Keyword] 核心技术精准检索
        - **目标**: 命中“A领域的B技术研究”类论文。
        - **组合**: **Block A (学术词)** + **Block B (学术词)**。
        - **策略**: 
            - CNKI: `TI=(Block A * Block B)`
            - Scholar: `intitle:"Block B" "Block A"` (Block B 更重要，放前面)

        #### 2. [Algo/Material] 算法或材料专项检索
        - **目标**: 针对 Block B 是具体算法或材料的情况，忽略应用场景，查原理性文献。
        - **组合**: **Block B (具体的算法名/化学式)**。
        - **策略**:
            - CNKI: `KY=(Block B Extended)` (搜关键词字段)
            - Scholar: `"Specific Algorithm Name" OR "Specific Material"`

        #### 3. [Problem/Solution] 问题导向检索
        - **目标**: 寻找解决相同 Block C (功能/问题) 的不同技术路线。
        - **组合**: **Block A (场景)** + **Block C (功能/指标)**。
        - **策略**:
            - 学术界常在标题中写“基于...的优化”或“...性能分析”。
            - Query: `(Block A) AND (Block C)` (重点关注综述 Review 类文章)。

        ### 输出格式 (JSON List Only)
        必须严格输出标准的 JSON 列表，**严禁使用 Markdown 代码块 (```json)**，严禁包含任何解释性文字。结构如下：
        [
            {
                "name": "NPL 非专利检索",
                "description": "针对学术数据库的术语检索(去专利化)",
                "queries": [
                    { 
                        "db": "CNKI", 
                        "step": "题名精准(学术词)", 
                        "query": "TI=('无人机' + 'UAV') * ('卡尔曼滤波' + 'Kalman Filter')" 
                    },
                    { 
                        "db": "Google Scholar", 
                        "step": "自然语言组合", 
                        "query": "intitle:\"Visual Odometry\" drone OR UAV" 
                    }
                ]
            }
        ]
        """

        response = self.llm_service.chat_completion_json(
            model=settings.LLM_MODEL_REASONING,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context}
            ]
        )

        return response if isinstance(response, list) else []
    
    def _inject_semantic_strategy(self, strategy_plan: Dict) -> None:
        # 1. 获取技术手段文本
        tech_means = self.report_data.get("technical_means", "")
        if not tech_means:
            # 降级策略：如果 technical_means 为空，使用 technical_scheme 或 abstract
            tech_means = self.report_data.get("technical_scheme") or self.report_data.get("ai_abstract", "")
        
        # 2. 构造语义检索节点结构
        semantic_strategy = {
            "name": "语义检索",
            "description": "基于核心技术手段的自然语言输入，用于智能检索系统快速圈定X类文献。",
            "queries": [
                {
                    "db": "Smart Search",
                    "step": "自然语言输入",
                    "query": tech_means
                }
            ]
        }

        # 3. 插入到策略列表头部
        if isinstance(strategy_plan, dict):
            # 确保 strategies 键存在且为列表
            if "strategies" not in strategy_plan or not isinstance(strategy_plan["strategies"], list):
                strategy_plan["strategies"] = []
            
            # 插入到索引 0
            strategy_plan["strategies"].insert(0, semantic_strategy)
        # 如果 strategy_plan 为空或异常，初始化它
        else:
            strategy_plan = { "strategies": [semantic_strategy] }