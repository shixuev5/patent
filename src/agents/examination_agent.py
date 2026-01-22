import concurrent.futures
from pathlib import Path
from typing import List, Dict, Optional
from loguru import logger
from src.utils.llm import LLMService
from src.utils.cache import StepCache
from config import Settings


class ExaminationAgent:
    """
    AI 审查员代理
    职责：模拟专利审查员，对检索结果进行 Claim-to-Claim 的深度比对，
    判定文献类型 (X/Y/A/R) 并给出审查理由。
    """

    SYSTEM_PROMPT = """
你是一位专业的**专利审查员 (Patent Examiner)**。
你的任务是对提供的【对比文件】进行查新审查，判断其是否公开了【本申请】的核心技术特征。

### 审查判定标准 (Classification Criteria)
请严格基于以下标准进行分类：
1. **X类 (新颖性破坏)**: 
   - 对比文件公开了本申请权利要求所限定的全部核心特征。
   - 技术方案实质相同，单独对比即可否定新颖性。
2. **Y类 (创造性破坏)**: 
   - 对比文件公开了主要技术特征。
   - 解决了相同的技术问题。
   - 对本领域技术人员来说，该技术方案是显而易见的，或容易与其他文献结合。
3. **A类 (背景技术)**: 
   - 技术领域相同，反映了该领域的背景技术。
   - 但核心技术方案或关键特征差异较大，未公开本申请的发明点。
4. **R类 (不相关)**: 
   - 技术领域不同，或完全不相关的噪音文档。

### 评分标准 (AI Score)
- **X类**: 9-10 分
- **Y类**: 7-8 分
- **A类**: 4-6 分
- **R类**: 0-3 分

### 输出格式 (JSON Only)
你必须直接返回一个合法的 JSON 对象，不要包含 markdown 代码块标记（如 ```json ... ```）：
{
    "ai_score": <int>,           // 根据分类给出的整数分数
    "ai_category": "<string>",   // "X", "Y", "A", 或 "R"
    "ai_reason": "<string>"      // 审查员口吻的理由 (简练，100字以内)。例如："对比文件1公开了特征A和B，但未公开特征C..."
}
"""

    def __init__(self, report_data: Dict, cache_file: Optional[Path] = None):
        """
        Args:
            report_data: 专利分析报告数据
            cache_file: 缓存文件路径 (用于存储中间审查结果)
        """
        self.cache = StepCache(cache_file) if cache_file else None
        
        self.llm_service = LLMService(
            api_key=Settings.LLM_EXAM_API_KEY, base_url=Settings.LLM_EXAM_BASE_URL
        )
        self.model_name = Settings.LLM_MODEL_EXAM
        
        self.report_data = report_data

        # 1. 预处理：构建对比基准 (The Anchor)
        self.anchor_context = self._build_anchor_context()

    def _build_anchor_context(self) -> str:
        """
        构建核心技术指纹 (The Anchor)
        """
        feature_map = {
            f.get("name", "").strip(): f.get("description", "").strip()
            for f in self.report_data.get("technical_features", [])
        }

        processed_features = set()
        core_features_text = []

        # 按分数降序排列效果
        sorted_effects = sorted(
            self.report_data.get("technical_effects", []), 
            key=lambda x: x.get("tcs_score", 0), 
            reverse=True
        )

        for effect in sorted_effects:
            score = effect.get("tcs_score", 0)
            if score < 4:
                continue

            effect_summary = effect.get("effect", "")[:20] 
            
            for feat_name in effect.get("contributing_features", []):
                clean_name = str(feat_name).strip()
                if clean_name in processed_features:
                    continue
                
                desc = feature_map.get(clean_name, "详情见权利要求")
                desc = desc.replace("\n", " ")[:200] 

                entry = f"- 【{clean_name}】(贡献效果: {effect_summary}...): {desc}"
                core_features_text.append(entry)
                processed_features.add(clean_name)

        return f"""
<本申请核心档案 (Target Patent Anchor)>
**核心技术问题**: {self.report_data.get('technical_problem', '未定义')}
**核心技术手段**: {self.report_data.get('technical_means', '')[:600]}

**必须重点比对的核心权利要求特征**:
{chr(10).join(core_features_text)}
"""

    def examine_and_filter(
        self, search_results: List[Dict], top_k: int = 15
    ) -> List[Dict]:
        """
        执行审查过滤的主流程
        """
        logger.info(
            f"[Examiner] 开始对 {len(search_results)} 篇文档进行深度审查 (Model: {self.model_name})..."
        )

        evaluated_results = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            future_to_doc = {
                executor.submit(self._examine_single_patent, doc): doc
                for doc in search_results
            }

            for future in concurrent.futures.as_completed(future_to_doc):
                doc = future_to_doc[future]
                try:
                    evaluation = future.result()
                    if evaluation:
                        doc.update(evaluation)
                        evaluated_results.append(doc)
                except Exception as e:
                    logger.error(f"Error evaluating patent {doc.get('pn')}: {e}")

        # 排序策略：类别优先 (X>Y>A>R)，分数其次
        type_priority = {"X": 3, "Y": 2, "A": 1, "R": 0}

        evaluated_results.sort(
            key=lambda x: (
                type_priority.get(x.get("ai_category", "R"), 0),
                x.get("ai_score", 0),
            ),
            reverse=True,
        )

        final_list = [r for r in evaluated_results if r.get("ai_category") != "R"]
        return final_list[:top_k]

    def _examine_single_patent(self, doc: Dict) -> Dict:
        """
        调用 LLM 对单篇专利进行画像
        """
        pn = doc.get('pn')
        if not pn:
            return {}
        
        # === 1. 缓存检查 ===
        cache_key = f"exam_{pn}"
        if self.cache:
            cached_val = self.cache.get(cache_key)
            if isinstance(cached_val, dict) and "ai_category" in cached_val:
                logger.debug(f"[Examiner] Hit Cache: {pn} -> {cached_val.get('ai_category')}")
                return cached_val
                
        # === 2. 构造 User Context (动态数据) ===
        claims_text = doc.get("claims", "")
        if len(claims_text) > 1500:
            claims_text = claims_text[:1500] + "...(truncated)"

        # 组合 User Prompt：包含 "基准(Anchor)" 和 "待测(Reference)"
        user_content = f"""
请根据系统指令，对比以下两份技术文件：

{self.anchor_context}

--------------------------------------------------

<待审查对比文件 (Reference Document)>
**公开号**: {doc.get('pn')}
**标题**: {doc.get('title')}
**摘要**: {doc.get('abstract')}
**权利要求片段**: 
{claims_text}
"""

        # === 3. 调用 LLM (分离 System 与 User) ===
        result = {}
        try:
            response = self.llm_service.chat_completion_json(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": user_content}
                ],
                temperature=0.1
            )
            
            if isinstance(response, list) and len(response) > 0:
                result = response[0]
            elif isinstance(response, dict):
                result = response
            
            # === 4. 写入缓存 ===
            if self.cache and result and "ai_category" in result:
                self.cache.save(cache_key, result)
                
        except Exception as e:
            logger.warning(f"[Examiner] Analysis failed for {pn}: {e}")
        
        return result