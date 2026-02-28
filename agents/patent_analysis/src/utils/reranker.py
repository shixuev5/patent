# src/utils/reranker.py

import re
from typing import List, Dict, Set
from loguru import logger

class Reranker:
    """
    [P0-2] 生产级语义重排序器 (Semantic Reranker)
    
    职责：
    对搜索引擎返回的 Top N 结果进行二次排序，将最有可能命中 X/Y 类的文档排到前面。
    解决 TF-IDF/BM25 关键词匹配分数与语义相关性不一致的问题。
    """

    def rank_docs(self, query_text: str, docs: List[Dict]) -> List[Dict]:
        """
        对文档列表进行重排序。
        
        Args:
            query_text: 锚点文本 (Report 'technical_means' 或核心特征)
            docs: 待排序的文档列表
        """
        if not docs or not query_text:
            return docs

        # 1. 预处理 Query 关键词
        keywords = self._extract_keywords(query_text)
        
        # 如果没有提取到有效关键词，直接返回原序
        if not keywords:
            return docs

        logger.debug(f"[Reranker] Ranking {len(docs)} docs with {len(keywords)} anchor keywords...")

        for doc in docs:
            # 2. 构建文档内容指纹 (Title + Abstract)
            title = str(doc.get("title", "")).lower()
            abstract = str(doc.get("abstract", "")).lower()
            content = f"{title} {abstract}"
            
            # 3. 计算基础分 (Base Score - Normalized)
            # 尝试归一化搜索引擎分数，防止 scale 差异太大
            raw_score = doc.get("score", 0)
            if isinstance(raw_score, (str, bytes)):
                try:
                    raw_score = float(raw_score)
                except ValueError:
                    raw_score = 0.0
            
            # 简单假设 score 大于 100 就算高分，将其压缩到 0-10 范围作为 base
            # 实际生产中建议使用 rank position 的倒数 (1/rank)
            base_score = min(raw_score / 10.0, 5.0) 
            
            # 4. 计算关键词覆盖分 (Coverage Score)
            # 核心逻辑：Technical Means 中的实词在文档中出现的密度
            hit_count = 0
            for kw in keywords:
                if kw in content:
                    hit_count += 1
            
            # 覆盖率 = 命中词数 / 总关键词数
            coverage_ratio = hit_count / len(keywords)
            coverage_boost = coverage_ratio * 20.0  # 权重系数 20
            
            # 5. 策略加权 (Strategy Boost)
            strategy_boost = 0.0
            
            # [Logic] E类/P类 高风险加权
            if doc.get("check_date_logic") == "Conflicting":
                strategy_boost += 5.0 # E类极其重要
            
            # [Logic] 来源加权 (Precision 意图的通常更准)
            if doc.get("source_intent") == "Precision":
                strategy_boost += 2.0
            
            # 6. 计算最终得分
            doc["rerank_score"] = base_score + coverage_boost + strategy_boost

        # 7. 执行排序 (降序)
        sorted_docs = sorted(
            docs, 
            key=lambda x: x.get("rerank_score", 0), 
            reverse=True
        )

        return sorted_docs

    def _extract_keywords(self, text: str) -> Set[str]:
        """
        简单的多语言关键词提取 (Zero-Dependency)
        """
        if not text:
            return set()
            
        # 移除常见的 Markdown 标记
        text = text.replace("**", "").replace("__", "").replace("##", "")
        
        # 正则切分：匹配中文连续字符 或 英文单词
        tokens = re.findall(r"[\u4e00-\u9fa5]+|[a-zA-Z0-9]+", text)
        
        # 简易停用词表 (Hardcoded for performance)
        stopwords = {
            "the", "a", "an", "in", "on", "of", "for", "with", "by", "is", "are", 
            "method", "device", "system", "apparatus", "comprising", "includes",
            "一种", "所述", "包括", "特征", "在于", "其中", "或者", "以及"
        }
        
        valid_keywords = set()
        for t in tokens:
            t = t.lower()
            if len(t) > 1 and t not in stopwords: 
                valid_keywords.add(t)
                
        return valid_keywords