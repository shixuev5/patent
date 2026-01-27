# src/graph/components/brain.py

import json
import re
from typing import List, Dict, Optional
from loguru import logger

from config import settings
from src.utils.llm import get_llm_service
from src.graph.consts import (
    PHASE_TIER0_SCOUT,
    PHASE_TIER1_X,
    PHASE_TIER2_Y,
    PHASE_TIER3_BROAD,
)


class StrategyBrain:
    """
    生产级大脑组件 (StrategyBrain) - Full Prompt Version
    
    核心能力：
    1. TCS-Guided Context: 基于评分构建检索积木 (Block A/B/C)。
    2. Adaptive Planning: 自适应 Tier 1/2/3 及 E类/Y类 特殊策略。
    3. Diff-Driven Search: 针对区别特征生成 D2 检索策略。
    4. Syntax Translation: 专业的 Patsnap 语法翻译 (TAC, s/p, w/n)。
    """

    def __init__(self, patent_data: Dict, report_data: Dict):
        self.patent_data = patent_data
        self.report_data = report_data
        self.llm_service = get_llm_service()

        # 提取基础分类号 (原始案卷)
        self.base_ipcs = self.patent_data.get("bibliographic_data", {}).get(
            "ipc_classifications", []
        )

    def build_search_matrix(self) -> List[Dict]:
        """
        [Phase: INIT] 构建检索要素矩阵 (ABC Blocks)。
        基于 TCS 评分，将特征划分为 Subject(A), KeyFeature(B), Functional(C)。
        """
        logger.info("[Brain] Building Search Matrix based on TCS scores...")

        # 1. 构建带有评分权重的上下文
        context = self._build_matrix_context()

        # 2. 调用 Reasoning Model 生成高质量矩阵
        matrix = self._llm_build_matrix(context)
        return matrix

    def plan_for_phase(
        self, 
        phase: str, 
        search_matrix: List[Dict], 
        executed_intents: List[str] = None,
        executed_queries: List[str] = None,
        diff_features: List[str] = None,
        validated_ipcs: List[str] = None
    ) -> List[Dict]:
        """
        [Phase: TIER 1/2/3] 核心规划入口。
        根据当前阶段和反馈 (Diffs, IPCs) 生成具体策略。
        """
        # 决定使用的 IPC：优先使用校准后的，否则用原始的
        active_ipcs = validated_ipcs if validated_ipcs else self.base_ipcs
        executed_intents = executed_intents or []
        executed_queries = set(executed_queries or []) # 转为集合加速查找
        
        logger.info(f"[Brain] Planning strategies for {phase}. IPCs: {active_ipcs[:3]}...")
        strategies = []

        # --- [NEW] 策略 0: 侦察兵 (Tier 0 Scout) ---
        # 目标: 快速获取 Top 相关文档，用于 Keyword Harvesting 和 IPC Calibration
        if phase == PHASE_TIER0_SCOUT:
            logger.info("  Generating Scouting Strategy (Semantic)...")
            # 1. 语义检索 (从原 Tier 3 移至此处)
            strategies.append(self._get_semantic_strategy())
            
            # 2. [Optional] 极简的核心词布尔检索 (无 IPC 限制，用于防语义漏检)
            # 仅使用 Block B 中 Score=5 的最核心词
            pivot_query = self._get_pivot_scout_strategy(search_matrix)
            if pivot_query:
                strategies.append(pivot_query)

        # --- 策略 1: 精准打击 (Tier 1 Precision) ---
        # 此时的 search_matrix 和 active_ipcs 应该已经被 Tier 0 校准过
        elif phase == PHASE_TIER1_X:
            target_intents = ["Precision", "Synergy", "Conflicting_E"]
            
            # 生成通用计划
            planner_context = self._build_planner_context(search_matrix, active_ipcs, executed_intents)
            universal_plan = self._llm_generate_universal_plan(planner_context)
            filtered_plan = [p for p in universal_plan if p.get("intent") in target_intents]
            
            # 翻译
            translator_context = self._build_translator_context(search_matrix, filtered_plan, active_ipcs)
            strategies.extend(self._llm_translate_to_patsnap(translator_context))

        # --- 策略 2: 创造性结合 (Tier 2 Inventive) ---
        elif phase == PHASE_TIER2_Y:
            # A. 针对区别特征 (Diff) 的打击
            if diff_features:
                logger.info(f"  Targeting Diff Features: {diff_features}")
                y_context = self._build_diff_context(search_matrix, diff_features)
                y_plan = self._llm_generate_diff_plan(y_context)
                
                translator_context = self._build_translator_context(search_matrix, y_plan, active_ipcs)
                strategies.extend(self._llm_translate_to_patsnap(translator_context))

            # B. 常规泛化打击 (防止 Diff 找不全)
            target_intents = ["Competitor", "Functional", "Component", "Broad"]
            planner_context = self._build_planner_context(search_matrix, active_ipcs, executed_intents)
            universal_plan = self._llm_generate_universal_plan(planner_context)
            filtered_plan = [p for p in universal_plan if p.get("intent") in target_intents]
            
            translator_context = self._build_translator_context(search_matrix, filtered_plan, active_ipcs)
            strategies.extend(self._llm_translate_to_patsnap(translator_context))

        # --- 策略 3: 兜底 (Tier 3 Broad Fallback) ---
        elif phase == PHASE_TIER3_BROAD:
            logger.info("  Generating Fallback Broad Strategy...")
            # 这里可以放宽 IPC 限制，或者做简单的 A+B 检索
            # 如果 Tier 0 的语义检索没找到好结果，这里可以尝试用不同的描述再跑一次语义
            pass 

        # --- 全局去重 ---
        unique_strategies = []
        local_seen = set()
        for s in strategies:
            s["status"] = "pending" # 确保状态重置
            q_str = s.get("query", "").strip()
            if not q_str: continue
            if q_str in executed_queries: continue
            if q_str in local_seen: continue
            local_seen.add(q_str)
            unique_strategies.append(s)

        return unique_strategies

    def _get_pivot_scout_strategy(self, matrix: List[Dict]) -> Optional[Dict]:
        """
        [Helper] 生成一个极简的布尔侦察查询。
        仅使用 Score 5 的核心词，不加任何限制，用于捕获跨领域的噪音较小的核心专利。
        """
        # 寻找 Role=KeyFeature 且出现在 Block B 的词
        core_feats = [m for m in matrix if m.get("role") == "KeyFeature"]
        if not core_feats:
            return None
        
        # 取第一个最核心的词
        feat = core_feats[0]
        # 构造简单的 TAC 查询
        keywords = " OR ".join(feat.get("en_expand", [])[:3]) # 取前3个英文扩展
        if not keywords: return None
        
        return {
            "name": f"Scout-Pivot-{feat['concept_key']}",
            "intent": "Broad", # 标记为 Broad
            "db": "Patsnap",
            "query": f"TAC:({keywords})" 
        }

    def _build_matrix_context(self) -> str:
        """Step 1 Context: 基于 TCS 评分构建技术理解环境。"""
        biblio = self.patent_data.get("bibliographic_data", {})
        report = self.report_data
        
        feature_max_scores = {}
        for effect in report.get("technical_effects", []):
            score = effect.get("tcs_score", 0)
            for feat_name in effect.get("contributing_features", []):
                feat_name = str(feat_name).strip()
                if score > feature_max_scores.get(feat_name, 0):
                    feature_max_scores[feat_name] = score

        block_b_content = [] 
        block_c_content = []
        
        feature_details = {f.get("name", "").strip(): f for f in report.get("technical_features", [])}

        for feat_name, info in feature_details.items():
            score = feature_max_scores.get(feat_name, 0)
            desc = info.get("description", "无描述").replace("\n", " ").strip()
            rationale = info.get("rationale", "").replace("\n", " ").strip()
            
            entry = (
                f"- 【{feat_name}】 (TCS: {score})\n"
                f"    定义: {desc}\n"
                f"    原理: {rationale[:200]}..."
            )
            
            if score >= 5:
                block_b_content.append(entry)
            elif score >= 3:
                block_c_content.append(entry)

        effects_summary = []
        for e in report.get("technical_effects", []):
            score = e.get("tcs_score", 0)
            if score >= 3:
                effects_summary.append(f"- [Score {score}] {e.get('effect')}")

        return f"""
        [发明名称] {biblio.get('invention_title')}
        [IPC参考] {', '.join(self.base_ipcs[:5])}

        === 1. Block A: 技术领域 (Subject & Field) ===
        [检索主语 (核心产品/方法)]
        {report.get("claim_subject_matter", "未定义")}
        
        [所属领域 (用于锁定 IPC/CPC 大类)]
        {report.get("technical_field", "未定义")}

        === 2. Block B: 核心创新点 (Key Features - Vital) ===
        *** TCS Score 5分特征 (必须作为核心检索词) ***
        {chr(10).join(block_b_content) if block_b_content else "（未识别到5分特征，请从技术手段中提取）"}

        === 3. Block C: 功能与限定 (Functional - Enabler/Improver) ===
        *** TCS Score 3-4分特征 (作为限定条件或降噪词) ***
        {chr(10).join(block_c_content)}

        [关键技术效果参考 (Effects)]
        {chr(10).join(effects_summary)}
        
        === 4. 补充上下文 (Technical Context) ===
        {report.get('technical_means', '未定义')[:800]}
        """

    def _build_planner_context(self, matrix: List[Dict], ipcs: List[str], executed_intents: List[str] = None) -> str:
        """
        Tier 1/2 Context: 为 Planner 提供作战地图。
        [Step 6] 使用 active_ipcs 替代原始 IPC。
        [Refinement] 传入 executed_intents。
        """
        blocks = {"Subject": [], "KeyFeature": [], "Functional": [], "Other": []}
        for item in matrix:
            role = item.get("role", "Other")
            if role not in blocks: role = "Other"
            
            cn_preview = ",".join(item.get("zh_expand", [])[:3])
            en_preview = ",".join(item.get("en_expand", [])[:3])
            
            blocks[role].append(f"   - [ID: {item['concept_key']}] (Type: {item.get('feature_type', 'N/A')})\n     Preview: {cn_preview} | {en_preview}")

        inventory = []
        for role, items in blocks.items():
            if items:
                inventory.append(f"=== Block {role} ===\n" + "\n".join(items))

        # Synergy Clusters
        clusters_text = []
        for effect in self.report_data.get("technical_effects", []):
            contributors = effect.get("contributing_features", [])
            valid_contributors = [f for f in contributors if any(m['concept_key'] == f for m in matrix)]
            if len(valid_contributors) >= 2:
                clusters_text.append(f"- [Synergy]: {effect.get('effect')} -> {json.dumps(valid_contributors, ensure_ascii=False)}")

        # Pivot Features
        high_score_features = []
        for feat in self.report_data.get("technical_features", []):
            if feat.get("tcs_score", 0) >= 4:
                high_score_features.append(f"{feat.get('name')} (Score:{feat.get('tcs_score')})")

        biblio = self.patent_data.get("bibliographic_data", {})
        applicants = [a["name"] for a in biblio.get("applicants", [])]
        
        executed_str = ", ".join(executed_intents) if executed_intents else "None"
        
        return f"""
        【作战地图：检索要素清单 (Search Inventory)】
        {chr(10).join(inventory)}

        【核心策略输入 1: 协同聚类 (Synergy Clusters)】
        {chr(10).join(clusters_text) if clusters_text else "暂无强聚类关系。"}

        【核心策略输入 2: 高分轴心 (Pivot Features)】
        Pivot Candidates: {', '.join(high_score_features)}

        【案情背景 & 历史】
        - Working IPCs (Calibrated): {', '.join(ipcs[:5])}
        - Applicant: {json.dumps(applicants[:3], ensure_ascii=False)}
        - Previously Attempted Intents: {executed_str}
        """

    def _build_diff_context(self, matrix: List[Dict], diffs: List[str]) -> str:
        """
        Tier 2 Diff Context: 针对区别特征。
        """
        return f"""
        【作战状态更新】
        我们在 Tier 1 中已找到最接近的现有技术 (Closest Prior Art / D1)。
        但 D1 未公开以下 **区别特征 (Distinctive Features)**:
        {json.dumps(diffs, ensure_ascii=False)}

        【任务目标 - 寻找 Y 类证据 (Inventive Step)】
        我们需要寻找现有技术 D2，它公开了上述区别特征，并给出了将其应用于 D1 的启示。
        
        【检索要素清单 (Search Inventory)】
        (LLM应基于内部知识扩展区别特征的同义词，或参考 Search Matrix)
        """

    def _build_translator_context(self, matrix: List[Dict], plan: List[Dict], ipcs: List[str]) -> str:
        """
        Translation Context: 词汇表 + 计划 + 约束。
        """
        vocabulary = []
        for item in matrix:
            zh = [w for w in item.get("zh_expand", []) if len(w)>1]
            en = [w for w in item.get("en_expand", []) if len(w)>2]
            
            zh_display = [f"{w}(需拆解)" if len(w)>4 else w for w in zh[:8]]
            
            vocab_entry = (
                f"ID: [{item['concept_key']}]\n"
                f"  - CN_Keywords: {', '.join(zh_display)} ...\n"
                f"  - EN_Keywords: {', '.join(en[:8])} ...\n"
                f"  - Self_IPC (For Broad Search): {', '.join(item.get('ipc_cpc_ref', []))}"
            )
            vocabulary.append(vocab_entry)

        biblio = self.patent_data.get("bibliographic_data", {})
        raw_applicants = [a["name"] for a in biblio.get("applicants", [])]
        raw_inventors = biblio.get("inventors", [])

        constraints = (
            f"- Working IPCs (Calibrated): {', '.join(ipcs[:5])}\n"
            f"- Raw Assignees: {json.dumps(raw_applicants, ensure_ascii=False)}\n"
            f"- Raw Inventors: {json.dumps(raw_inventors, ensure_ascii=False)}"
        )

        return f"""
        === 1. 词汇映射表 (Vocabulary Map) ===
        *Translator 注意：标记为 (需拆解) 的长词严禁直接使用，必须使用 'nD' 或 'S' 算符拆分。*
        {chr(10).join(vocabulary)}

        === 2. 背景约束数据 (Constraints Data) ===
        *用于 Trace (申请人) 策略*
        {constraints}

        === 3. 通用检索计划 (Universal Plan) ===
        *请将以下每一条 Plan 翻译为目标数据库的查询语句*
        {json.dumps(plan, ensure_ascii=False, indent=2)}
        """

    # =========================================================================
    # LLM Calls (Reasoning & Translation)
    # =========================================================================

    def _llm_build_matrix(self, context: str) -> List[Dict]:
        """Step 1: 生成矩阵 (使用完整的生产级 Prompt)"""
        logger.info("[SearchAgent] Step 1: Building Search Matrix with TCS-Guided Strategy...")

        system_prompt = """
        你是一位拥有 20 年经验的专利检索专家。请基于提供的**经过 TCS (技术贡献评分) 预处理**的技术交底信息，拆解技术方案并构建**检索要素表（Search Matrix）**。

        ### 核心任务：ABC 映射 (基于输入上下文的评分)
        输入信息已经将特征按 TCS 分数进行了分组，请严格遵循以下映射逻辑提取 4-6 个检索要素：

        1.  **Block A - 技术领域 (Subject)**: (1 个) 
            - **来源**: 直接提取上下文中的 **[保护主题]**。
            - **作用**: 检索式的主语（基础产品）。
            
        2.  **Block B - 核心创新点 (Key Features)**: (2-3 个, **最高优先级**) 
            - **来源**: 必须且只能来自上下文的 **[Block B: 核心创新点 (Score 5)]** 区域。
            - **指令**: 忽略所有 TCS Score < 5 的特征，除非 Block B 为空。
            - **定义**: 这是区别于现有技术的根本特征。如果 Block B 为空，请从 Block C 中提拔得分最高的特征。
            
        3.  **Block C - 功能/效果/修饰 (Functional)**: (1-2 个) 
            - **来源**: 优先从 **[Block C (Score 3-4)]** 中提取。
            - **指令**: 选择那些能解释 Block B "为什么能起作用" 的关键参数或效果（如“db02小波”、“Shannon熵”）。

        ### 扩展原则 (Expansion Rules)：
        对于每个提取的要素，必须进行全方位的“检索语言翻译”：
        
        1.  **中文扩展 (CN)**:
            - 语域转换 (关键): 必须同时覆盖 **口语/俗称** (如“手机”、“猫眼”) 和 **专利法律/学术术语** (如“移动终端”、“光电窥孔”、“图像采集装置”)。
            - 缩略语与全称: 包含常见的中文简称及混合写法 (如“无人机” <-> “UAV” <-> “无人驾驶飞行器”)。
            - 结构-功能互换: 
                - 若是结构词，扩展其功能描述 (如“弹簧” -> “弹性件”、“偏置部件”)。
                - 若是功能词，扩展其常见实现载体 (如“显示” -> “屏幕”、“面板”、“显示器”)。
            - 适度上位化: 仅扩展至**本领域通用**的上位概念，禁止无限泛化 (如“锂电池”可扩展为“二次电池”，但不可扩展为“能源设备”)。
        2.  **英文扩展 (EN)**:
            - 使用 **Patentese (专利法律英语)**。如 `plurality`, `configured to`, `assembly`, `means` 等专业表达。
            - 强制使用截词符: 如 `sensor+` (涵盖 sensors, sensory), `configur+`。
            - 词性变化: 动词/名词形式必须同时包含 (e.g., `mix+` for mixing, mixer, mixture)。
            - 美式/英式拼写: 如 `colo?r`, `alumini?um` (若支持正则) 或列出两种拼写。
        3.  **分类号 (IPC/CPC)**:
            - 维度区分:
                - 对于 **Block A (技术领域)**: 提供 **应用类** 分类号 (如: 车辆 B60)。
                - 对于 **Block B/C (功能部件)**: 提供 **功能类** 分类号 (如: 电池 H01M, 数据处理 G06F)。
            - 精度控制:
                - 默认级别：**大组 (Main Group)** (如 `H01M 10/00`)。
                - 仅当且仅当上下文中有明确的具体技术术语（如“锂离子”）时，才精确到 **小组 (Subgroup)** (如 `H01M 10/0525`)。
            - 格式: 严格使用标准格式，包含斜杠和空格 (如 `G06F 17/30`, `H04W 72/04`)。

        ### 输出格式 (JSON List Only)
        必须严格输出标准的 JSON 列表，**严禁使用 Markdown 代码块 (```json)**，严禁包含任何解释性文字。结构如下：
        [
          {
            "concept_key": "核心概念词 (如: 柔性屏幕)",
            "role": "KeyFeature",           // 必须准确对应: Subject, KeyFeature (对应 Block B), Functional (对应 Block C)
            "feature_type": "Apparatus",    // 选项: Apparatus (侧重名词), Method (侧重动词)
            "zh_expand": ["柔性屏幕", "柔性显示器", "..."], 
            "en_expand": ["flex+ screen", "flex+ display", "..."],
            "ipc_cpc_ref": ["G09F 9/30"]
          },
          ...
        ]
        """

        response = self.llm_service.chat_completion_json(
            model=settings.LLM_MODEL_REASONING,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context},
            ],
        )
        return response if isinstance(response, list) else []

    def _llm_generate_universal_plan(self, context: str) -> List[Dict]:
        """Step 2.1: 生成通用检索计划 (含 E类/Trace/Broad/Precision)"""
        logger.info("[SearchAgent] Step 2.1: Planning Universal Strategy...")

        system_prompt = """
        你是一位 **高级专利检索策略专家 (Senior Search Strategist)**。
        你的任务是基于提供的【作战地图 (Search Inventory)】、【协同聚类】和【高分轴心】，制定一份覆盖全场景的检索计划。

        ### 核心任务：制定 9 类战术意图
        请根据案情灵活组合以下策略。如果某类 Block (如 Block C) 数据为空，则跳过相关策略。

        1.  **[Trace] 来源追踪 (Source Tracking)**
            -   **目标**: 锁定申请人或发明人的相关专利。
            -   **逻辑**: 
                - 若申请人是初创/个人: 仅使用 ["Assignee", "Inventor"]。
                - **降噪 (Noise Reduction)**: 若申请人为大型企业(如华为/波音)或常见名，必须追加 "IPC" 限制。
            -   **适用源**: Patent。

        2.  **[Competitor] 竞争对手技术穿透 (Competitor-Tech Intersection)**
            -   **目标**: 精准打击特定申请人（尤其是本案申请人）在该技术点上的布局，寻找自我抵触。
            -   **逻辑**: 必须包含 "Assignee" 以及 "Block B (KeyFeature)" 中的核心词。
            -   **组件**: ["Assignee", "ID_of_KeyFeature"]。

        3.  **[Precision] 核心新颖性打击 (Novelty - X Document)**
            -   **目标**: 寻找与【Block B (KeyFeature)】完全一致的现有技术。
            -   **路径**: 
                - 优先: "Block A (Subject) + Pivot Feature (高分轴心)"。
                - 常规: "Block A (Subject) + Block B (其他特征)"。
            -   **要求**: 强调 **SameSentence (同句)** 紧密逻辑。

        4.  **[Synergy] 方案复现与协同 (Full Embodiment)**
            -   **目标**: 验证【协同聚类】中的特征组合，或寻找完整的实施例。
            -   **路径**:
                - 若存在【协同聚类 (Clusters)】: 直接提取 Cluster 中的 Bound Components 进行组合。
                - 常规: "Block A + Block B + Block C (Functional)"。
            -   **要求**: 强调 **SameParagraph (同段)** 或 **BooleanAND (全文)**。

        5.  **[Functional] 功能性泛化 (Problem-Solution - Y Document)**
            -   **目标**: 寻找“不同结构但实现相同功能”的现有技术。
            -   **逻辑**: **保留 Block A (Subject)，保留 Block C (Functional)，刻意丢弃 Block B**。
            -   **组件**: ["ID_of_Subject", "ID_of_Function"]。

        6.  **[Component] 组件跨界应用 (Cross-Domain Component)**
            -   **目标**: 寻找该核心结构在其他领域（非 Block A）的应用。
            -   **逻辑**: **保留 Block B，保留 Block C，刻意丢弃 Block A**。
            -   **组件**: ["ID_of_KeyFeature", "ID_of_Function"]。

        7.  **[Broad] 宽泛/创造性 (Creativity - Y Document)**
            -   **目标**: 忽略具体应用场景，寻找核心特征在其他领域的应用。
            -   **逻辑**: 仅保留 "Block B (KeyFeature)"，并强制结合 "IPC" 以防噪音。
            -   **组件**: ["ID_of_KeyFeature", "IPC"]。
            
        8.  **[Conflicting_E] 抵触申请排查 (E类)**  <--- **[Critical]**
            -   **目标**: 寻找 **申请在先、公开在后** 的潜在破坏新颖性的专利。
            -   **逻辑**: 仅使用最核心的 "Block B (KeyFeature)"，**严禁加 IPC 限制** (防止漏检)，**不加 Block A** (除非 Block B 极为常见)。
            -   **组件**: ["ID_of_KeyFeature"]。
            -   **说明**: 这是一个时间维度的特殊打击，关键词要尽量精简（只查最核心特征）。日期过滤由执行层处理，这里只需列出核心词。

        9.  **[Fundamental] 基础原理/学术溯源 (Academic/NPL)**
            -   **目标**: 针对算法、材料或机理进行去专利化的学术搜索。
            -   **逻辑**: 
                - 若 Block B 是算法/材料: 仅搜索 "Block B"。
                - 若 Block B 是通用结构: 搜索 "Block A + Block B" (自然语言)。
            -   **适用源**: Academic (CNKI/Scholar)。

        ### 输出约束 (Strict Constraints)
        1.  **JSON List Only**: 直接返回 JSON 列表，严禁 Markdown 格式，严禁废话。
        2.  **ID Validation**: 
            - `components` 列表中的值必须是以下两类之一：
              a) **Reserved Keywords**: "Assignee", "Inventor", "IPC"
              b) **Concept IDs**: 必须严格匹配【检索要素清单】中方括号内的 ID (如 `UAV_System`, `Folding_Wing`)。
        3.  **Field Definition**:
            -   `name`: 策略名称 (简短描述)。
            -   `intent`: [Trace, Competitor, Precision, Synergy, Functional, Component, Broad, Conflicting_E, Fundamental]。
            -   `proximity`: [None, SameSentence, SameParagraph, BooleanAND, NaturalLanguage]。
            -   `components`: [ID1, ID2, ...] (积木ID或保留字列表)。
            -   `target_source`: [Patent, Academic, All]。

        ### JSON 输出示例
        [
            {
                "name": "华为-IPC联合降噪追踪",
                "intent": "Trace",
                "components": ["Assignee", "IPC"],
                "proximity": "BooleanAND",
                "target_source": "Patent"
            },
            {
                "name": "E类-折叠翼核心排查",
                "intent": "Conflicting_E",
                "components": ["Folding_Wing_Pivot"],
                "proximity": "SameSentence",
                "target_source": "Patent"
            }
        ]
        """

        response = self.llm_service.chat_completion_json(
            model=settings.LLM_MODEL_REASONING,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context},
            ],
        )
        return response if isinstance(response, list) else []

    def _llm_generate_diff_plan(self, context: str) -> List[Dict]:
        """Step 2.2: 生成针对 Diff 的 Y 类计划"""
        system_prompt = """
        你是一位精通**创造性审查 (Inventive Step)** 的专利检索专家。
        
        ### 核心任务
        基于提供的【区别特征】，制定寻找 **D2 (副引证)** 的检索策略。

        ### 策略模式 (Strategy Patterns)
        1.  **[Problem-Solution] 功能/问题导向**:
            -   **逻辑**: 区别特征是为了解决什么技术问题？搜索 "区别特征 + 技术问题/效果"。
            -   **目的**: 证明该特征是本领域的常规技术手段。
            -   **算符**: 必须使用 `p` (Same Paragraph) 或 `AND`。
        
        2.  **[Cross-Field] 跨领域应用**:
            -   **逻辑**: 该特征是否在其他领域（非本专利领域）是通用的？搜索 "区别特征" (不加本领域限定)。
            -   **目的**: 证明是转用发明。
            
        3.  **[Structure] 结构特征补全**:
            -   **逻辑**: 仅针对该具体结构进行精准检索。搜索 "区别特征 + 结构限定词"。

        ### 输出格式 (JSON List Only)
        请严格输出 JSON 列表，格式同通用计划：
        [
          {
            "name": "D2-区别特征-问题导向",
            "intent": "Inventive_Step_Y",
            "proximity": "SameParagraph",
            "components": ["Diff_Feature_Keywords", "Technical_Problem_Keywords"],
            "target_source": "Patent"
          }
        ]
        注意：components 中的关键词请直接提供英文/中文检索词建议，而不是 ID。
        """
        
        response = self.llm_service.chat_completion_json(
            model=settings.LLM_MODEL_REASONING,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context},
            ],
        )
        return response if isinstance(response, list) else []

    def _llm_translate_to_patsnap(self, context: str) -> List[Dict]:
        """Step 2.4: 翻译为 Patsnap 语法"""
        logger.info("[SearchAgent] Translating Plan to Patsnap (Zhihuiya) Syntax...")

        system_prompt = """
        你是一位 **智慧芽 (Patsnap) 数据库高级检索专家**。
        你的任务是将【通用检索计划】翻译为 **Patsnap 命令行检索式**。

        ### 0. 预处理规则 (Pre-processing Rules) - 必须执行！
        在生成检索式前，对关键词进行清洗：
        1.  **英文截词 (English Stemming)**:
            -   **规则**: 将所有英文实词转为词根 + `*`。
            -   *示例*: `detecting` -> `detect*`, `batteries` -> `batter*`.
            -   *例外*: 3个字母以下的短词 (e.g., `fan`, `bus`) 不截词；专有名词 (e.g., `Bluetooth`) 不截词。
        2.  **中文处理**: 中文**严禁**使用截词符 `*`。
        3.  **短语粉碎 (Phrase Smashing)**:
            -   **规则**: 严禁使用双引号 `""` 包裹非专有名词的词组。
            -   **操作**: 将 "Adjective Noun" 转换为 `(Adjective w/5 Noun)`。
            -   *理由*: 智慧芽的 `w/n` 算符比精确匹配更具鲁棒性。

        ### 1. 字段代码白名单 (Field Codes Whitelist)
        请仅使用以下字段代码，严禁编造：

        -   **TAC**: (Title, Abstract, Claims) -> **核心字段，最常用**。用于精准检索。
        -   **TTL**: (Title) -> 用于极高相关性检索。
        -   **DESC**: (Description) -> 仅用于功能/应用扩展，必须配合 `p` 算符。
        -   **ANS**: (Assignee Name Standardized) -> **标准化申请人** (优于 AN)。
        -   **IN**: (Inventor Name).
        -   **IPC**: (IPC Classification).

        ### 2. 算符映射体系 (Operator Mapping)
        
        -   **逻辑算符**: `AND`, `OR`, `NOT` (全大写)。
        -   **位置算符 (Proximity)**:
            -   `w/n` (Within n words): **词组拼接专用**。
                -   *场景*: Block B 内部修饰。
                -   *例*: `(variable w/3 resist*)` 代替 "variable resistor"。
            -   **s** (Same Sentence): **结构绑定专用**。
                -   *场景*: Block A (主语) 与 Block B (核心特征) 的结合。
                -   *例*: `(drone OR UAV) s (camera w/5 rotat*)`.
            -   **p** (Same Paragraph): **功能结合专用**。
                -   *场景*: 实体 与 功能/效果 的结合。
                -   *例*: `(graphene) p (heat w/3 dissipat*)`.

        ### 3. 意图与策略模版 (Intent-Strategy Templates)

        #### [Trace] 来源追踪
        -   **输入**: 使用 `Raw Assignees`。
        -   **策略**: 优先使用标准化申请人字段 `ANS`。
        -   **语法**: `ANS:(Huawei) OR IN:(Zhang San)`.
        -   **降噪**: 若需结合 IPC，则: `ANS:(Huawei) AND IPC:(H04W*)`.

        #### [Competitor] 竞争对手穿透
        -   **目标**: 某公司具体的某项技术。
        -   **语法**: `ANS:(Assignee_Name) AND TAC:((Block B_Terms))`.

        #### [Precision] 核心新颖性 (X类文献)
        -   **场景**: 查找完全相同的现有技术。
        -   **字段**: 必须限制在 **TAC** (题/摘/权) 以保证相关度。
        -   **语法**: `TAC:( (Block A_Terms) s (Block B_Terms) )`.
        -   *注意*: 使用 `s` 算符确保特征在同一句话中描述。

        #### [Functional] 功能泛化 / [Component] 组件应用 / [Inventive_Step_Y]
        -   **场景**: 在全文中寻找某一功能或组件的应用，或者寻找 D2 (技术启示)。
        -   **字段**: 放宽至全文 (通常不加字段前缀即全文，或显式用 `DESC`)。
        -   **语法**: `DESC:( (Feature_A_Terms) p (Feature_B_Terms) )`.
        -   *注意*: 功能描述往往较长，必须使用 `p` (同段)。

        #### [Broad] 跨领域扩展
        -   **场景**: 核心结构 + 分类号。
        -   **语法**: `TAC:((Block B_Terms)) AND IPC:(Self_IPC)`.
        
        #### [Conflicting_E] 抵触申请
        -   **场景**: 仅针对核心特征进行宽泛检索，**不需要**你在 Query 中写日期限制（Execution Hand 会自动处理日期）。
        -   **字段**: 必须使用 **TAC**。
        -   **语法**: `TAC:((Block B_Pivot_Terms))`.
        -   **注意**: 关键词要准，算符要紧 (SameSentence)，不要加多余的限定词。

        ### 4. 输出示例 (JSON List Only)
        [
            {
                "name": "Patsnap精准-折叠翼",
                "db": "Patsnap",
                "intent": "Precision",
                "query": "TAC:( (UAV OR drone) s (fold* w/5 wing*) )"
            },
            {
                "name": "Patsnap功能-散热",
                "db": "Patsnap",
                "intent": "Functional",
                "query": "DESC:( (UAV OR drone) p (heat w/3 dissipat*) )"
            }
        ]
        """

        response = self.llm_service.chat_completion_json(
            model=settings.LLM_MODEL_REASONING,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context},
            ],
        )
        return response if isinstance(response, list) else []

    def _clean_for_search_query(self, text: str) -> str:
        """
        辅助函数：清洗用于语义检索的文本
        """
        if not text:
            return ""
        text = re.sub(r"\*\*(.+?)\*\*\s*\[\d+\]", r"\1", text)
        return text.strip()

    def _get_semantic_strategy(self) -> Dict:
        # 1. 获取技术手段文本
        tech_means = self.report_data.get("technical_means", "")

        if not tech_means:
            # 降级策略
            tech_means = self.report_data.get(
                "technical_scheme"
            ) or self.report_data.get("ai_abstract", "")

        # 清洗 markdown 标记
        clean_query = self._clean_for_search_query(tech_means)

        # 2. 构造语义检索节点结构
        return {
            "name": "技术手段语义检索",
            "intent": "Broad",
            "db": "Smart Search",
            "query": clean_query,
            "status": "pending"
        }