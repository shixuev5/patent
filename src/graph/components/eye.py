# src/graph/components/eye.py

import re
from typing import Dict, List, Any, Set
from loguru import logger
import jieba

from src.utils.llm import get_llm_service
from src.search_clients.factory import SearchClientFactory
from config import settings


class QuickEye:
    """
    生产级阅卷组件 (QuickEye) - Final Production Version
    
    核心能力：
    1. 两阶段阅卷 (Two-Stage Screening): Python(Jieba)粗筛 + LLM精读。
    2. 全文证据定位 (Full-Text Localization): 解决摘要致盲问题。
    3. 特征比对表生成 (Claim Chart Generation): 输出结构化证据。
    4. 创造性验证 (Inventive Step Check): 针对 D2 的快速判定。
    """

    # 内置基础中文停用词，生产环境可从文件加载
    STOP_WORDS = {
        "的", "了", "和", "是", "就", "都", "而", "及", "与", "着", 
        "或", "一个", "没有", "我们", "你们", "它们", "在", "对", "对于", 
        "把", "被", "让", "向", "往", "到", "为", "为了", "因为", "所以",
        "the", "a", "an", "and", "or", "of", "to", "in", "for", "with"
    }

    def __init__(self, report_data: Dict):
        self.report_data = report_data
        self.llm = get_llm_service()
        
        # 初始化 Jieba (预热)
        jieba.initialize()
        
        # 初始化全文获取客户端
        self.client = SearchClientFactory.get_client("zhihuiya")
        
        # 1. 预处理核心特征 (Anchor)
        self.target_features = self._extract_target_features()
        self.anchor_context = self._build_anchor_str(self.target_features)
        
        # 2. 预计算特征关键词 (用于 Python 级全文切片)
        # 结构: {'F1': {'keyword1', 'keyword2'}, ...}
        self.feature_keywords_map = {
            f['id']: self._extract_keywords(f) 
            for f in self.target_features
        }
        # 扁平化所有关键词用于全局搜索
        self.all_keywords_flat = set()
        for k_set in self.feature_keywords_map.values():
            self.all_keywords_flat.update(k_set)

    def quick_screen(self, docs: List[Dict], top_k: int = 5) -> Dict[str, Any]:
        """
        [主入口] 对 Top K 文档进行深度阅卷。
        """
        candidates = docs[:top_k]
        
        best_doc = None
        max_match_count = -1
        missing_feats = []
        
        logger.info(f"[Eye] Deep screening top {len(candidates)} docs (Full-Text + Jieba)...")

        for doc in candidates:
            # 1. 获取证据上下文 (优先摘要，必要时取全文切片)
            evidence_text = self._get_smart_context(doc)
            
            # 2. 调用 Smart Model 进行深度比对 (Claim Chart)
            analysis = self._llm_generate_claim_chart(doc, evidence_text)
            
            # 挂载分析结果
            doc["analysis"] = analysis
            
            # 3. 判定逻辑
            # 统计 "disclosed" 的特征数量
            match_cnt = len([
                m for m in analysis.get("claim_chart", []) 
                if m.get("status") == "disclosed"
            ])
            
            # 更新最佳文档
            if match_cnt > max_match_count:
                max_match_count = match_cnt
                best_doc = doc
            
            # X 类判定
            if len(self.target_features) > 0 and match_cnt == len(self.target_features):
                logger.success(f"[Eye] FOUND X-CLASS DOC: {doc.get('pn')} (Score: {match_cnt}/{len(self.target_features)})")
                doc["is_x_class"] = True
                return {
                    "found_x": True, 
                    "best_doc": doc, 
                    "missing_features": []
                }

        # 4. 如果没找到完美 X，提取 D1 的区别特征
        if best_doc:
            chart = best_doc.get("analysis", {}).get("claim_chart", [])
            missing_feats = [
                item.get("feature_name", "") 
                for item in chart 
                if item.get("status") == "not_disclosed"
            ]

        return {
            "found_x": False, 
            "best_doc": best_doc, 
            "missing_features": missing_feats
        }

    def check_secondary_reference(self, doc: Dict, feature_name: str) -> Dict[str, Any]:
        """
        [Step 7 Logic] 针对特定特征验证 D2 (副引证)。
        """
        # 1. 寻找该特征对应的关键词
        target_keys = set()
        for f in self.target_features:
            if f['name'] == feature_name:
                target_keys = self.feature_keywords_map.get(f['id'], set())
                break
        
        if not target_keys:
            # 降级：直接对 feature name 分词
            target_keys = set(jieba.cut_for_search(feature_name)) - self.STOP_WORDS

        # 2. 定向获取上下文
        context = ""
        try:
            full_text = self.client.get_full_text(doc.get("uid"))
            if full_text:
                context = self._find_relevant_paragraphs(full_text, target_keys, top_n=3)
        except Exception:
            pass # 回退
            
        if not context:
            context = doc.get("abstract", "")

        # 3. Fast Model 判定
        prompt = f"""
        Determine if the reference discloses the specific feature: "{feature_name}".
        
        Reference Text:
        {context[:2500]}
        
        Return JSON: {{"is_disclosed": true/false, "evidence": "quote...", "reasoning": "..."}}
        """

        try:
            return self.llm.chat_completion_json(
                model=settings.LLM_MODEL_EXAM,
                messages=[{"role": "user", "content": prompt}]
            )
        except Exception:
            return {"is_disclosed": False}

    # =========================================================================
    # Internal Helpers (Context & Analysis)
    # =========================================================================

    def _get_smart_context(self, doc: Dict) -> str:
        """
        [Step 10 Core] 智能上下文获取。
        尝试获取全文 -> Jieba 分词匹配 -> Top N 段落。
        """
        abstract = doc.get("abstract", "")
        uid = doc.get("uid") or doc.get("id") or doc.get("pn")
        
        if not uid:
             return f"【摘要 (Abstract)】\n{abstract}"

        # 尝试获取全文
        try:
            full_text = self.client.get_full_text(uid)
            if full_text and len(full_text) > 200:
                # Python 级切片 (Zero Token Cost)
                snippets = self._find_relevant_paragraphs(
                    full_text, 
                    self.all_keywords_flat, 
                    top_n=8
                )
                
                if snippets:
                    doc["full_text_snippets"] = snippets
                    return f"【全文核心段落节选 (Full-Text Excerpts)】\n{snippets}"
        except Exception as e:
            logger.debug(f"Full text fetch failed for {uid}: {e}")
        
        return f"【摘要 (Abstract)】\n{abstract}"

    def _find_relevant_paragraphs(self, text: str, keywords: Set[str], top_n: int = 5) -> str:
        """
        基于 Jieba 分词的段落召回算法。
        相比简单的字符串匹配，能更准确地计算词频密度。
        """
        # 1. 健壮的段落切分 (处理不规范的换行)
        # 移除过多的空白字符
        text = re.sub(r'\n\s*\n', '\n', text)
        raw_paragraphs = [p.strip() for p in text.split('\n')]
        
        # 过滤过短段落 (通常是页眉页脚或标题)
        paragraphs = [p for p in raw_paragraphs if len(p) > 25]
        
        if not paragraphs:
            return ""

        scored_paragraphs = []

        for p in paragraphs:
            # 使用 search 模式分词，提高召回率
            # 生产环境优化：对于超长段落，可以截断处理
            p_tokens = set(jieba.cut_for_search(p[:2000]))
            
            # 计算交集 (Hit Count)
            hits = 0
            for t in p_tokens:
                if t in keywords:
                    hits += 1
            
            if hits > 0:
                # 评分策略: 命中数 + 段落长度惩罚(可选)
                scored_paragraphs.append((hits, p))
        
        # 按分数降序
        scored_paragraphs.sort(key=lambda x: x[0], reverse=True)
        
        # 取 Top N
        top_ps = [p for s, p in scored_paragraphs[:top_n]]
        
        return "\n...\n".join(top_ps)

    def _llm_generate_claim_chart(self, doc: Dict, context: str) -> Dict:
        """
        调用 LLM 生成特征比对表。
        """
        prompt = f"""
        你是资深专利审查员。任务是生成特征比对表 (Claim Chart)。

        【本申请核心特征 (Target)】
        {self.anchor_context}

        【对比文件 (Reference)】
        Title: {doc.get('title')}
        Content:
        {context[:6000]} 
        
        【指令】
        逐一判断 Reference 是否公开了 Target 中的每个特征。
        1. status: "disclosed" (公开) / "not_disclosed" (未公开)。
        2. evidence: 引用原文。
        3. reasoning: 简短说明。
        
        输出 JSON:
        {{
            "claim_chart": [
                {{
                    "feature_id": "F1",
                    "feature_name": "...",
                    "status": "disclosed", 
                    "evidence": "...",
                    "reasoning": "..."
                }}
            ],
            "match_score": 0-100 (整数)
        }}
        """

        try:
            resp = self.llm.chat_completion_json(
                model=settings.LLM_MODEL_REASONING, 
                messages=[{"role": "user", "content": prompt}],
            )
            if isinstance(resp, dict):
                return resp
            return {"claim_chart": [], "match_score": 0}
        except Exception as e:
            logger.error(f"LLM analysis failed: {e}")
            return {"claim_chart": [], "match_score": 0}

    # =========================================================================
    # Initialization Helpers
    # =========================================================================

    def _extract_target_features(self) -> List[Dict]:
        """
        从 report_data 中提取高价值特征 (Score >= 3)。
        """
        raw = self.report_data.get("technical_features", [])
        # 按 TCS 分数降序
        sorted_feats = sorted(raw, key=lambda x: x.get("tcs_score", 0), reverse=True)
        
        targets = []
        for idx, f in enumerate(sorted_feats):
            if f.get("tcs_score", 0) < 3:
                continue
            targets.append({
                "id": f"F{idx+1}",
                "name": f.get("name", ""),
                "desc": f.get("description", "")[:200],
                "score": f.get("tcs_score", 0)
            })
        return targets

    def _extract_keywords(self, feature: Dict) -> Set[str]:
        """
        使用 Jieba 提取特征中的核心名词，构成关键词集合。
        """
        text = f"{feature['name']} {feature['desc']}"
        # 使用 search 模式分词
        tokens = jieba.cut_for_search(text)
        
        valid_keys = set()
        for t in tokens:
            t = t.strip()
            # 过滤短词、数字和停用词
            if len(t) > 1 and not t.isdigit() and t not in self.STOP_WORDS:
                valid_keys.add(t)
        
        return valid_keys

    def _build_anchor_str(self, features: List[Dict]) -> str:
        lines = []
        for f in features:
            lines.append(f"- [{f['id']}] {f['name']} (Score:{f['score']}): {f['desc']}")
        return "\n".join(lines)