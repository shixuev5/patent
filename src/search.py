import concurrent.futures
from typing import Dict, Any, List
from loguru import logger
from src.llm import get_llm_service
from config import settings

class SearchStrategyGenerator:
    def __init__(self, patent_data: Dict, report_data: Dict):
        self.llm_service = get_llm_service()
        self.patent_data = patent_data
        self.report_data = report_data

        self.base_ipcs = self.patent_data.get("bibliographic_data", {}).get("ipc_classifications", [])

    def generate_strategy(self) -> Dict[str, Any]:
        """
        主入口：执行两阶段检索策略生成
        """
        logger.info("开始构建检索策略...")

        # 初始化默认返回结构
        search_matrix = []
        strategy_plan = {"strategies": []}

        try:
            # Stage 1: 检索要素表 (Search Matrix) - 深度扩展
            matrix_context = self._build_matrix_context()
            search_matrix = self._build_search_matrix(matrix_context)

            if not search_matrix:
                logger.warning("Search Matrix generation returned empty.")
            
            try:
                if search_matrix:
                    # Stage 2: 检索式构建 (Query Formulation) - 分库分治
                    query_context = self._build_query_context(search_matrix)
                    
                    # 使用 ThreadPoolExecutor 进行并行调用
                    # max_workers=3 对应三个独立的数据库策略生成任务
                    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                        logger.info("启动并行任务生成各库检索策略...")
                        
                        # 提交任务
                        future_cntxt = executor.submit(self._generate_cntxt_strategies, query_context)
                        future_ven = executor.submit(self._generate_ven_strategies, query_context)
                        future_npl = executor.submit(self._generate_npl_strategies, query_context)

                        # 获取结果 (result() 会阻塞直到任务完成)
                        # 注意：这里按顺序获取结果，以保证最终列表的顺序符合逻辑 (CNTXT -> VEN -> NPL)
                        cntxt_strategies = future_cntxt.result()
                        ven_strategies = future_ven.result()
                        npl_strategies = future_npl.result()

                    # 合并所有策略
                    combined_strategies = self._merge_strategies(
                        cntxt_strategies, 
                        ven_strategies, 
                        npl_strategies
                    )
                    
                    # 包装为最终对象
                    strategy_plan = {"strategies": combined_strategies}

            except Exception as e_stage2:
                logger.error(f"Stage 2 Query generation failed: {e_stage2}")
        
        except Exception as e_stage1:
            logger.error(f"Stage 1 Matrix generation failed: {e_stage1}")

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
        阶段一上下文：专注技术理解与同义词扩展
        """
        biblio = self.patent_data.get("bibliographic_data", {})
        
        distinguishing_features = [
            f"- {f['name']}: {f.get('description', '')}"
            for f in self.report_data.get("technical_features", []) 
            if f.get("is_distinguishing", False) is True
        ]
        
        return f"""
        [发明名称] {biblio.get('invention_title')}
        [IPC参考] {', '.join(self.base_ipcs[:3])} 

        [技术手段] {self.report_data.get('technical_means')}

        [需检索的关键区别特征 (Focus Here)]:
        {chr(10).join(distinguishing_features)}
        """
    
    def _build_query_context(self, matrix: List[Dict]) -> str:
        """
        阶段二上下文：专注逻辑组装
        包含：Matrix(原料), Applicants(用于追踪)
        """
        biblio = self.patent_data.get("bibliographic_data", {})

        # 将 Matrix 格式化为 LLM 容易理解的逻辑块
        matrix_text = []
        for item in matrix:
            tag = "[CORE]" if item.get('is_essential') else "[OPTIONAL]"
            f_type = item.get('type', 'General')
            matrix_text.append(
                f"{tag} {item['concept_key']} ({f_type}):\n"
                f"   - ZH: {', '.join(item.get('zh_expand', []))}\n"
                f"   - EN: {', '.join(item.get('en_expand', []))}\n"
                f"   - IPC: {', '.join(item.get('ipc_cpc_ref', []))}"
            )
        
        formatted_matrix = "\n".join(matrix_text)

        applicants = ', '.join([a['name'] for a in biblio.get('applicants', [])][:3])
        inventors = ', '.join(biblio.get('inventors', [])[:5])

        return f"""
        【检索要素积木 (Building Blocks)】:
        {formatted_matrix}

        【背景约束】:
        - 现有IPC: {', '.join(self.base_ipcs)}
        - 申请人 (PA): {applicants}
        - 发明人 (IN): {inventors}
        """

    def _build_search_matrix(self, context: str) -> List[Dict]:
        """
        阶段一：概念拆解与多语言扩展
        """
        logger.info("[SearchAgent] Step 1: Building Search Matrix...")
        
        system_prompt = """
        你是一位经验丰富的专利检索专家，精通中英文专利术语。请基于提供的信息，提取 3-5 个核心检索要素，构建一份**全要素扩展检索词表**。

        ### 核心原则：
        1. **要素选择**: 必须区分 **核心要素 (Essential)** 和 **辅助要素**。
           - Essential: 构成技术方案必不可少的结构或步骤。
        2.  **中文扩展 (针对 CNTXT)**：
            - 必须包含：原文术语，同义词，常见缩写。
            - 建议包含：上位概念 (如“紧固件”对“螺钉”)，下位概念 (如“螺钉”对“螺栓”)，行业俗称。
            - 必须提供至少 3-5 个中文扩展词。
        3.  **英文扩展 (针对 VEN)**：
            - 必须使用**专业专利英语 (Patentese)**。
            - 包含：美式/英式拼写变体，词根截断 (如 `comput+`, `connect+`, `test+`)，常用缩写。
            - 必须提供至少 3-5 个英文扩展词。
        4.  **分类号关联 (IPC & CPC)**：
            - 针对每个核心概念，尝试匹配最相关的 IPC 或 CPC 小组（Subgroup）。
            - **精度要求**：尽可能精确到小组 (Subgroup)，例如 `F16H 1/28`。
            - **留空规则**：如果某个概念非常通用（如“控制方法”、“系统”），或者你无法确定极高置信度的具体分类号，**请务必返回空列表 `[]`**。

        ### 输出格式 (JSON List Only)
        仅输出 JSON 列表，无 Markdown 标记：
        [
          {
            "concept_key": "核心概念A (如: 谐波减速器)",
            "is_essential": true,         // 是否为核心必要特征
            "type": "Structure",          // 选项: Structure, Method, Material, Function
            "zh_expand": ["谐波减速器", "谐波传动", "柔轮", "刚轮"], 
            "en_expand": ["harmonic drive", "harmonic gear", "flexspline", "flex?spline"],
            "ipc_cpc_ref": ["F16H 49/00", "F16H 1/00"] <-- 若不存在或不确定，则填 []
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
        侧重：丰富的中文同义词、精确的 S/D 位置算符、申请人中文清洗
        """
        logger.info("[SearchAgent] Generating CNTXT Strategies...")
        
        system_prompt = """
        你是一位**中国国家知识产权局 (CNIPA) 的资深审查员**。
        你的任务是基于提供的【检索要素积木】，编写适用于 **CNTXT (中国专利全文数据库)** 的专业检索式。

        ### CNTXT 语法铁律
        1. **逻辑算符**: 仅使用 `AND`, `OR`, `NOT`。严禁使用 `+`, `-`。
        2. **位置算符 (核心武器)**:
           - `S` (同句): 用于强相关的【结构+修饰】或【结构+功能】。例: `(活塞 S 螺旋槽)`。
           - `nD` (词距): 限定两词间隔不超过 n 个词 (n=1-20)。比 `S` 灵活，用于应对修饰语较长的情况。例: `(神经网络 5D 训练)`。
           - `P` (同段): 用于组合不同的技术组件。例: `(雷达 P 摄像头 P 融合)`。
           - **禁止**在全文(`/TXT`)中直接使用 `AND` 连接通用词，必须用 `P`或`S`。
        3. **字段**: `/TI` (名称), `/AB` (摘要), `/CLMS` (权利要求), `/TXT` (全文), `/PA` (申请人), `/IN` (发明人)。
        4. **长复合词拆解策略**：
            - 对于核心概念或其中文扩展过长的情况（如“谐波减速器柔性轴承试验台”），**严禁直接全词检索**。
            - **必须**拆解为多个核心词，并使用 `S` 或 `nD` 算符连接。
            - **优先嵌套使用**：先用 `nD` 将修饰词与核心名词组合，再用 `S` 连接功能/测试词。
            - *示例*: `((谐波 OR 柔性) nD (轴承)) S (试验 OR 测试)`。

        ### 检索策略生成流程 (按顺序执行，体现“由严到宽，层层递进”的逻辑)

        #### Step 1: 追踪检索 (Inventor/Assignee Trace)
        - **核心原则**: 宁滥勿缺。除非申请人是巨头，否则**严禁使用技术关键词**进行限定。
        
        - **Query 1.1 (名称清洗与变体)**:
          - **CNTXT**: 使用申请人(PA)的**核心词**（去除 "Co., Ltd", "股份公司" 等后缀）。
            - 语法: `/PA=(核心词1 OR 核心词2) OR /IN=(发明人姓名)`
        
        - **Query 1.2 (巨头降噪策略)**:
          - 仅当申请人是知名大企业时，为了避免结果过多，使用 **IPC 小类 (Subclass, 4位)** 进行限定。
          - **绝对禁止**在此阶段使用具体的“结构关键词”，防止术语不一致导致的漏检。
          - 语法: `/PA=... AND (IPC前4位)/IPC`
           
        #### Step 2: 精确检索 (Precision / Novelty) - [针对 X 类文献]
        - **核心理念**: **“切片打击”与“降维打击”**。严禁试图用一条 Query 命中所有特征。必须将技术方案拆解为“特征对”进行验证。
        - **必须执行**: 所有检索词**必须**包含 `zh_expand` 同义词扩展 (e.g., `(A OR A1 OR A2)` )。

        - **Query 2.1 (黄金特征对 / Golden Pair - Claim Focus)**:
          - **战术**: 选取 Matrix 中标记为 `[CORE]` 的【结构】与该结构的【状态/修饰/功能】。
          - **场景**: 专门针对权利要求书 (`/CLMS`)。这是审查员判定新颖性的首选。
          - **语法**: 
            - CNTXT: `((核心结构扩展) S (核心修饰扩展))/CLMS`
          - *示例*: 核心是“活塞”，修饰是“螺旋槽”。Query: `((活塞 OR 柱塞) S (螺旋 OR 旋槽))/CLMS`。
          - *目的*: 锁定最核心的技术改进点，利用 `S` (同句) 算符排除无关噪音。

        - **Query 2.2 (主语+独特点 / Subject + Distinguishing Feature)**:
          - **战术**: 选取【产品名称/技术领域】+【最具区别性的特征词】。
          - **场景**: 针对标题 (`/TI`) 和摘要 (`/AB`)。如果一个特征被写在标题或摘要里，它一定是该文献的改进重点。
          - **语法**:
            - `((产品词扩展) AND (独特特征词扩展))/TI/AB`
          - *示例*: 产品是“无人机”，独特点是“折叠旋翼”。Query: `((无人机 OR UAV) AND (折叠 S 旋翼))/TI/AB`。
          - *注意*: 这里可以使用 `AND`，因为字段范围仅限 TI/AB，噪音可控。

        - **Query 2.3 (实施例复现 / Embodiment Re-enactment)**:
          - **战术**: 假设对比文件中有一个具体的实施例段落描述了你的方案。
          - **场景**: 全文 (`/TXT`) 或 说明书 (`/DESC`)。
          - **语法**: 
            - **必须使用 P (同段) 算符**。严禁在全文检索中使用 `AND` 连接通用词。
            - 格式: `((要素A扩展) P (要素B扩展) P (要素C扩展))/TXT`
          - *逻辑*: 只有当 Feature A, B, C 出现在同一自然段时，它们才更有可能属于同一个技术实施例。
           
        #### Step 3: 扩展检索 (Expansion Search) - [针对 Y 类文献](创造性)
        - **核心理念**: 寻找“跨领域”的技术启示。采用“一松一紧”策略。
        
        - **Query 3.1 (窄词宽类 / Specific Term + Broad Class)**:
          - **适用场景**: 针对具象的、独特的区别特征（如具体装置名）。
          - **逻辑**: 放宽分类号限制，去邻近领域寻找。
          - **语法**: `(独特特征词) AND (IPC大类 only)` 
          - *说明*: IPC 仅保留前 4 位 (如 F16H)，不要精确到小组。如果特征词极度生僻，甚至可以不加 IPC。
        
        - **Query 3.2 (功能泛化 / Functional Generalization)**:
          - **适用场景**: 针对区别特征是改用常见部件（如螺栓换铆钉）。
          - **逻辑**: 不搜结构词，搜“功能词”或“效果词”。
          - **语法**: `(功能/效果词) AND (技术领域词) AND (IPC/CPC)`
          - *示例*: 原特征是“橡胶垫”，扩展搜 `(缓冲 OR 吸震) AND (支架)`.
          - *目的*: 命中那些结构不同但功能相同的现有技术。

        ### 输出格式 (JSON Only)
        必须返回合法的 JSON 格式，不要包含 Markdown 标记。结构如下：
        [
            {
                "name": "追踪检索",
                "description": "申请人/发明人排查（去后缀，含拼音，无关键词）",
                "queries": [
                    { 
                        "db": "CNTXT", 
                        "step": "申请人去后缀", 
                        "query": "/PA=(比亚迪 OR BYD) OR /IN=(王传福)" 
                    },
                    { 
                        "db": "CNTXT", 
                        "step": "IPC领域限定(针对大户)", 
                        "query": "/PA=(比亚迪) AND (B60L)/IPC" 
                    },
                    ...
                ]
            },
            {
                "name": "精确检索",
                "description": "基于权利要求和核心实施例的精确打击",
                "queries": [
                    { 
                        "db": "CNTXT", 
                        "step": "黄金特征对(Claim)", 
                        "query": "((结构词扩展) S (功能词扩展))/CLMS"
                    },
                    ...
                    { 
                        "db": "CNTXT", 
                        "step": "全文段落级组合", 
                        "query": "((特征A) P (特征B))/TXT"
                    }
                ]
            },
            {
                "name": "扩展检索",
                "description": "结合分类号的跨领域检索",
                "queries": [
                    { 
                        "db": "CNTXT", 
                        "step": "窄词宽类(Broad IPC)", 
                        "query": "((独特特征词扩展) /TXT) AND (IPC大类 e.g. B60L)/IPC" 
                    },
                    ...
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
        侧重：Patentese (专利英语)、截词符应用、拼音转换、同段/同句算符
        """
        logger.info("[SearchAgent] Generating VEN Strategies...")

        system_prompt = """
        你是一位**精通全球专利数据库 (VEN/Derwent) 的资深检索专家** (如 USPTO 或 EPO 审查员)。
        你的任务是基于提供的上下文，编写高质量的**英文检索式**，但**策略名称、描述和步骤名必须是中文**。

        ### VEN 数据库核心语法规范 (必须严格遵守)
        1.  **语言要求**：检索式内容必须全部为**英文** (包含拼音)。
        2.  **截词符 (查全率核心)**：
            -   **必须使用 `+`**：表示无限截断。例如 `detect+` 可命中 detect, detector, detection, detecting。
            -   **必须使用 `?`**：表示0-1个字符。注意**英美拼写差异** (e.g. `fiber` vs `fibre`, `mold` vs `mould`)，必须使用 `?` 覆盖，例如 `fib??`, `mo?ld`。
            -   **规则**：几乎所有实词词根后都应加 `+` 以覆盖复数和动名词形式。
        3.  **逻辑与位置算符**：
            -   `AND`, `OR`, `NOT`。
            -   `S` (Same Sentence): 词与词在同一句。用于强关联 (e.g., `(piston+ S seal+)`).
            -   `P` (Same Paragraph): 词与词在同一段。用于组合不同组件 (e.g., `(motor+ P sensor+ P control+)`).
        4.  **字段标识**：`/TI` (标题), `/AB` (摘要), `/CLM` (权利要求), `/DESC` (说明书全文), `PA` (申请人).

        ### 策略生成任务 (Step-by-Step)

        #### 1. [Global Trace] 申请人/发明人追踪
        -   **核心难点**：中文申请人/发明人在海外申请时会使用**拼音**或**英文译名**。
        -   **执行动作**：
            -   将中文名转换为 **拼音 (Pinyin)**。
            -   如果已知英文商号 (如 BYD)，一并加入。
            -   *示例*: 原文"王传福", Query: `IN=(Wang Chuanfu)`; 原文"比亚迪", Query: `PA=(BYD OR Bi Ya Di)`.

        #### 2. [Precision Focus] 核心特征精确检索 (X类)
        -   **场景**：判定新颖性。
        -   **战术**：
            -   选取 `[CORE]` 核心要素。
            -   利用 `S` 算符锁定 Claim 中的"黄金特征对"。
            -   利用 `/TI/AB` 锁定标题和摘要中的改进点。
            -   *必须使用专利英语 (Patentese)*: 如用 `fasten+` 代替 `hold`, 用 `plurality` 代替 `many`.

        #### 3. [Expansion] 宽泛/功能检索 (Y类)
        -   **场景**：寻找跨领域的创造性启示。
        -   **战术**：仅保留最独特的特征词，结合 IPC 分类号 (只取前4位, 如 F16H) 进行限制。

        ### 输出格式 (JSON List Only)
        必须返回 JSON 列表，结构如下：
        [
            {
                "name": "追踪检索",
                "description": "申请人/发明人排查（含拼音及英文变体）",
                "queries": [
                    { 
                        "db": "VEN", 
                        "step": "申请人拼音", 
                        "query": "PA=(Huawei OR Hua Wei) AND IN=(Ren Zhengfei)" 
                    },
                    ...
                ]
            },
            {
                "name": "精确检索",
                "description": "基于同句算符(/CLM)和标题摘要(/TI/AB)的核心特征验证",
                "queries": [
                    { 
                        "db": "VEN", 
                        "step": "黄金特征对(Claim)",
                        "query": "((piston+ S groove+) AND (seal+ S ring+))/CLM" 
                    },
                    ...
                    { 
                        "db": "VEN", 
                        "step": "标题摘要组合",
                        "query": "((drone+ OR UAV) AND (fold+ S wing+))/TI/AB" 
                    }
                ]
            },
            {
                "name": "扩展检索",
                "description": "关键词结合IPC分类号进行宽泛搜索",
                "queries": [
                    {
                        "db": "VEN",
                        "step": "窄词宽类",
                        "query": "((unique_feature+) AND (F16H))/IPC"
                    },
                    ...
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
        侧重：学术术语、具体算法名、CNKI/Google Scholar 语法
        """
        logger.info("[SearchAgent] Generating NPL Strategies...")

        system_prompt = """
        你是一位 **CNKI / Google Scholar 的学术检索专家**。
        你的任务是将专利概念“翻译”为学术检索式。

        ### 转换原则
        1. **去专利化**: 不要用 "plurality of", "device", "means" 等专利词汇。
        2. **学术化**: 使用具体的**算法名称** (e.g., CNN, LSTM, PID)、**化学式**、**材料学名**。
        3. **语法简化**: 
           - **CNKI/IEEE**: 使用 `TI=(词A AND 词B)`。
           - **Google Scholar**: **不支持 `TI=`**。直接使用关键词组合。如果需要精确短语，使用双引号。

        ### 策略生成要求
        - **(题名/关键词精准打击)**:
          - **目标**: CNKI, IEEE, Google Scholar。
          - **策略**: 仅使用 2 个最具体的学术名词，限定在标题(TI)或关键词(KY)中。
          - *示例*: 专利写“数据处理装置”，NPL搜“FPGA”或“DSP”；专利写“神经网络”，NPL搜“CNN”或“LSTM”。

        - **(算法/工艺-场景组合)**:
          - **目标**: 针对方法类或材料类申请。
          - **语法**: `(具体的算法名/工艺名) AND (具体应用场景)`

        ### 输出格式 (JSON Only)
        必须返回合法的 JSON 格式，不要包含 Markdown 标记。结构如下：
        [
            {
                "name": "NPL 非专利检索",
                "description": "针对学术数据库(CNKI/IEEE/Scholar)的术语检索",
                "queries": [
                    { 
                        "db": "CNKI/IEEE", 
                        "step": "题名精准(学术词)", 
                        "query": "TI=(Specific_Algo_Name AND Specific_Material)" 
                    },
                    { 
                        "db": "Google Scholar", 
                        "step": "自然语言组合", 
                        "query": "\"Specific Concept\" \"Application Field\"" 
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