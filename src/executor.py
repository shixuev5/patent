import time
from typing import Dict, Any, Set
from loguru import logger
from src.search_clients.factory import SearchClientFactory

class SearchExecutor:
    def __init__(self, strategy_data: Dict):
        """
        初始化执行器
        :param strategy_data: 包含检索策略的字典 (Step 6 的输出)
        """
        self.strategy_data = strategy_data
        # 使用工厂获取智慧芽客户端实例
        self.client = SearchClientFactory.get_client("zhihuiya")

    def execute(self) -> Dict[str, Any]:
        """
        执行主流程：
        1. 遍历策略树
        2. 识别 Patsnap/Zhihuiya 任务
        3. 调用 API
        4. 统计命中数并聚合结果
        """
        # 1. 从 strategy_data 中获取查新截止日期
        critical_date = self.strategy_data.get("critical_date", "")
        
        logger.info("开始执行检索策略 (Execution Mode)...")
        
        # 初始化报告结构
        execution_report = {
            "meta": {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "provider": "Zhihuiya"
            },
            "total_unique_hits": 0,    # 去重后的总专利数
            "strategy_stats": [],      # 每个策略的执行统计
            "results_collection": []   # 扁平化的专利详情列表 (去重)
        }

        # 辅助集合用于全局去重
        seen_ids: Set[str] = set()
        
        # 1. 解析策略路径
        # 兼容两种结构：直接是 list 或包裹在 search_plan 中
        strategies = self.strategy_data.get("search_plan", {}).get("strategies", [])
        if not strategies:
            strategies = self.strategy_data.get("strategies", [])

        if not strategies:
            logger.warning("未找到有效的检索策略列表，跳过执行。")
            return execution_report

        # 2. 遍历执行
        for group_idx, group in enumerate(strategies):
            group_name = group.get("name", f"Group_{group_idx}")
            queries = group.get("queries", [])
            
            logger.info(f"Processing Group: {group_name}")

            for q_item in queries:
                step_name = q_item.get("step", "Unknown Step")
                db_type = q_item.get("db", "").lower()
                query_str = q_item.get("query", "")
                
                if not query_str: continue

                # 判断检索类型
                is_semantic = "smart search" in db_type or "semantic" in db_type
                is_command = "patsnap" in db_type or "zhihuiya" in db_type

                if not (is_semantic or is_command):
                    continue

                logger.info(f"Running [{group_name}-{step_name}] ({'Semantic' if is_semantic else 'Command'})")

                try:
                    hits = []
                    if is_semantic:
                        hits = self.client.search_semantic(
                            text=query_str, 
                            to_date=critical_date, 
                            limit=100
                        )
                    else:
                        hits = self.client.search(query=query_str, limit=100)
                    
                    # 统计当前查询的结果
                    hit_count = len(hits)
                    logger.info(f"    Hits: {hit_count}")

                    # 记录策略统计
                    execution_report["strategy_stats"].append({
                        "group": group_name,
                        "step": step_name,
                        "type": "semantic" if is_semantic else "command",
                        "hit_count": hit_count,
                        "top_ids": [h["id"] for h in hits[:5]]
                    })

                    for doc in hits:
                        uid = doc.get("id") or doc.get("pn")
                        if uid and uid not in seen_ids:
                            seen_ids.add(uid)
                            doc["_source_strategy"] = f"{group_name}::{step_name}"
                            execution_report["results_collection"].append(doc)
                    
                    time.sleep(1) # 避免 QPS 过高

                except Exception as e:
                    logger.error(f"    Failed to execute query: {e}")
                    execution_report["strategy_stats"].append({
                        "group": group_name,
                        "step": step_name,
                        "query": query_str,
                        "type": "semantic" if is_semantic else "command",
                        "error": str(e),
                        "hit_count": 0
                    })

        # 3. 汇总统计
        execution_report["total_unique_hits"] = len(execution_report["results_collection"])
        logger.success(f"检索执行完成。策略数: {len(execution_report['strategy_stats'])}, 去重后命中: {execution_report['total_unique_hits']}")

        return execution_report