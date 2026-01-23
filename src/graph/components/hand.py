# src/graph/components/hand.py

import asyncio
import re
import json
from collections import Counter
from typing import List, Dict, Optional
from loguru import logger

from src.search_clients.factory import SearchClientFactory
from src.utils.llm import get_llm_service
from config import settings

from src.utils.reranker import Reranker


class ExecutionHand:
    """
    生产级执行组件 (ExecutionHand)
    
    核心职责：
    1. 异步执行检索策略 (Async Execution)
    2. 动态查询降级 (Query Relaxation)
    3. 复杂日期逻辑注入 (Date Logic Injection for E/P docs)
    4. 结果扩展 (Spider Search: Family & Citations)
    5. 智能反馈 (IPC Analysis & Keyword Harvesting)
    """

    def __init__(self):
        # 获取检索客户端 (假设底层 Client 是同步的，我们将通过 asyncio.to_thread 包装)
        self.client = SearchClientFactory.get_client("zhihuiya")
        self.llm = get_llm_service()
        self.reranker = Reranker()

    async def execute_batch(
        self, 
        strategies: List[Dict], 
        critical_date: str = "",
        rerank_anchor: str = "" # 新增：用于 Rerank 的锚点文本
    ) -> List[Dict]:
        """
        [Async] 批量并发执行检索策略。
        
        Args:
            strategies: 策略列表
            critical_date: 查新截止日期 (YYYYMMDD)
            
        Returns:
            List[Dict]: 命中的文档列表 (未去重)
        """
        if not strategies:
            return []

        logger.info(f"[Hand] 🚀 Async executing batch of {len(strategies)} queries...")
        
        # 创建并发任务
        tasks = []
        for strat in strategies:
            tasks.append(
                self._execute_single_strategy_async(strat, critical_date)
            )
        
        # 等待所有任务完成
        results_list_of_lists = await asyncio.gather(*tasks)
        
        # 扁平化结果
        all_docs = []
        # 使用 set 简单去重 (基于 uid)，防止同一批次内重复
        seen_ids = set()
        
        for res_list in results_list_of_lists:
            for doc in res_list:
                uid = doc.get("uid") or doc.get("id") or doc.get("pn")
                if uid and uid not in seen_ids:
                    all_docs.append(doc)
                    seen_ids.add(uid)
                    
        # 4. [P0-2] 语义重排序 (Rerank)
        # 在返回前，对结果进行一次重排，确保下游 Reviewer 拿到的是最相关的
        if rerank_anchor and all_docs:
            logger.info(f"[Hand] ⚖️ Reranking {len(all_docs)} docs...")
            all_docs = self.reranker.rank_docs(rerank_anchor, all_docs)
                    
        logger.info(f"[Hand] Batch execution finished. Total unique docs found: {len(all_docs)}")
        return all_docs

    async def _execute_single_strategy_async(
        self, 
        strat: Dict, 
        date: str
    ) -> List[Dict]:
        """
        执行单个策略的完整生命周期：
        Date Injection -> Search -> (Zero Hit) -> Relaxation -> Retry -> Noise Filter
        """
        q_str = strat.get("query")
        db_type = strat.get("db")
        intent = strat.get("intent", "Broad")

        if not q_str:
            return []

        # 1. 注入日期限制 (Core Logic)
        final_query = self._inject_date(q_str, db_type, date, intent)
        found_docs = []

        try:
            # --- Attempt 1: Strict Execution ---
            # 使用 to_thread 将同步 IO 转换为异步
            logger.debug(f"Running [{intent}]: {final_query[:60]}...")
            
            response = await asyncio.to_thread(
                self.client.search, query=final_query, limit=100
            )
            
            total = response.get("total", 0)
            hits = response.get("results", [])

            # --- Attempt 2: Relaxation (Step 5 Feature) ---
            # 如果严格检索为 0，且不是 E 类检索 (E类通常就要严，不能随便放宽)，尝试降级
            if total == 0 and intent != "Conflicting_E":
                relaxed_query = self._relax_query(final_query)
                
                # 只有当 Query 真的发生了变化才重试
                if relaxed_query and relaxed_query != final_query:
                    logger.info(f"  ↳ Zero hits. Retrying with relaxed: {relaxed_query[:50]}...")
                    strat["status"] = "executed_relaxed"
                    strat["relaxed_query"] = relaxed_query
                    
                    # 降级重试 (限制返回数量，防止噪音)
                    response = await asyncio.to_thread(
                        self.client.search, query=relaxed_query, limit=50
                    )
                    hits = response.get("results", [])
                    total = response.get("total", 0)

            # --- Noise Circuit Breaker ---
            # 如果结果过多，通常意味着 Query 太宽泛，参考价值低
            max_hits = 2000 if intent == "Broad" else 1000
            if total > max_hits:
                logger.warning(f"Query returned {total} hits (>{max_hits}). Treating as Noise.")
                strat["status"] = "skipped_noise"
                return []

            if total == 0:
                strat["status"] = "executed_empty"
                return []

            if strat.get("status") != "executed_relaxed":
                strat["status"] = "executed_success"

            # --- Result Normalization ---
            for doc in hits:
                # 补充元数据
                doc["uid"] = doc.get("id") or doc.get("pn")
                doc["source_strategy"] = strat.get("name")
                doc["source_intent"] = intent
                
                # 标记日期逻辑类型，辅助 Reranker 加权
                if intent == "Conflicting_E":
                    doc["check_date_logic"] = "Conflicting"
                else:
                    doc["check_date_logic"] = "PriorArt"
                
                found_docs.append(doc)

        except Exception as e:
            logger.error(f"Query execution failed: {e}")
            strat["status"] = "error"

        return found_docs

    def _inject_date(self, query: str, db: str, date: str, intent: str) -> str:
        """
        [Step 3 Feature] 给 Query 加上时间限制。
        支持 E 类 (抵触申请) 的特殊逻辑。
        """
        if not date:
            return query

        # 适配 Patsnap / Zhihuiya 语法
        if "Patsnap" in db or "Zhihuiya" in db:
            if intent == "Conflicting_E":
                # E类定义: 申请日 <= 查新日 AND 公开日 > 查新日
                # 注意: 具体边界(包含/不包含)需根据数据库文档微调，这里采用通用逻辑
                return f"({query}) AND APD:[* TO {date}] AND PBD:[{date} TO *]"
            else:
                # 常规/P类定义: 公开日 <= 查新日
                return f"({query}) AND PBD:[* TO {date}]"

        return query

    def _relax_query(self, query: str) -> Optional[str]:
        """
        [Step 5 Feature] 基于正则的查询降级逻辑。
        Hierachy: s -> p -> AND
        """
        original = query
        relaxed = query

        # 1. 's' (Same Sentence) -> 'p' (Same Paragraph)
        # 匹配: ) s ( 或 )s( (不区分大小写)
        if re.search(r"\)\s*s\s*\(", relaxed, re.IGNORECASE):
            relaxed = re.sub(r"\)\s*s\s*\(", ") p (", relaxed, flags=re.IGNORECASE)
        
        # 2. 'w/n' (Within Words) -> 'p'
        elif re.search(r"w/\d+", relaxed, re.IGNORECASE):
             relaxed = re.sub(r"w/\d+", "p", relaxed, flags=re.IGNORECASE)

        # 3. 'p' (Same Paragraph) -> 'AND'
        # 只有当前已经是 p，且上一步没变过，才降级到 AND
        elif re.search(r"\)\s*p\s*\(", relaxed, re.IGNORECASE):
            relaxed = re.sub(r"\)\s*p\s*\(", ") AND (", relaxed, flags=re.IGNORECASE)
        
        if relaxed == original:
            return None
            
        return relaxed

    async def expand_high_value_docs(self, docs: List[Dict]) -> List[Dict]:
        """
        [Step 4 Feature] Spider Search (同族与引证扩展)
        Async implementation.
        """
        # 只对 Top 3 进行扩展，防止爆炸
        seed_docs = docs[:3] 
        if not seed_docs:
            return []

        logger.info(f"[Hand] 🕷️ Running Spider Search on {len(seed_docs)} seed docs...")
        
        async def fetch_lineage(seed_doc):
            uid = seed_doc.get("uid")
            if not uid: return []
            
            lineage_docs = []
            try:
                # 并发请求同族和引证
                # 假设 client 提供了 get_family 和 get_citations 方法
                fams, cits = await asyncio.gather(
                    asyncio.to_thread(self.client.get_family, uid),
                    asyncio.to_thread(self.client.get_citations, uid),
                    return_exceptions=True
                )
                
                if isinstance(fams, list):
                    for d in fams:
                        d["source_strategy"] = "Spider_Family"
                        d["source_intent"] = "Lineage"
                        lineage_docs.append(d)
                
                if isinstance(cits, list):
                    for d in cits:
                        d["source_strategy"] = "Spider_Citation"
                        d["source_intent"] = "Lineage"
                        lineage_docs.append(d)
                        
            except Exception as e:
                logger.warning(f"Spider failed for {uid}: {e}")
            
            return lineage_docs

        # 并发执行所有种子的扩展
        results = await asyncio.gather(*[fetch_lineage(d) for d in seed_docs])
        
        expanded_docs = []
        for res in results:
            expanded_docs.extend(res)

        logger.info(f"[Hand] 🕷️ Spider found {len(expanded_docs)} related docs.")
        return expanded_docs

    def analyze_ipcs(self, docs: List[Dict], top_n: int = 8) -> List[str]:
        """
        [Step 6 Feature] IPC Calibration
        统计命中结果的高频分类号 (大组级)。
        """
        if not docs:
            return []
            
        ipc_counter = Counter()
        
        for doc in docs:
            # 兼容不同的字段名
            ipcs = doc.get("ipc_classifications", []) or doc.get("ipcs", [])
            if isinstance(ipcs, str):
                ipcs = [ipcs]
                
            for ipc in ipcs:
                # 提取大组 (e.g., "H04W 72/04" -> "H04W 72")
                # 正则匹配: 4位子类 + 空格 + 数字
                match = re.match(r"^([A-H]\d{2}[A-Z]\s?\d+)", ipc.strip())
                if match:
                    group = match.group(1).replace(" ", "") # H04W72
                    ipc_counter[group] += 1
        
        if not ipc_counter:
            return []
            
        # 简单的阈值过滤：至少出现 2 次
        valid_ipcs = [
            ipc for ipc, count in ipc_counter.most_common(top_n) 
            if count >= 2
        ]
        
        if valid_ipcs:
            logger.info(f"[Hand] 📊 Validated IPCs: {valid_ipcs}")
            
        return valid_ipcs

    def harvest_new_keywords(
        self, 
        docs: List[Dict], 
        current_matrix: List[Dict]
    ) -> List[Dict]:
        """
        [Step 8 Feature] Keyword Harvesting
        使用 Fast Model 从高相关文档中学习新词汇。
        """
        if not docs:
            return current_matrix

        # 1. 准备语料 (取 Top 3)
        candidates = docs[:3]
        corpus_parts = []
        for d in candidates:
            corpus_parts.append(f"Title: {d.get('title')}\nAbs: {d.get('abstract')}")
        corpus_str = "\n---\n".join(corpus_parts)

        # 2. 准备现有概念的简化版 (减少 Token)
        matrix_lite = []
        for item in current_matrix:
            matrix_lite.append({
                "id": item["concept_key"],
                "zh": item.get("zh_expand", [])[:5], # 只给前5个作为提示
                "en": item.get("en_expand", [])[:5]
            })

        # 3. 调用 Fast Model
        prompt = f"""
        Extract NEW synonyms for the concepts below from the patent text provided.
        
        Concepts (ID: Existing Keywords):
        {json.dumps(matrix_lite, ensure_ascii=False)}

        Patent Text Corpus:
        {corpus_str[:3000]} # Truncate

        Instructions:
        1. Find synonyms/related terms present in the text but NOT in the existing list.
        2. Output JSON list.
        
        Output Format:
        [
            {{"id": "concept_id", "new_zh": ["词1"], "new_en": ["term1"]}}
        ]
        """

        try:
            resp = self.llm.chat_completion_json(
                model=settings.LLM_MODEL_FAST, # 使用廉价模型
                messages=[{"role": "user", "content": prompt}]
            )
            
            if not isinstance(resp, list):
                return current_matrix
            
            # 4. 合并更新
            # 深拷贝 current_matrix 以避免副作用
            updated_matrix = [dict(item) for item in current_matrix]
            matrix_map = {item["concept_key"]: item for item in updated_matrix}
            
            has_update = False
            for update in resp:
                cid = update.get("id")
                if cid in matrix_map:
                    # Update ZH
                    new_zh = set(update.get("new_zh", [])) - set(matrix_map[cid].get("zh_expand", []))
                    if new_zh:
                        matrix_map[cid]["zh_expand"].extend(list(new_zh))
                        has_update = True
                    
                    # Update EN
                    new_en = set(update.get("new_en", [])) - set(matrix_map[cid].get("en_expand", []))
                    if new_en:
                        matrix_map[cid]["en_expand"].extend(list(new_en))
                        has_update = True
            
            if has_update:
                logger.info("[Hand] 🌾 Harvested new keywords into Matrix.")
                return updated_matrix
                
        except Exception as e:
            logger.warning(f"Keyword harvesting failed: {e}")

        return current_matrix