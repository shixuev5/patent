# src/graph/state.py

from typing import List, Dict, Set, Any, Optional
from typing_extensions import TypedDict, NotRequired

class SearchStrategy(TypedDict):
    """
    单个检索策略的结构定义。
    包含意图、数据库源、查询语句及执行状态。
    """
    name: str
    intent: str         # Precision, Broad, Conflicting_E, Inventive_Step_Y, etc.
    db: str             # Patsnap, Zhihuiya, Google Scholar...
    query: str
    status: str         # pending, executed, executed_relaxed, skipped_noise, error
    
    # [Step 5] 动态降级后的查询语句
    relaxed_query: NotRequired[Optional[str]]


class FoundDocument(TypedDict, total=False):
    """
    单篇命中文档的详细信息。
    使用 total=False 允许字段懒加载，节省内存。
    """
    # 基础元数据
    uid: str            # 唯一标识符 (id 或 pn)
    pn: str             # 公开号
    title: str
    abstract: str
    link: str
    
    # 检索元数据
    score: float          # 搜索引擎原始相关性评分
    rerank_score: float   # [Step 11] 重排序后的综合得分
    is_x_class: bool      # 是否被判定为 X 类
    
    source_strategy: str  # 来源策略名
    source_intent: str    # 来源意图 (Lineage, Precision...)
    
    # [Step 3] 日期逻辑检查标记 (PriorArt vs Conflicting)
    check_date_logic: str 
    
    # [Step 10] 深度分析数据 (Lazy Load by Eye)
    # 包含 claim_chart (list), match_score (int)
    analysis: Optional[Dict]  
    
    # [Step 10] 全文核心段落缓存 (避免重复从 Client 获取)
    full_text_snippets: Optional[str] 


class AgentState(TypedDict):
    """
    Search Agent 的全局状态总线。
    所有 Node 都会接收这个 State，修改后返回增量更新。
    """

    # --- 1. 上下文输入 (Input Context) ---
    patent_data: Dict[str, Any]   # 原始专利结构化数据
    report_data: Dict[str, Any]   # 初始分析报告 (Features, Effects...)
    critical_date: str            # 查新截止日期 (YYYYMMDD)

    # --- 2. 知识库 (Knowledge Base) ---
    # [Step 1] 检索要素矩阵 (可能会被 Step 8 Keyword Harvesting 更新)
    search_matrix: List[Dict]  
    
    # [Step 6] 动态校准的分类号 (IPC/CPC)
    validated_ipcs: List[str]   
    
    # [Step 2] 动态发现的区别特征 (用于 Tier 2 定向检索)
    diff_features: List[str]    

    # --- 3. 状态机控制 (Flow Control) ---
    current_phase: str          # INIT -> TIER1_X -> TIER2_Y -> DONE
    iteration_count: int        # 当前循环次数
    max_iterations: int         # 最大允许循环次数

    # --- 4. 执行流 (Execution Flow) ---
    # 下一步计划要执行的策略列表
    planned_strategies: List[SearchStrategy]
    
    # 已经执行过的 Query 字符串 (用于全局去重)
    executed_queries: List[str]
    
    # 记录已执行的意图，防止策略循环
    executed_intents: List[str]
    
    # 检索到的所有文档 (全量去重后 + Rerank排序后)
    found_docs: List[FoundDocument]
    
    # [P1-4] 增量阅卷记录：存储已经过 QuickEye 审阅的文档 UID
    reviewed_uids: Set[str]

    # --- 5. 结果产出 (Deliverables) ---
    # [Step 1] 最佳证据 (X类/D1)
    best_evidence: Optional[FoundDocument]    
    
    # [Step 7] 最佳创造性组合 (D1 + D2)
    # 结构: {"d1": doc, "d2": doc, "feature": str, "reason": str}
    best_combination: Optional[Dict]          
    
    # [Step 9] 最终生成的查新报告数据
    final_report: Optional[Dict]