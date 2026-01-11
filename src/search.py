import json
from typing import Dict, Any, List
from loguru import logger
from src.llm import get_llm_service
from config import settings

class SearchStrategyGenerator:
    def __init__(self, patent_data: Dict, report_data: Dict):
        self.llm_service = get_llm_service()
        self.patent_data = patent_data
        self.report_data = report_data

    def generate_strategy(self) -> Dict[str, Any]:
        """
        主入口：执行两阶段检索策略生成
        """
        logger.info("开始构建检索策略...")

        try:
            # Stage 1: 检索要素表 (Search Matrix) - 深度扩展
            matrix_context = self._build_matrix_context()
            search_matrix = self._build_search_matrix(matrix_context)
            
            # Stage 2: 检索式构建 (Query Formulation)
            query_context = self._build_query_context(search_matrix)
            strategy_plan = self._build_queries(query_context)

            # --- 手动注入语义检索策略 (Semantic Search Injection) ---
            # 1. 获取技术手段文本
            tech_means = self.report_data.get("technical_means", "未提取到技术手段")
            
            # 2. 构造语义检索节点结构
            semantic_strategy = {
                "name": "语义检索",
                "description": "基于核心技术手段的自然语言输入，用于智能检索系统快速圈定X类文献。",
                "queries": [
                    {
                        "db": "Smart Search",
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

            # 合并结果
            return {
                "search_matrix": search_matrix, # 对应你要求的中文、中文扩展、英文翻译
                "search_plan": strategy_plan    # 具体的检索步骤和分析
            }

        except Exception as e:
            logger.error(f"Search strategy generation failed: {e}")
            return { "search_matrix": [], "search_plan": {} }

    def _build_matrix_context(self) -> str:
        """
        阶段一上下文：专注技术理解与同义词扩展
        """
        biblio = self.patent_data.get("bibliographic_data", {})
        
        # 仅保留特征名称和简要描述，帮助理解术语含义
        essential_features_summary = [
            f"- {f['name']}: {f.get('description', '')}"
            for f in self.report_data.get("technical_features", []) 
            if f.get("is_essential", True)
        ]
        
        return f"""
        [发明名称] {biblio.get('invention_title')}
        [IPC主分类] {', '.join(biblio.get('ipc_classifications', [])[:3])} 
        [技术问题] {self.report_data.get('technical_problem')}
        [技术手段] {self.report_data.get('technical_means')}
        [需检索的核心特征]:
        {chr(10).join(essential_features_summary)}
        """
    
    def _build_query_context(self, matrix: List[Dict]) -> str:
        """
        阶段二上下文：专注逻辑组装
        包含：Matrix(原料), Applicants(用于追踪)
        """
        biblio = self.patent_data.get("bibliographic_data", {})
        matrix_str = json.dumps(matrix, ensure_ascii=False, indent=2)

        # 仅提取特征名称用于对应逻辑，不需要描述
        feature_names = [
            f"- Feature: {f['name']}"
            for f in self.report_data.get("technical_features", []) 
            if f.get("is_essential", True)
        ]

        return f"""
        【检索要素表 (Search Matrix)】:
        {matrix_str}

        【数据填充源】:
        1. [分类号限定] (IPC/CPC Limit):
           {', '.join(biblio.get('ipc_classifications', []))}

        2. [追踪检索对象] (Trace Targets):
           - Applicants (PA): {', '.join([a['name'] for a in biblio.get('applicants', [])])}
           - Inventors (IN): {', '.join(biblio.get('inventors', []))}
        
        3. [逻辑组合参考] (Features to Combine):
           {chr(10).join(feature_names)}
        """

    def _build_search_matrix(self, context: str) -> List[Dict]:
        """
        阶段一：概念拆解与多语言扩展
        """
        logger.info("[SearchAgent] Step 1: Building Search Matrix...")
        
        system_prompt = """
        你是一位经验丰富的专利检索专家，精通中英文专利术语。请提取 3-5 个核心检索要素，并构建一份**全要素扩展检索词表**。

        ### 核心原则：最大限度涵盖“规避设计”的术语
        1.  **不仅仅是翻译**：要思考竞争对手可能使用的“同义词”、“近义词”、“上位/下位概念”、“行业俗称”，甚至“规避性描述”。
        2.  **中文扩展 (针对 CNTXT)**：
            - 必须包含：原文术语，同义词，常见缩写。
            - 建议包含：上位概念 (如“紧固件”对“螺钉”)，下位概念 (如“螺钉”对“螺栓”)，行业俗称。
            - 必须提供至少 3-5 个中文扩展词。
        3.  **英文扩展 (针对 VEN)**：
            - 必须使用**专业专利英语 (Patentese)**。
            - 包含：美式/英式拼写变体，词根截断 (如 `comput+`, `connect+`, `test+`)，常用缩写。
            - 必须提供至少 3-5 个英文扩展词。
        4.  **分类号关联 (IPC & CPC)**：
            - 针对每个核心概念，给出最相关的 IPC 和 CPC 分类号。
            - **精度要求**：尽可能精确到小组 (Subgroup)，例如 `F16H 1/28`。
            - **留空规则**：如果该概念非常通用（如“方法”、“系统”）或无法确定特定分类，数组请保留为空 `[]`。

        ### 输出格式 (JSON List Only)
        仅输出 JSON 列表，无 Markdown 标记：
        [
          {
            "concept_key": "核心概念A (如: 谐波减速器)",
            "zh_expand": ["谐波减速器", "谐波传动", "柔轮", "刚轮"], 
            "en_expand": ["harmonic drive", "harmonic gear", "flexspline", "flex?spline"],
            "ipc": ["F16H 49/00", "F16H 1/00"], 
            "cpc": ["F16H 49/001"]
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

    def _build_queries(self, context: str) -> Dict:
        """
        阶段二：构建逻辑检索式（逻辑专家模式）
        """
        logger.info("[SearchAgent] Step 2: Formulating Queries...")

        role_definition = """
        你是一位经验丰富的专利审查员，拥有 20 年以上在中国国家知识产权局 (CNTXT) 和全球专利数据库 (VEN) 的检索实战经验。
        你的任务是基于提供的【待审专利档案】和【检索要素表】，生成一份**详尽、实战、可直接复制粘贴**的检索策略。
        """

        syntax_rules = """
        ### 数据库语法规范 (必须严格遵守)
        
        **1. 逻辑算符 (Unified Boolean)**：
        - 必须使用标准布尔算符：`AND`, `OR`, `NOT`。
        - **严禁**使用 `+`, `*`, `-` 作为逻辑连接符。
        - 示例: `(A OR B) AND C`。

        **2. 位置算符 (中英文库通用)**：
        - **同句检索**：使用 `S`。表示两个词必须出现在同一句中。
          - 格式: `(词A S 词B)`
        - **同段/距离检索**：使用 `D`。表示两个词在一定距离内或同一段落。
          - 格式: `(词A D 词B)`
        - **说明**：在精确检索核心特征组合时，优先使用 `S` 算符以提高相关度。

        **3. 字段标识**：
        - `/TI` (标题), `/AB` (摘要), `/CLMS` (权利要求), `/KW` (关键词), `/TXT` (全文)。
        - 关键词位置: 放在关键词之后，例如 `(螺钉 OR 螺栓)/TI/KW`。

        **4. 截词符 (VEN 英文)**：
        - 词根后加 `?` 或 `+` (例如 `comput+`)。

        **5. CPC/IPC 格式**: 
        - 必须精确到**大组或小组** (Subgroup Level)，例如 `G06F 16/30`。
        """

        process_steps = """
        ### 检索策略生成流程 (按顺序执行)

        #### Step 1: 追踪检索 (Inventor/Assignee Trace)
        - **目标**：排查申请人 (PA) 和发明人 (IN) 的自引、同族申请或既往技术。
        - **数据库**：CNTXT, VEN。
        - **语法**：
            - CNTXT: `/PA=XXX` (申请人), `/IN=XXX` (发明人)。
            - VEN: `PA=(XXX)` (Assignee), `IN=(XXX)` (Inventor)。
        - **策略**：将申请人/发明人名称与核心关键词结合。

        #### Step 2: 精确检索 (Precision Search) - [针对 X 类文献](新颖性)
        - **逻辑**：选取 2-3 个**最核心的必要技术特征**，进行高精度组合。
        - **CNTXT 策略**：强行使用 `S` (同句) 算符。
          - *Template*: `(核心特征A扩展 OR 特征A2) S (核心特征B扩展 OR 特征B2)/CLMS/TXT`
        - **VEN 策略**：限定在 `/TI` 或 `/AB` 字段。
          - *Template*: `(termA+ OR termA2) AND (termB+ OR termB2)/TI/AB`
        
        #### Step 3: 扩展检索 (Expansion Search) - [针对 Y 类文献](创造性)
        - **逻辑**：块检索 (Block Search)。假设某个特征被替换或隐藏，每次只保留 1-2 个核心特征，放宽其他限制，或结合分类号。
        - **CNTXT 策略**：使用全文 `/TXT` 配合 `AND`，或结合 IPC。
          - *Template*: `(核心特征A) AND (IPC分类号)` 或 `(特征A) AND (特征B)`
        - **VEN 策略**：使用 `/KW` 或 `/TXT`，配合截词符。

        #### Step 4: 非专利文献检索 (NPL Search)
        - **目标**：查找技术原理、测试方法、标准法规。
        - **数据库**：CNKI (期刊、学位论文), Google Scholar, 标准数据库 (如 GB/T)。
        - **策略**：不使用具体的结构词（如“丝杆”），改用功能词（如“直线驱动”）OR 效果词（如“精度”）。
        - **语法**：`(功能词 OR 效果词) AND (应用场景 OR 技术领域)`。
        """

        output_format = """
        ### 输出格式 (JSON Only)
        必须返回合法的 JSON 格式，不要包含 Markdown 标记。结构如下：
        {
            "strategies": [
               {
                    "name": "追踪检索",
                    "description": "申请人与发明人追踪",
                    "queries": [
                        { "db": "CNTXT", "query": "/PA=..." },
                        { "db": "VEN", "query": "PA=(...)" }
                    ]
                },
                {
                    "name": "精确检索",
                    "description": "核心特征同句检索 (X类)",
                    "queries": [
                        { "db": "CNTXT", "query": "(TermA OR TermB) S (TermC)/CLMS" },
                        { "db": "VEN", "query": "(TermA OR TermB) S (TermC)/AB/TI" }
                    ]
                },
                {
                    "name": "扩展检索",
                    "description": "特征扩展与块检索 (Y类)",
                    "queries": [
                        { "db": "CNTXT", "query": "..." }
                    ]
                },
                {
                    "name": "NPL 非专利检索",
                    "description": "...",
                    "queries": [
                        { "db": "NPL", "query": "(功能词 OR 效果词) AND (场景词)" }
                    ]
                }
            ]
        }
        """

        system_prompt = f"{role_definition}\n\n{syntax_rules}\n\n{process_steps}\n\n{output_format}"

        return self.llm_service.chat_completion_json(
            model=settings.LLM_MODEL_REASONING,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context}
            ]
        )