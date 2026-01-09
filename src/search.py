import json
from typing import Dict, Any, List
from loguru import logger
from src.llm import get_llm_service
from config import settings

class SearchAgent:
    def __init__(self):
        self.llm_service = get_llm_service()

    def generate_strategy(self, patent_data: Dict, report_data: Dict) -> Dict[str, Any]:
        """
        主入口：执行两阶段检索策略生成
        """
        logger.info("开始构建检索策略...")
        
        # 0. 准备通用上下文
        context_text = self._build_base_context(patent_data, report_data)

        try:
            # Stage 1: 检索要素表 (Search Matrix) - 深度扩展
            search_matrix = self._build_search_matrix(context_text)
            
            # Stage 2: 检索式构建 (Query Formulation)
            strategy_plan = self._build_queries(context_text, search_matrix)

            # 合并结果
            return {
                "search_matrix": search_matrix, # 对应你要求的中文、中文扩展、英文翻译
                "search_plan": strategy_plan    # 具体的检索步骤和分析
            }

        except Exception as e:
            logger.error(f"Search strategy generation failed: {e}")
            return { "search_matrix": [], "search_plan": {} }

    def _build_base_context(self, patent_data: Dict, report_data: Dict) -> str:
        biblio = patent_data.get("bibliographic_data", {})

        essential_features = [
            f"- {f['name']}: {f['description']}" 
            for f in report_data.get("technical_features", []) 
            if f.get("is_essential", True)
        ]
        
        return f"""
        [发明名称] {biblio.get('invention_title')}
        [申请人] {', '.join([a['name'] for a in biblio.get('applicants', [])])}
        [发明人] {', '.join(biblio.get('inventors', []))}
        [IPC分类] {', '.join(biblio.get('ipc_classifications', []))}
        [技术问题] {report_data.get('technical_problem')}
        [技术方案] {report_data.get('technical_scheme')}
        [必要技术特征(必须检索点)]:
        {chr(10).join(essential_features)}
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
        4.  **CPC 关联**：如果可能，请联想到相关的 CPC 分类号关键词。

        ### 输出格式 (JSON List Only)
        仅输出 JSON 列表，无 Markdown 标记：
        [
          {
            "concept_key": "核心概念A (如: 减速器)",
            "zh_expand": ["减速器", "减速机", "齿轮箱", "变速箱", "传动机构"], 
            "en_expand": ["reducer", "gear box", "gearbox", "transmission", "speed reduc+"]
          },
          ...
        ]
        """
        
        response = self.llm_service.chat_completion_json(
            config=settings.LLM_MODEL_REASONING,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context}
            ]
        )
        
        # 确保返回的是列表
        return response if isinstance(response, list) else []

    def _build_queries(self, context: str, matrix: List[Dict]) -> Dict:
        """
        阶段二：构建逻辑检索式（逻辑专家模式）
        """
        logger.info("[SearchAgent] Step 2: Formulating Queries...")

        matrix_str = json.dumps(matrix, ensure_ascii=False, indent=2) 

        role_definition = """
        你是一位经验丰富的专利审查员，拥有 10 年以上在中国国家知识产权局 (CNTXT) 和全球专利数据库 (VEN) 的检索实战经验。
        你的任务是基于提供的【待审专利档案】和【检索要素表】，生成一份**详尽、实战、可直接复制粘贴**的检索策略。
        """   

        syntax_rules = """
        ### 数据库语法规范 (必须严格遵守)
        **通用逻辑算符**：
        - OR: `+` (如: `A + B`) | AND: `*` (如: `A * B`) | NOT: `-`

        **CNTXT (中文) 规范**：
        - **位置算符**：必须使用 `S` (同句) 来提高核心特征组合的精度。格式: `(词A S 词B)`。仅在宽松检索时使用 `*`。
        - **字段标识**：
          - `/TI` (标题), `/AB` (摘要), `/CLMS` (权利要求), `/KW` (关键词), `/TXT` (全文)。
          - 关键词位置: 放在关键词之后，例如 `(螺钉 + 螺栓)/TI/KW`。
        
        **VEN (英文) 规范**：
        - **截词符**：词根后加 `?` 或 `+` (例如 `comput+`, `rotat?`)。
        - **位置算符**：使用 `*` (AND) 为主，必要时使用 `nW` (n个词距)。
        - **字段标识**：`/TI` (Title), `/AB` (Abstract), `/KW` (Keywords), `/CPC` (CPC分类)。

        **CPC/IPC 格式要求**: 
        - 必须精确到**大组或小组** (Subgroup Level)，例如 `G06F 16/30` 或 `H04L 5/00`。
        - 严禁只给出部级或大类 (如 `G` 或 `G06F`)，除非该技术非常宽泛。
        """

        process_steps = """
        ### 检索策略生成流程 (按顺序执行)

        #### Step 1: 语义/智能检索 (Semantic Search First)
        - **目标**：利用智能系统（如专利局内部的智能检索平台、或第三方 AI 检索工具）的语义分析能力，快速圈定最相关的 X 类文献。
        - **核心动作**：输入专利的“发明名称”、“摘要”或“核心特征描述”作为查询文本，并**限定**到最相关的 IPC/CPC 分类号。
        - **输出**: 提取一段最能代表技术方案的文本（200字以内），并给出建议限制的主分类号。

        #### Step 2: 追踪检索 (Inventor/Assignee Trace)
        - **目标**：排查申请人 (PA) 和发明人 (IN) 的自引、同族申请或既往技术。
        - **数据库**：CNTXT, VEN。
        - **语法**：
            - CNTXT: `/PA=XXX` (申请人), `/IN=XXX` (发明人)。
            - VEN: `PA=(XXX)` (Assignee), `IN=(XXX)` (Inventor)。
        - **策略**：将申请人/发明人名称与核心关键词结合。

        #### Step 3: 布尔逻辑检索 (Boolean Logic Search) - 核心
        请分别针对 **X类文献 (新颖性)** 和 **Y类文献 (创造性)** 构建策略：

        **A. 精确检索 (Precision Search) - [针对 X 类文献]**
        - **逻辑**：选取 2-3 个**最核心的必要技术特征**，进行高精度组合。
        - **CNTXT 策略**：强行使用 `S` (同句) 算符。
          - *Template*: `(核心特征A扩展 + 特征A2) S (核心特征B扩展 + 特征B2)/CLMS/TXT`
        - **VEN 策略**：限定在 `/TI` 或 `/AB` 字段。
          - *Template*: `(termA+ + termA2) * (termB+ + termB2)/TI/AB`

        **B. 扩展检索 (Expansion Search) - [针对 Y 类文献]**
        - **逻辑**：块检索 (Block Search)。假设某个特征被替换或隐藏，每次只保留 1-2 个核心特征，放宽其他限制，或结合分类号。
        - **CNTXT 策略**：使用全文 `/TXT` 配合 `*` (AND)，或结合 IPC。
          - *Template*: `(核心特征A) * (IPC分类号)` 或 `(特征A) * (特征B)`
        - **VEN 策略**：使用 `/IW` 或 `/TXT`，配合截词符。

        #### Step 4: 非专利文献检索 (NPL Search)
        - **目标**：查找技术原理、测试方法、标准法规。
        - **数据库**：CNKI (期刊、学位论文), Google Scholar, 标准数据库 (如 GB/T)。
        - **策略**：不使用具体的结构词（如“丝杆”），改用功能词（如“直线驱动”）+ 效果词（如“精度”）。
        - **语法**：`(IPC分类号) * (功能词 + 效果词)`。
        """

        output_format = """
        ### 输出格式 (JSON Only)
        必须返回合法的 JSON 格式，不要包含 Markdown 标记。结构如下：
        {
            "cpc_suggestion": ["G06F 16/33", "G06F 40/20"],  // 顶层字段：用于前端展示完整的分类号建议列表（精确到组）
            "strategies": [
                {
                    "name": "语义检索",
                    "description": "语义文本输入与分类号限定",
                    "queries": [
                        { "db": "Semantic_Input", "query": "在此处生成用于输入的200字技术摘要..." },
                        { "db": "Limit_IPC", "query": "G06F" }  // 策略字段：仅用于限定范围，通常比建议列表宽泛，避免漏检
                    ]
                },
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
                    "description": "核心特征精确去噪 (X类)",
                    "queries": [
                        { "db": "CNTXT", "query": "(...) S (...)/CLMS" },
                        { "db": "VEN", "query": "(...) * (...)/TI/AB" }
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
                    "queries": []
                }
            ]
        }
        """

        system_prompt = f"{role_definition}\n\n{syntax_rules}\n\n{process_steps}\n\n{output_format}"

        user_content = f"""
        【待审专利档案】:
        {context}
        
        【检索要素表】:
        {matrix_str}

        请根据以上信息生成 JSON 格式的检索策略。
        """

        return self.llm_service.chat_completion_json(
            config=settings.LLM_MODEL_REASONING,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ]
        )