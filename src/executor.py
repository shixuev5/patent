import time
from pathlib import Path
from typing import Dict, Any, Optional
from loguru import logger
from src.search_clients.factory import SearchClientFactory
from src.utils.ranker import HybridRanker
from src.utils.cache import StepCache


class SearchExecutor:
    def __init__(self, strategy_data: Dict, cache_file: Optional[Path]):
        """
        初始化执行器
        :param strategy_data: 包含检索策略的字典 (Step 6 的输出)
        :param cache_file: 缓存文件路径 (用于存储中间检索结果，防止重复扣费)
        """
        self.strategy_data = strategy_data
        self.client = SearchClientFactory.get_client("zhihuiya")
        self.ranker = HybridRanker()

        # 初始化缓存管理器
        self.cache = StepCache(cache_file) if cache_file else None

    def execute(self) -> Dict[str, Any]:
        """
        执行主流程：
        1. 遍历策略树
        2. 调用 API (获取 total 和 results)
        3. 噪声过滤 (>1000 丢弃)
        4. 截取 Top 100 并按策略分组存储
        5. 全局去重并记录命中策略
        6. 调用 Ranker 进行混合评分排序
        """
        # 1. 从 strategy_data 中获取查新截止日期
        critical_date = self.strategy_data.get("critical_date", "")

        logger.info("开始执行检索策略 (Execution Mode)...")

        # 初始化报告结构
        execution_report = {
            "meta": {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "provider": "Zhihuiya",
            },
            "total_unique_hits": 0,  # 全局去重后的总专利数
            "strategy_details": [],  # 详细的分组结果（包含每个策略的具体专利列表）
            "results_collection": [],  # 全局扁平化列表 (去重后，用于渲染总表)
        }

        # 辅助字典用于全局去重与策略聚合
        # Key: Patent ID/PN, Value: Patent Dict
        unique_results_map: Dict[str, Dict] = {}

        # 2. 解析策略路径
        strategies = self.strategy_data.get("search_plan", {}).get("strategies", [])
        if not strategies:
            # 兼容旧结构
            strategies = self.strategy_data.get("strategies", [])

        if not strategies:
            logger.warning("未找到有效的检索策略列表，跳过执行。")
            return execution_report

        # 3. 遍历执行
        for group_idx, group in enumerate(strategies):
            group_name = group.get("name", f"Group_{group_idx}")
            queries = group.get("queries", [])

            logger.info(f"Processing Group: {group_name}")

            for q_item in queries:
                step_name = q_item.get("step", "Unknown Step")
                db_type = q_item.get("db", "")
                query_str = q_item.get("query", "")

                if not query_str:
                    continue
                
                if not (db_type in ("Smart Search", "Patsnap")):
                    continue
                
                is_semantic = db_type == 'Smart Search'

                logger.info(f"Running [{group_name}-{step_name}]...")

                # 初始化单步策略记录
                strategy_record = {
                    "group": group_name,
                    "step": step_name,
                    "query": query_str,
                    "db": db_type,
                    "status": "pending",
                    "raw_hit_count": 0,
                    "stored_hit_count": 0,
                    "results": [],  # 分策略存储结果
                }

                # 构建缓存 Key (确保唯一性)
                cache_key = f"search_exec_{group_name}::{step_name}"

                try:
                    # 调用 API
                    response = {}
                    hit_cache = False

                    # === 1. 尝试读取缓存 ===
                    if self.cache:
                        cached_val = self.cache.get(cache_key)
                        if cached_val:
                            response = cached_val
                            hit_cache = True
                            logger.info(f"    [Cache Hit] Loaded data for {step_name}")

                    if not hit_cache:
                        if is_semantic:
                            response = self.client.search_semantic(
                                text=query_str, to_date=critical_date, limit=100
                            )
                        else:
                            response = self.client.search(query=query_str, limit=100)

                        # 写入缓存 (仅当有响应时)
                        if self.cache and response:
                            self.cache.save(cache_key, response)

                    # 解析返回结果
                    total_found = response.get("total", 0)
                    hits = response.get("results", [])

                    strategy_record["raw_hit_count"] = total_found

                    # === 关键逻辑: 噪声过滤 ===
                    if total_found > 1000:
                        # 超过 1000 条 -> 判定为噪声 -> 丢弃结果
                        logger.warning(
                            f"    [Noise Filter] Query returned {total_found} hits (>1000). Discarding results."
                        )
                        strategy_record["status"] = "discarded_noise"
                        strategy_record["results"] = []
                        strategy_record["stored_hit_count"] = 0

                    else:
                        # 有效结果 -> 保留 Top 100
                        # 注意：hits 已经是 API 截断后的 top 100
                        logger.info(
                            f"    [Valid] Total Hits: {total_found}. Keeping Top {len(hits)}."
                        )

                        strategy_record["status"] = "success"
                        strategy_record["stored_hit_count"] = len(hits)
                        strategy_record["results"] = hits

                        # === 关键逻辑: 全局去重与策略聚合 ===
                        for doc in hits:
                            uid = doc.get("id") or doc.get("pn")
                            if not uid:
                                continue

                            current_strategy_tag = f"{group_name}::{step_name}"

                            if uid in unique_results_map:
                                # A. 已存在：追加策略标签，不覆盖文档
                                existing_doc = unique_results_map[uid]

                                # 确保 matched_strategies 存在
                                if "matched_strategies" not in existing_doc:
                                    existing_doc["matched_strategies"] = [
                                        existing_doc.get("_source_strategy", "Unknown")
                                    ]

                                if (
                                    current_strategy_tag
                                    not in existing_doc["matched_strategies"]
                                ):
                                    existing_doc["matched_strategies"].append(
                                        current_strategy_tag
                                    )

                                # 特殊处理：如果是语义检索，且当前分数更高，则更新 score 字段
                                if is_semantic and doc.get("score"):
                                    # 简单比较：这里 doc['score'] 可能是 "88%" 或 0.88，不做复杂转换，直接覆盖
                                    # 更严谨的做法是在 Ranker 里处理，这里先假设语义结果较少，覆盖通常是安全的
                                    existing_doc["score"] = doc.get("score")

                            else:
                                # B. 新文档：初始化
                                doc["matched_strategies"] = [current_strategy_tag]
                                doc["_source_strategy"] = (
                                    current_strategy_tag  # 保留主来源用于 UI 显示
                                )
                                unique_results_map[uid] = doc

                    # 将当前策略的执行记录放入报告
                    execution_report["strategy_details"].append(strategy_record)

                    # 避免 QPS 过高
                    time.sleep(1)

                except Exception as e:
                    logger.error(f"    Failed to execute query: {e}")
                    strategy_record["status"] = "error"
                    strategy_record["error_msg"] = str(e)
                    execution_report["strategy_details"].append(strategy_record)

        # 4. 后处理：排序与统计
        flat_results = list(unique_results_map.values())

        # 调用 Ranker 进行统一评分排序
        sorted_results = self.ranker.rank(flat_results)

        execution_report["results_collection"] = sorted_results
        execution_report["total_unique_hits"] = len(sorted_results)

        # 简单统计
        valid_strategies = sum(
            1 for s in execution_report["strategy_details"] if s["status"] == "success"
        )
        noise_strategies = sum(
            1
            for s in execution_report["strategy_details"]
            if s["status"] == "discarded_noise"
        )

        logger.success(
            f"检索执行完成。有效策略: {valid_strategies}, 噪声丢弃: {noise_strategies}, 全局去重命中: {execution_report['total_unique_hits']}"
        )

        return execution_report
