import operator
from typing import Annotated, List, Dict, TypedDict
from loguru import logger
from langgraph.graph import StateGraph, END
from src.search_clients.factory import SearchClientFactory # 新增

# 假设复用之前的配置和LLM服务
from config import settings
from src.utils.llm import get_llm_service

# --- 定义图的状态 (State) ---
class AgentState(TypedDict):
    """
    LangGraph 的状态流转对象
    """
    # 输入数据
    report_data: Dict  # 原始技术交底书分析报告
    initial_plan: Dict # 上一步生成的 Search Plan
    
    # 中间状态
    candidate_pool: Annotated[List[Dict], operator.add] # 候选专利池 (增量追加)
    feedback_stats: Dict # 统计信息 (高频CPC, 新关键词)
    refined_queries: List[Dict] # 修正后的检索式
    
    # 输出数据
    final_top_30: List[Dict] # 最终排序结果


# --- 高级检索代理核心类 ---
class AdvancedSearchAgent:
    def __init__(self):
        self.llm = get_llm_service()
        self.search_client = SearchClientFactory.get_client("zhihuiya") 

    def build_graph(self):
        """
        构建 LangGraph 工作流 DAG (有向无环图 + 循环)
        Nodes:
          1. execute_initial_search: 执行 SearchStrategyGenerator 生成的策略
          2. analyze_feedback: 分析 Top 结果的 CPC 分布和文本特征
          3. generate_refined_strategy: 生成基于反馈的修正策略 (Dynamic CPC / Negative)
          4. execute_refined_search: 执行修正策略
          5. citation_expansion: 对高分结果进行引文裂变
          6. semantic_rerank: 最终 LLM 重排序
        """
        workflow = StateGraph(AgentState)

        # 添加节点
        workflow.add_node("execute_initial_search", self._node_execute_initial)
        workflow.add_node("analyze_feedback", self._node_analyze_feedback)
        workflow.add_node("generate_refined_strategy", self._node_refine_strategy)
        workflow.add_node("execute_refined_search", self._node_execute_refined)
        workflow.add_node("citation_expansion", self._node_citation_expansion)
        workflow.add_node("semantic_rerank", self._node_final_rerank)

        # 定义边 (Edges)
        workflow.set_entry_point("execute_initial_search")
        
        # 初始检索 -> 分析反馈
        workflow.add_edge("execute_initial_search", "analyze_feedback")
        
        # 分析反馈 -> 策略修正
        workflow.add_edge("analyze_feedback", "generate_refined_strategy")
        
        # 策略修正 -> 执行修正检索
        workflow.add_edge("generate_refined_strategy", "execute_refined_search")
        
        # 执行修正检索 -> 引文裂变 (同时也可以把初始检索结果送入引文，这里简化流程)
        workflow.add_edge("execute_refined_search", "citation_expansion")
        
        # 引文裂变 -> 最终重排
        workflow.add_edge("citation_expansion", "semantic_rerank")
        
        # 结束
        workflow.add_edge("semantic_rerank", END)

        return workflow.compile()

    # --- Node Implementations ---

    def _node_execute_initial(self, state: AgentState):
        """
        节点 1: 执行初始生成的静态策略
        """
        logger.info("[Node] Executing Initial Search Strategies...")
        strategies = state['initial_plan'].get('strategies', [])
        
        raw_results = []
        for strategy in strategies:
            for query_obj in strategy.get('queries', []):
                # 仅执行专利库检索 (CNTXT/VEN)，跳过 NPL 以聚焦专利
                if query_obj['db'] in ['CNTXT', 'VEN']:
                    hits = self.search_client.search(
                        query=query_obj['query'],
                        limit=20 # 初始探测不需要太多
                    )
                    # 标记来源
                    for h in hits: h['source'] = 'initial'
                    raw_results.extend(hits)
        
        return {"candidate_pool": raw_results}

    def _node_analyze_feedback(self, state: AgentState):
        """
        节点 2: 反馈分析 (The "20%" Intelligence)
        统计 Top 结果中的 CPC 分布，发现漏检关键词。
        """
        logger.info("[Node] Analyzing Search Feedback...")
        candidates = state['candidate_pool']
        if not candidates:
            return {"feedback_stats": {}}

        # 1. 简单的 Python 统计 (Top CPC)
        cpc_counter = {}
        titles_text = []
        
        for doc in candidates:
            # 统计 CPC
            for cpc in doc.get('cpc', []):
                subgroup = cpc.split(' ')[0] # 粗略取前段，实际应更细
                cpc_counter[subgroup] = cpc_counter.get(subgroup, 0) + 1
            # 收集文本供 LLM 分析
            titles_text.append(f"- {doc.get('title')}")

        top_cpcs = sorted(cpc_counter.items(), key=lambda x: x[1], reverse=True)[:3]
        
        # 2. LLM 分析：寻找新关键词 (Lexicon Expansion)
        tech_means = state['report_data'].get('technical_means', '')
        prompt = f"""
        基于当前技术方案：{tech_means[:500]}
        
        我们进行了一轮初查，命中了以下专利标题：
        {chr(10).join(titles_text[:20])}
        
        请分析：
        1. 现有结果中是否使用了我们未覆盖的“隐晦术语”或“行业黑话”？
        2. 识别出 3 个高价值的同义词，用于扩展检索。
        
        输出格式 JSON: {{ "new_keywords": ["词1", "词2", "词3"] }}
        """
        
        try:
            resp = self.llm.chat_completion_json(
                model=settings.LLM_MODEL_REASONING,
                messages=[{"role": "user", "content": prompt}]
            )
            new_keywords = resp.get("new_keywords", [])
        except:
            new_keywords = []

        return {"feedback_stats": {"top_cpcs": [x[0] for x in top_cpcs], "new_keywords": new_keywords}}

    def _node_refine_strategy(self, state: AgentState):
        """
        节点 3: 策略修正 (Refinement)
        利用统计出的精准 CPC 和新关键词，生成“狙击”检索式。
        """
        logger.info("[Node] Refining Strategy based on Feedback...")
        stats = state['feedback_stats']
        report = state['report_data']
        
        if not stats:
            return {"refined_queries": []}

        refined_queries = []
        
        # 策略 A: 动态分类号钻取 (Dynamic Drill-down)
        # 逻辑：使用从结果中挖掘出的高频 CPC，配合最核心的特征词
        # 这比预设的 IPC 要准得多
        core_feature = report.get('technical_features', [{}])[0].get('name', '')
        if stats.get('top_cpcs'):
            cpc_str = " OR ".join(stats['top_cpcs'])
            query = f"({core_feature}) AND ({cpc_str})/CPC"
            refined_queries.append({
                "name": "动态CPC钻取",
                "db": "VEN",
                "query": query
            })

        # 策略 B: 词汇指纹扩展 (Lexicon Expansion)
        # 逻辑：使用挖掘出的“黑话”进行检索
        if stats.get('new_keywords'):
            kw_str = " OR ".join(stats['new_keywords'])
            query = f"({kw_str})/TI/AB"
            refined_queries.append({
                "name": "反馈词汇扩展",
                "db": "CNTXT",
                "query": query
            })

        return {"refined_queries": refined_queries}

    def _node_execute_refined(self, state: AgentState):
        """
        节点 4: 执行修正后的检索
        """
        logger.info("[Node] Executing Refined Search...")
        new_queries = state['refined_queries']
        refined_hits = []
        
        for q in new_queries:
            hits = self.search_client.search(q['query'], limit=20)
            for h in hits: h['source'] = 'refined'
            refined_hits.extend(hits)
            
        return {"candidate_pool": refined_hits} # LangGraph 会自动追加 list

    def _node_citation_expansion(self, state: AgentState):
        """
        节点 5: 引文裂变 (Citation Cascading)
        从 Candidate Pool 中选出相关性最高的 5 篇，爬取其引文。
        这是找到 Y 类文献（组合对比）的绝佳路径。
        """
        logger.info("[Node] Performing Citation Expansion...")
        pool = state['candidate_pool']
        if not pool:
            return {"candidate_pool": []}
        
        # 简单策略：假设 pool 里的前 5 个是相关的 (实际应先 Rerank 再 Citation，这里简化)
        seeds = pool[:5] 
        seed_ids = [doc['id'] for doc in seeds if 'id' in doc]
        
        citation_hits = self.search_client.get_citations(seed_ids)
        for h in citation_hits: h['source'] = 'citation'
        
        return {"candidate_pool": citation_hits}

    def _node_final_rerank(self, state: AgentState):
        """
        节点 6: 终极重排 (Semantic Rerank)
        使用 LLM 对所有候选专利进行评分，输出 Top 30。
        """
        logger.info("[Node] Final Semantic Reranking...")
        pool = state['candidate_pool']
        report = state['report_data']
        
        # 1. 去重 (Deduplication)
        unique_pool = {doc['id']: doc for doc in pool if 'id' in doc}.values()
        
        # 2. 构造打分 Prompt
        # 实际场景：应使用 Cross-Encoder 模型 (如 BGE-Reranker) 进行向量打分，速度快且准。
        # 这里用 LLM 模拟 Reranker 逻辑。
        
        scored_results = []
        
        # 为了演示，只取前 20 个进行 LLM 打分，防止 Token 爆炸
        for doc in list(unique_pool)[:20]:
            prompt = f"""
            技术方案摘要：{report.get('ai_abstract', '')[:300]}
            
            现有技术专利：
            标题：{doc.get('title')}
            摘要：{doc.get('abstract', '')[:300]}
            
            请打分 (0-10)：该专利与技术方案的相关性。
            仅输出数字。
            """
            try:
                # 模拟 LLM 打分
                # score = float(self.llm.predict(prompt))
                score = 5.0 # Mock
            except:
                score = 0
            
            doc['relevance_score'] = score
            scored_results.append(doc)
            
        # 3. 排序与截断
        scored_results.sort(key=lambda x: x.get('relevance_score', 0), reverse=True)
        top_30 = scored_results[:30]
        
        logger.info(f"Rerank complete. Top 1 score: {top_30[0]['relevance_score'] if top_30 else 0}")
        
        return {"final_top_30": top_30}

# --- Entry Point Example ---
if __name__ == "__main__":
    # 模拟上一步的输出
    mock_input = {
        "report_data": {"technical_means": "...", "technical_features": [...]},
        "initial_plan": {"strategies": []}
    }
    
    agent = AdvancedSearchAgent()
    workflow = agent.build_graph()
    
    # 运行图
    result = workflow.invoke(mock_input)
    
    print("Top 30 Patents:")
    for pat in result['final_top_30']:
        print(f"- [{pat['relevance_score']}] {pat['title']} ({pat['source']})")