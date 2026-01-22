# src/utils/ranker.py

from typing import List, Dict, Any
from loguru import logger

class HybridRanker:
    """
    混合评分排序器：统一语义检索与布尔检索的评分标准。
    用于解决“语义检索有分，布尔检索无分”导致的排序困难问题。
    """

    # === 1. 策略基础分权重配置 ===
    # Key: 策略名称中的关键词, Value: 赋予的基础置信度 (0-100)
    STRATEGY_WEIGHTS = {
        "precision": 95.0,   # 精确检索：逻辑最严密，命中即高价值
        "precise": 95.0,
        "synergy": 90.0,     # 协同/复现：多特征组合
        "trace": 75.0,       # 追踪检索：竞争对手/发明人，商业价值高但可能有噪音
        "competitor": 75.0,
        "functional": 65.0,  # 功能泛化：寻找Y类文献
        "component": 65.0,   # 组件跨界
        "broad": 50.0,       # 宽泛/扩展：噪音较多，作为补充
        "extension": 50.0,
        "semantic": 0.0,     # 语义检索：不使用固定分，直接读取 API 返回的相似度
        "default": 45.0      # 未知策略
    }

    # === 2. 加权参数 ===
    CROSS_MATCH_BOOST = 8.0  # 每多命中一个不同类型的策略组，加多少分
    SEMANTIC_BOOLEAN_BOOST = 12.0 # 同时命中“语义”和“布尔”策略的额外奖励

    def _get_strategy_base_score(self, group_name: str) -> float:
        """根据策略组名称模糊匹配基础分"""
        name_lower = group_name.lower()
        
        # 语义检索跳过固定分
        if "语义" in name_lower or "semantic" in name_lower or "smart" in name_lower:
            return 0.0
            
        # 匹配权重表
        for key, weight in self.STRATEGY_WEIGHTS.items():
            if key in name_lower:
                return weight
        
        return self.STRATEGY_WEIGHTS["default"]

    def rank(self, results_collection: List[Dict]) -> List[Dict]:
        """
        核心排序方法
        :param results_collection: 扁平化的专利列表，必须包含 'matched_strategies' 字段
        :return: 排序并注入 _unified_score 的列表
        """
        if not results_collection:
            return []

        logger.info(f"正在执行混合评分排序 (Ranking {len(results_collection)} docs)...")

        for doc in results_collection:
            # 1. 提取语义原始分
            semantic_raw = doc.get("score", 0)
            
            # 2. 分析命中的策略
            matched_strategies = doc.get("matched_strategies", [])
            # 兜底：如果没有 matched_strategies，尝试用 _source_strategy
            if not matched_strategies and doc.get("_source_strategy"):
                matched_strategies = [doc.get("_source_strategy")]

            unique_group_types = set()
            max_boolean_score = 0.0

            for strat_tag in matched_strategies:
                # 假设 tag 格式为 "Group::Step"
                group_name = strat_tag.split("::")[0] if "::" in strat_tag else strat_tag
                unique_group_types.add(group_name)
                
                # 计算该策略对应的基础分，取最大值
                base = self._get_strategy_base_score(group_name)
                if base > max_boolean_score:
                    max_boolean_score = base

            # 3. 计算基础分 (语义分 vs 布尔策略分 取其高)
            base_final = max(semantic_raw, max_boolean_score)
            
            # 4. 计算 Boost (交叉验证奖励)
            boost = 0.0
            
            # 规则A: 命中多个策略组
            if len(unique_group_types) > 1:
                boost += (len(unique_group_types) - 1) * self.CROSS_MATCH_BOOST
            
            # 规则B: 同时有语义和布尔命中 (黄金文献特征)
            has_semantic = any("语义" in g or "semantic" in g.lower() for g in unique_group_types)
            has_boolean = any("语义" not in g and "semantic" not in g.lower() for g in unique_group_types)
            
            if has_semantic and has_boolean:
                boost += self.SEMANTIC_BOOLEAN_BOOST

            # 5. 合成最终分
            final_score = base_final + boost
            # 封顶 100 (可选，也可以允许突破100以凸显极高价值)
            final_score = min(final_score, 100.0)

            doc["_unified_score"] = round(final_score, 1)
            # 调试信息 (方便排查为什么得分高)
            doc["_score_debug"] = (
                f"Base:{base_final:.1f} (Sem:{semantic_raw}, Bool:{max_boolean_score}) | "
                f"Boost:{boost} (Groups:{len(unique_group_types)})"
            )

        # 6. 排序：分数降序 > 公开日降序
        sorted_docs = sorted(
            results_collection, 
            key=lambda x: (x.get("_unified_score", 0), x.get("publication_date", "")), 
            reverse=True
        )
        
        return sorted_docs