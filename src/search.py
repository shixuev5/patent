import re
import json
import concurrent.futures
from pathlib import Path
from typing import Dict, Any, List
from loguru import logger
from src.utils.llm import get_llm_service
from src.utils.cache import StepCache
from config import settings


class SearchStrategyGenerator:
    def __init__(self, patent_data: Dict, report_data: Dict, cache_file: Path = None):
        self.llm_service = get_llm_service()
        self.patent_data = patent_data
        self.report_data = report_data

        # 初始化缓存管理器
        self.cache = StepCache(cache_file) if cache_file else None

        self.base_ipcs = self.patent_data.get("bibliographic_data", {}).get(
            "ipc_classifications", []
        )

    def generate_strategy(self) -> Dict[str, Any]:
        """
        主流程入口
        Phase 1: 基础积木构建 (Matrix Builder)
        Phase 2: 全局策略规划 (Strategic Planner)
        Phase 3: 多库语法翻译 (Syntax Translator - Parallel)
        Phase 4: 全局注入与封装 (Finalizer)
        """
        logger.info("开始构建检索策略 (Plan-and-Execute Mode)...")

        # --- Phase 1: 检索要素表 (Search Matrix) ---
        def _execute_phase1():
            matrix_context = self._build_matrix_context()
            return self._build_search_matrix(matrix_context)

        search_matrix = self.cache.run_step("step1_matrix", _execute_phase1)
        if not search_matrix:
            logger.warning("Search Matrix 为空，策略生成将受限。")
            return {"search_matrix": [], "search_plan": {"strategies": []}}

        # --- Phase 2: 全局策略规划 (Planner) ---
        # 这一步只决定“查什么”和“逻辑组合方式”，不涉及具体数据库语法
        def _execute_phase2():
            planner_context = self._build_planner_context(search_matrix)
            return self._generate_universal_plan(planner_context)

        universal_plan = self.cache.run_step("step2_plan", _execute_phase2)

        # --- Phase 3: 多库语法翻译 (Translator) ---
        # 并行将通用计划翻译为 CNTXT, VEN, NPL 的具体查询语句
        translator_context = self._build_translator_context(
            search_matrix, universal_plan
        )

        # 定义任务映射
        tasks_map = {
            "step3_cntxt": self._translate_to_cntxt,
            "step3_ven": self._translate_to_ven,
            "step3_npl": self._translate_to_npl,
        }

        results_map = {}
        futures_map = {}

        # 使用 ThreadPoolExecutor 进行并行调用
        # max_workers=3 对应三个独立的数据库策略生成任务
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            for key, func in tasks_map.items():
                if self.cache.get(key):
                    logger.info(f"Step [{key}]: 跳过 (命中缓存)")
                    results_map[key] = self.cache.get(key)
                else:
                    logger.info(f"Step [{key}]: 提交翻译任务...")
                    # 提交任务
                    futures_map[executor.submit(func, translator_context)] = key

            # 2. 等待并行任务完成并保存缓存
            for future in concurrent.futures.as_completed(futures_map):
                key = futures_map[future]
                try:
                    res = future.result()
                    results_map[key] = res
                    # 线程安全地写入缓存
                    self.cache.save(key, res)
                except Exception as e:
                    logger.error(f"Step [{key}] failed: {e}")
                    results_map[key] = []

        # 合并所有策略
        combined_strategies = self._merge_strategies(
            results_map.get("step3_cntxt", []),
            results_map.get("step3_ven", []),
            results_map.get("step3_npl", []),
        )

        # 包装为最终对象
        strategy_plan = {"strategies": combined_strategies}

        # 1. 插入语义检索
        self._inject_semantic_strategy(strategy_plan)

        # 2. 注入时间限制
        self._inject_date_constraints(strategy_plan)

        # 合并结果
        return {
            "search_matrix": search_matrix,  # 对应你要求的中文、中文扩展、英文翻译
            "universal_plan": universal_plan,  # 可选：返回通用计划供调试
            "search_plan": strategy_plan,  # 具体的检索步骤和分析
        }

    def _merge_strategies(
        self, *strategy_lists: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        核心聚合函数：将散乱的策略按 Intent 聚类为四大战役。
        
        逻辑变更：
        1. Input: Translator 返回 {name="具体战术", intent="Trace", ...}
        2. Process: 
           - Map intent="Trace" -> Group="追踪检索"
           - Map name="具体战术" -> step="具体战术"
        3. Output: {name="追踪检索", queries=[{step="具体战术", ...}]}
        """
        # 1. 定义意图到分组的映射表 (Configuration)
        # Key: Intent (来自 Planner), Value: (Group Name, Group Description)
        INTENT_MAPPING = {
            # --- Group 1: 追踪检索 ---
            "Trace": ("追踪检索", "基于申请人/发明人的排查与背景追踪"),
            "Competitor": ("追踪检索", "基于申请人/发明人的排查与背景追踪"), # 竞争对手也归入追踪
            
            # --- Group 2: 精确检索 ---
            "Precision": ("精确检索", "Block A与Block B的结构化组合，锁定X类文献"),
            "Synergy": ("精确检索", "Block A与Block B的结构化组合，锁定X类文献"), # 协同复现通常也是为了查准
            
            # --- Group 3: 扩展检索 ---
            "Broad": ("扩展检索", "基于Block C的功能性或跨领域检索，寻找Y类文献"),
            "Functional": ("扩展检索", "基于Block C的功能性或跨领域检索，寻找Y类文献"),
            "Component": ("扩展检索", "基于Block C的功能性或跨领域检索，寻找Y类文献"),
            
            # --- Group 4: NPL 非专利 ---
            "Fundamental": ("NPL 非专利检索", "针对学术数据库的术语检索(去专利化)"),
            "NPL": ("NPL 非专利检索", "针对学术数据库的术语检索(去专利化)")
        }

        # 用于保持顺序的字典: {Group_Name: Group_Dict}
        grouped_strategies = {}
        
        # 定义分组的默认顺序 (确保输出 JSON 的顺序)
        default_order = ["追踪检索", "精确检索", "扩展检索", "NPL 非专利检索"]
        for group_name in default_order:
            # 预先查找描述（反向查找）
            desc = next((v[1] for k, v in INTENT_MAPPING.items() if v[0] == group_name), "")
            grouped_strategies[group_name] = {
                "name": group_name,
                "description": desc,
                "queries": []
            }

        # 2. 遍历合并
        for strategy_list in strategy_lists:
            for item in strategy_list:
                intent = item.get("intent", "Other")
                db_type = item.get("db", "")
                
                # 特殊处理：如果 DB 是学术库，强制归入 NPL (双重保险)
                if db_type in ["CNKI", "Google Scholar", "WanFang"]:
                    intent = "Fundamental"

                # 获取映射信息
                mapping = INTENT_MAPPING.get(intent)
                
                # 如果是未知的 Intent (如 Other)，归入扩展检索或新建其他
                if not mapping:
                    group_name = "其他检索"
                    group_desc = "补充策略"
                else:
                    group_name, group_desc = mapping

                # 初始化分组 (如果不在默认顺序中)
                if group_name not in grouped_strategies:
                    grouped_strategies[group_name] = {
                        "name": group_name,
                        "description": group_desc,
                        "queries": []
                    }

                # 3. 构造 Query Item (核心字段转换)
                # Translator.name -> Query.step
                query_item = {
                    "db": item.get("db"),
                    "step": item.get("name"),  # <--- 关键：将策略名转为步骤名
                    "query": item.get("query")
                }
                
                grouped_strategies[group_name]["queries"].append(query_item)

        # 4. 过滤掉没有 Query 的空组，并按顺序返回
        final_list = []
        # 先按默认顺序添加
        for g_name in default_order:
            if grouped_strategies[g_name]["queries"]:
                final_list.append(grouped_strategies[g_name])
                del grouped_strategies[g_name] # 处理完移除
        
        # 添加剩下的组 (如果有)
        for g_data in grouped_strategies.values():
            if g_data["queries"]:
                final_list.append(g_data)

        return final_list

    def _build_matrix_context(self) -> str:
        """
        阶段一上下文：构建全维度的技术理解环境 (基于 TCS 评分分级)
        将 Step 4 的评分结果直接映射为检索块 (Block B/C)，指导检索策略生成。
        """
        biblio = self.patent_data.get("bibliographic_data", {})
        report = self.report_data

        # 1. 建立特征详细信息查询表 (Feature Lookup Map)
        feature_details = {
            f.get("name", "").strip(): f for f in report.get("technical_features", [])
        }

        # 2. 基于 TCS 评分对特征进行桶排序 (Bucket Sort by Score)
        # 逻辑：一个特征可能贡献多个效果，取其最高得分作为定位依据
        feature_max_scores = {}  # {feature_name: max_score}

        for effect in report.get("technical_effects", []):
            score = effect.get("tcs_score", 0)
            contributors = effect.get("contributing_features", [])

            for feat_name in contributors:
                feat_name = str(feat_name).strip()
                current_max = feature_max_scores.get(feat_name, 0)
                if score > current_max:
                    feature_max_scores[feat_name] = score

        # 3. 构建分块内容列表
        block_b_content = []  # Score 5 (Vital)
        block_c_content = []  # Score 4 (Enabler) & 3 (Improver)
        block_a_candidates = []  # Score < 3 or Subject Matter

        # --- 基于 TCS 评分的精确逻辑 ---
        for feat_name, info in feature_details.items():
            score = feature_max_scores.get(feat_name, 0)
            desc = info.get("description", "无描述")

            # 格式: [名称] - [描述] (TCS Score: X)
            entry = f"- {feat_name}: {desc} (TCS Score: {score})"

            if score >= 5:
                block_b_content.append(entry)
            elif score >= 3:
                block_c_content.append(entry)
            else:
                block_a_candidates.append(entry)

        # 4. 提取效果描述 (用于 Block C 的补充)
        effects_summary = []
        for e in report.get("technical_effects", []):
            score = e.get("tcs_score", 0)
            if score >= 3:  # 只关注有价值的效果
                effects_summary.append(
                    f"- [Score {score}] {e.get('effect')} (源于: {e.get('contributing_features')})"
                )

        return f"""
        [发明名称] {biblio.get('invention_title')}
        [IPC参考] {', '.join(self.base_ipcs[:5])}

        === 1. Block A: 技术领域 (Subject) ===
        [保护主题 (检索主语)]
        {report.get("claim_subject_matter", "未定义")}
        
        [背景/通用特征 (仅作参考)]
        {chr(10).join(block_a_candidates[:5])}

        === 2. Block B: 核心创新点 (Key Features - Vital) ===
        *** TCS Score 5分特征 (必须作为核心检索词) ***
        {chr(10).join(block_b_content) if block_b_content else "（未识别到5分特征，请从技术手段中提取）"}

        === 3. Block C: 功能与限定 (Functional - Enabler/Improver) ===
        *** TCS Score 3-4分特征 (作为限定条件或降噪词) ***
        {chr(10).join(block_c_content)}

        [预期技术效果 (Effects)]
        {chr(10).join(effects_summary)}
        
        === 4. 整体技术手段综述 ===
        {report.get('technical_means', '未定义')}
        """

    def _build_search_matrix(self, context: str) -> List[Dict]:
        """
        阶段一：基于专家策略构建检索要素表 (Search Matrix)
        """
        logger.info(
            "[SearchAgent] Step 1: Building Search Matrix with TCS-Guided Strategy..."
        )

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
            "zh_expand": ["柔性屏幕", "柔性显示器", "可折叠屏", "柔性面板", "AMOLED"], 
            "en_expand": ["flex+ screen", "flex+ display", "foldable panel", "bendable display"],
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

        # 确保返回的是列表
        return response if isinstance(response, list) else []

    def _build_planner_context(self, matrix: List[Dict]) -> str:
        """
        阶段二上下文构建：为 Planner 提供结构化作战地图。

        核心改进：
        1. 显式划分 Block A/B/C 区域，明确组件角色。
        2. 结合 Report 数据引入 'Synergy Clusters' (协同效应) 和 'Pivot Features' (核心轴心)。
        3. 提供关键词预览，帮助 LLM 感知词汇粒度。
        """
        biblio = self.patent_data.get("bibliographic_data", {})
        report = self.report_data

        # --- 1. 建立映射索引 (Matrix Concept -> Metadata) ---
        # 方便后续校验 Feature 是否存在于 Matrix 中
        matrix_map = {item["concept_key"]: item for item in matrix}

        # --- 2. 积木逻辑分组 (Building Blocks Inventory) ---
        # 将平铺的 List 重组为有层级的 Dict，方便 LLM 理解角色
        grouped_blocks = {
            "Subject": [],  # Block A: 基础产品/领域
            "KeyFeature": [],  # Block B: 核心创新点
            "Functional": [],  # Block C: 效果/问题
            "Other": [],
        }

        for item in matrix:
            role = item.get("role", "Other")
            # 如果 role 标记不在预设范围内，归入 Other
            if role not in grouped_blocks:
                role = "Other"

            # 提取前3个关键词作为预览，帮助Planner判断词汇宽窄
            cn_preview = ",".join(item.get("zh_expand", [])[:3])
            en_preview = ",".join(item.get("en_expand", [])[:3])

            block_desc = (
                f"   - [ID: {item['concept_key']}] (Type: {item.get('feature_type', 'N/A')})\n"
                f"     Preview: {cn_preview} | {en_preview}"
            )
            grouped_blocks[role].append(block_desc)

        # 构建积木文本块
        inventory_text = []
        if grouped_blocks["Subject"]:
            inventory_text.append(
                f"=== Block A: Subject (基础主语/应用场景) ===\n"
                + "\n".join(grouped_blocks["Subject"])
            )
        if grouped_blocks["KeyFeature"]:
            inventory_text.append(
                f"=== Block B: KeyFeatures (核心创新结构/方法) ===\n"
                + "\n".join(grouped_blocks["KeyFeature"])
            )
        if grouped_blocks["Functional"]:
            inventory_text.append(
                f"=== Block C: Functional (技术效果/优化目标) ===\n"
                + "\n".join(grouped_blocks["Functional"])
            )

        # --- 3. 提取协同聚类 (Synergy Clusters) ---
        # 逻辑：从 Report 中读取“技术效果”，寻找绑定在一起的 >=2 个特征
        clusters_text = []
        for effect in report.get("technical_effects", []):
            contributors = effect.get("contributing_features", [])
            # 过滤：只保留在 Matrix 中存在的特征，防止幻觉
            valid_contributors = [f for f in contributors if f in matrix_map]

            if len(valid_contributors) >= 2:
                clusters_text.append(
                    f"- [Synergy Target]: {effect.get('effect')} (Score: {effect.get('tcs_score', 0)})\n"
                    f"  Bound Components: {json.dumps(valid_contributors, ensure_ascii=False)}\n"
                    f"  Rationale: {effect.get('rationale', '通过特定结构组合实现该效果')}"
                )

        # --- 4. 提取高分轴心 (Pivot Features) ---
        # 逻辑：提取 TCS Score >= 4 的特征，这些通常是检索的必备词
        high_score_features = []
        for feat in report.get("technical_features", []):
            name = feat.get("name")
            score = feat.get("tcs_score", 0)
            # 必须是 Matrix 里有的词，且分数够高
            if score >= 4 and name in matrix_map:
                high_score_features.append(f"{name} (Importance: {score}/5)")

        # --- 5. 组装最终上下文 ---
        applicants = ", ".join([a["name"] for a in biblio.get("applicants", [])][:3])
        inventors = ", ".join(biblio.get("inventors", [])[:3])

        return f"""
        【作战地图：检索要素清单 (Search Inventory)】
        {chr(10).join(inventory_text)}

        【核心策略输入 1: 协同聚类 (Synergy Clusters)】
        *指示：以下组件具有强因果关系，建议在"Synergy"策略中捆绑检索 (使用 P/S 算符)*
        {chr(10).join(clusters_text) if clusters_text else "暂无强聚类关系，请使用常规 Block A + Block B 组合。"}

        【核心策略输入 2: 高分轴心 (Pivot Features)】
        *指示：以下特征是发明的核心骨架，必须作为"Precision"或"Broad"策略的轴心词*
        Pivot Candidates: {', '.join(high_score_features) if high_score_features else "无高分特征，请均衡组合 Block B 中的要素。"}

        【案情背景 (Constraints)】
        - Base IPCs: {', '.join(self.base_ipcs[:3])}
        - Applicant: {applicants}
        - Inventor: {inventors}
        """

    def _generate_universal_plan(self, context: str) -> List[Dict]:
        """
        阶段二 Step 1: 生成通用检索计划 (Universal Search Plan)

        该函数制定战略层面的“作战意图”。
        """
        logger.info("[SearchAgent] Step 2.1: Planning Universal Strategy...")

        system_prompt = """
        你是一位 **高级专利检索策略专家 (Senior Search Strategist)**。
        你的任务是基于提供的【作战地图 (Search Inventory)】、【协同聚类】和【高分轴心】，制定一份覆盖全场景的检索计划。

        ### 核心任务：制定 8 类战术意图
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

        8.  **[Fundamental] 基础原理/学术溯源 (Academic/NPL)**
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
            -   `intent`: [Trace, Competitor, Precision, Synergy, Functional, Component, Broad, Fundamental]。
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
                "name": "本申请人-折叠翼自我抵触排查",
                "intent": "Competitor",
                "components": ["Assignee", "Folding_Wing_Pivot"],
                "proximity": "BooleanAND",
                "target_source": "Patent"
            },
            {
                "name": "无人机-散热功能泛化(去结构)",
                "intent": "Functional",
                "components": ["UAV_System", "Heat_Dissipation"],
                "proximity": "SameParagraph",
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

    def _build_translator_context(self, matrix: List[Dict], plan: List[Dict]) -> str:
        """
        阶段二 Step 2 (Context): 为翻译器构建详细的“词汇表”与“作战计划”。

        核心作用：
        1. 将抽象的 ID (如 'UAV_System') 关联到具体的扩展词 (如 '无人机, 无人驾驶飞行器...')。
        2. 提供 IPC 和 申请人信息，供 Trace/Broad 策略使用。
        """
        biblio = self.patent_data.get("bibliographic_data", {})

        # 1. 构建词汇表 (Vocabulary)
        # 格式: ID -> { CN: [...], EN: [...], IPC: [...] }
        vocabulary = []
        for item in matrix:
            # 过滤掉过短的词或无意义词，保证质量
            zh_keywords = [w for w in item.get("zh_expand", []) if len(w) > 1]
            en_keywords = [w for w in item.get("en_expand", []) if len(w) > 2]

            vocab_entry = (
                f"ID: [{item['concept_key']}]\n"
                f"  - CN_Keywords: {', '.join(zh_keywords[:8])} ...\n"  # 限制数量防Token溢出
                f"  - EN_Keywords: {', '.join(en_keywords[:8])} ...\n"
                # 关键修复：提供特征自身的分类号，而非专利的主分类号，用于扩展检索
                f"  - Self_IPC (For Broad Search): {', '.join(item.get('ipc_cpc_ref', []))}"
            )
            vocabulary.append(vocab_entry)

        # 2. 序列化通用计划
        # 在这里执行 ID 校验，防止 LLM 幻觉
        validated_plan = self._validate_plan_ids(plan, matrix)
        plan_str = json.dumps(validated_plan, ensure_ascii=False, indent=2)

        # 3. 提取背景约束 (传入原始数据)
        # 关键修复：传入 Raw Data，让 Prompt 处理多样性
        raw_applicants = [a["name"] for a in biblio.get("applicants", [])]
        raw_inventors = biblio.get("inventors", [])

        constraints = (
            f"- Base IPCs (Patent's Field): {', '.join(self.base_ipcs[:3])}\n"
            f"- Raw Assignees: {json.dumps(raw_applicants, ensure_ascii=False)}\n"
            f"- Raw Inventors: {json.dumps(raw_inventors, ensure_ascii=False)}"
        )

        return f"""
        === 1. 词汇映射表 (Vocabulary Map) ===
        *Translator 请注意：将 Plan 中的 Component ID 替换为这里的 Keywords*
        {chr(10).join(vocabulary)}

        === 2. 背景约束数据 (Constraints Data) ===
        *用于 Trace (申请人) 策略*
        {constraints}

        === 3. 通用检索计划 (Universal Plan) ===
        *请将以下每一条 Plan 翻译为目标数据库的查询语句*
        {plan_str}
        """
        
    def _validate_plan_ids(self, plan: List[Dict], matrix: List[Dict]) -> List[Dict]:
        """
        辅助函数：防止 Planner 编造不存在的 ID (Hallucination Check)
        """
        valid_ids = {item["concept_key"] for item in matrix}
        # 添加保留字 (Reserved Keywords)
        valid_ids.add("Assignee")
        valid_ids.add("Inventor")
        valid_ids.add("IPC")
        
        cleaned_plan = []
        for strategy in plan:
            components = strategy.get("components", [])
            # 过滤掉不存在的 ID
            valid_components = [cid for cid in components if cid in valid_ids]
            
            # 如果过滤后组件为空（且不是 Trace 策略），则丢弃该策略
            if not valid_components and strategy.get("intent") != "Trace":
                logger.warning(f"Strategy '{strategy.get('name')}' dropped due to invalid IDs: {components}")
                continue
                
            strategy["components"] = valid_components
            cleaned_plan.append(strategy)
            
        return cleaned_plan

    def _translate_to_cntxt(self, context: str) -> List[Dict]:
        """
        阶段二 Step 2.1 (Translation): 将通用计划翻译为 CNTXT 中文检索式。
        """
        logger.info("[SearchAgent] Translating Plan to CNTXT syntax...")

        system_prompt = """
        你是一位 **CNIPA 专利检索专家**。
        你的任务是将输入的【通用检索计划 (Universal Plan)】结合【词汇映射表】，翻译为 **CNTXT (中国专利全文数据库)** 的标准检索式。
        
        ### 0. 过滤原则 (Filtering Rule)
        -   **忽略** 任何 `intent` 为 **"Fundamental"** 的策略（因为它们针对 Academic 数据库）。
        -   仅处理针对 Patent 的策略 (Trace, Precision, Synergy, Functional, Component, Broad, Competitor)。

        ### 1. CNTXT 语法铁律 (Syntax Iron Rules)
        必须严格遵守以下规则，任何违反都将导致检索失败：
        
        1. **逻辑算符**: 仅使用 `AND`, `OR`, `NOT`。**严禁**使用 `+`, `-`。
        
        2. **位置算符 (核心武器)**:
           - `S` (同句): **最强位置限定**。必须用于 **Block A (主语) 与 Block B (结构类特征)** 的组合。例: `(活塞 S 螺旋槽)`。
           - `nD` (词距): 灵活限定。用于 **Block B 中较长的修饰语** 内部组装。例: `(神经网络 5D 训练)`。
           - `P` (同段): 宽松限定。用于 **Block A (主语) 与 Block B/C (不同组件/功能)** 的组合。
           - **TXT字段禁令**: **禁止**在全文(`/TXT`)中直接使用 `AND` 连接通用技术词，必须用 `P` 或 `S`。
           
        3. **字段**: `/TI` (名称), `/AB` (摘要), `/CLMS` (权利要求), `/TXT` (全文), `/PA` (申请人), `/IN` (发明人)。
        
        4. **长复合词拆解策略 (Compound Splitting)**:
            - 当 Block A 或 Block B 中出现长复合名词（如“谐波减速器柔性轴承试验台”，长度>4），**严禁直接全词检索**。
            - 必须将其拆解为 **[修饰词]**、**[核心名词]**、**[功能/后缀]**。
            - **操作步骤**: 先用 `nD` 将 [修饰词] 与 [核心名词] 紧密连接，再用 `S` 将上述组合与 [功能/后缀] 连接。
            - *示例*: 词表中是 "柔性显示面板" -> 翻译为 `((柔性 OR 柔性化) 5D (显示面板 OR 屏幕))`。
            - *示例*: 词表中是 "谐波减速器柔性轴承试验台" -> 翻译为 `((谐波 OR 柔性) 5D (轴承)) S (试验 OR 测试)`。

        ### 2. 意图与语法映射 (Intent -> Syntax)
        请根据 Plan 中的 `intent` 字段选择对应的翻译模板：

        -   **[Trace] 来源追踪**
            -   **输入**: 读取【背景约束】中的 `Raw Assignees`。
            -   **清洗**: 去除 "Co., Ltd", "有限公司", "股份公司" 等后缀，提取核心商号（如 "华为技术" -> "华为"）。
            -   **逻辑**: 
                - 若 `components` 包含 "IPC": `/PA=(核心商号) AND (Base IPCs 前4位)/IPC` (降噪模式)。
                - 否则: `/PA=(核心商号) OR /IN=(发明人)` (全量模式)。
        
        -   **[Competitor] 竞争对手穿透**
            -   **逻辑**: 锁定申请人在该技术点的布局。
            -   **语法**: `/PA=(核心商号) AND ((Block B 扩展关键词)/TI/AB/CLMS)`。
            -   *注意*: 仅在核心字段(TI/AB/CLMS)检索 Block B，以确保相关性。
        
        -   **[Precision] 核心新颖性 (Block A + B)**
            -   **逻辑**: 必须使用强位置限定。
            -   **语法**: `((Block A 扩展) S (Block B 扩展))/CLMS` (优先权利要求)。
            -   *备选*: 如果词汇较偏，可放宽到 `((Block A) S (Block B))/TXT`。
        
        -   **[Functional] 功能泛化 (Block A + C)**
            -   **逻辑**: 寻找实现相同功能(C)的主体(A)，**忽略**结构(B)。
            -   **语法**: `((Block A 扩展) P (Block C 扩展))/TXT`。
            -   *注意*: 使用 `P` (同段) 而非 `S`，因为功能描述通常跟在产品名称后面一段距离。
        
        -   **[Component] 组件跨界 (Block B + C)**
            -   **逻辑**: 寻找结构(B)实现功能(C)的其他应用，**忽略**原主体(A)。
            -   **语法**: `((Block B 扩展) S (Block C 扩展))/TXT`。
        
        -   **[Synergy] 方案复现 (Block A + B + C)**
            -   **语法**: `((Block A) P (Block B) P (Block C))/TXT`。
        
        -   **[Broad] 跨领域 (Block B + IPC)**
            -   **逻辑**: 核心结构(B) + 基础分类号限制(IPC)。
            -   **输入**: 读取【背景约束】中的 `Base IPCs` (取前4位，如 G06F)。
            -   **语法**: `((Block B 扩展)/TI/AB/CLMS) AND ((Base IPCs)/IPC)`。

        ### 3. 输出格式 (JSON List Only)
        [
            {
                "name": "策略名称",
                "db": "CNTXT",
                "intent": "Precision",
                "query": "((无人机 OR UAV) S ((折叠 3D 旋翼) S 枢轴))/CLMS"
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

    def _translate_to_ven(self, context: str) -> List[Dict]:
        """
        阶段二 Step 2.2 (Translation): 将通用计划翻译为 VEN (Global Patent Database) 英文检索式。
        """
        logger.info("[SearchAgent] Translating Plan to VEN (English)...")

        system_prompt = """
        你是一位 **EPO/USPTO 资深审查员**。
        你的任务是将【通用检索计划】结合【词汇映射表】，翻译为 **VEN (Global Patent Database)** 的标准英文检索式。

        ### 0. 过滤原则 (Filtering Rule)
        -   **忽略** 任何 `intent` 为 **"Fundamental"** 的策略。
        -   仅处理 Patent 相关策略。

        ### 1. VEN 语法铁律 (Syntax Iron Rules)
        必须严格遵守以下英文检索规范：

        1.  **截词 (Stemming)**: 
            -   所有英文实词词根后必须加 `+` 以覆盖复数、时态。
            -   *示例*: `detect` -> `detect+` (覆盖 detects, detected, detecting, detection)。
            -   *例外*: 专有名词或缩写 (如 UAV, OLED) 不加。

        2.  **短语与词距 (Phrases & Proximity)**:
            -   **严禁**直接拼接多个单词而不加算符。
            -   若关键词是词组 (如 "folding wing"):
                -   精确短语: 使用双引号 `"folding wing"`。
                -   灵活短语: 使用 `nD` (词距) 连接。 `(folding+ 2D wing+)`。
            
        3.  **算符映射 (Operators)**:
            -   `S` (Same Sentence): 用于结构特征结合。 `(piston+ S cylinder+)`.
            -   `P` (Same Paragraph): 用于功能/实施例结合。 `(wing+ P heat+ P dissipat+)`.
            -   `AND/OR/NOT`: 标准逻辑算符。

        4.  **字段映射 (Fields)**:
            -   `/TI` (Title), `/AB` (Abstract), `/CLM` (Claims), `/DESC` (Description/FullText).
            -   `/PA` (Assignee), `/IN` (Inventor).
            -   `/IPC`, `/CPC` (Classification).

        ### 2. 意图与逻辑映射 (Intent Logic)
        
        -   **[Trace] 来源追踪**
            -   **输入**: 读取 `Raw Assignees` / `Raw Inventors`。
            -   **拼音策略 (Pinyin Strategy)**: 
                -   若原名是中文，必须同时检索 **英文商号** AND **拼音**。
                -   拼音格式: `HUANG` OR `ZHANG` (姓), 以及全拼 `(ZHANG 1D SAN)`。
            -   **降噪**: 若 Plan 含 "IPC"，追加 `AND (Base IPCs 4-digits)/IPC`。
            -   **语法**: `/PA=(DJI OR (DA 1D JIANG) OR SZ_DJI)`.

        -   **[Competitor] 竞争对手穿透**
            -   **逻辑**: 申请人 + 核心特征(B) (在 TI/AB/CLM 中)。
            -   **语法**: `/PA=(Assignee_Name) AND ((Block B_Keywords)/TI/AB/CLM)`.

        -   **[Precision] 核心新颖性 (Block A + B)**
            -   **目标**: 权利要求中的结构结合。
            -   **语法**: `((Block A_Terms) S (Block B_Terms))/CLM`.

        -   **[Functional] 功能泛化 (Block A + C)**
            -   **逻辑**: **忽略 Block B**。寻找 主语(A) + 功能(C)。
            -   **语法**: `((Block A_Terms) P (Block C_Terms))/DESC`. (功能常在说明书中详述).

        -   **[Component] 组件跨界 (Block B + C)**
            -   **逻辑**: **忽略 Block A**。寻找 结构(B) + 功能(C)。
            -   **语法**: `((Block B_Terms) S (Block C_Terms))/DESC`. (结构与功能结合紧密).

        -   **[Synergy] 方案复现 (A + B + C)**
            -   **语法**: `((Block A) P (Block B) P (Block C))/DESC`.

        -   **[Broad] 跨领域 (Block B + IPC)**
            -   **逻辑**: Block B + **Self_IPC** (来自词汇表，非 Base IPC)。
            -   **语法**: `((Block B_Terms)/TI/AB/CLM) AND ((Self_IPC)/IPC)`.

        ### 3. 数据源引用 (Reference)
        -   必须使用【词汇映射表】中的 `EN_Keywords`。
        -   必须处理每一个 Keyword 的截词 (`+`)。

        ### 4. 输出格式 (JSON List Only)
        [
            {
                "name": "权利要求同句限定-折叠翼",
                "db": "VEN",
                "intent": "Precision",
                "query": "((UAV OR drone+) S ((fold+ 2D wing+) OR rotat+))/CLM"
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

    def _translate_to_npl(self, context: str) -> List[Dict]:
        """
        阶段二 Step 2.3 (Translation): 将通用计划翻译为 NPL (非专利文献) 检索式。
        """
        logger.info("[SearchAgent] Translating Plan to NPL (Academic)...")

        system_prompt = """
        你是一位 **学术文献检索专家 (Google Scholar / CNKI)**。
        你的任务是将【通用检索计划】调整并翻译为学术检索式。

        ### 1. 过滤与聚焦 (Filter & Focus)
        学术检索不关注法律状态或特定申请人，只关注技术原理。
        -   **处理 (Keep)**: 
            -   `[Fundamental]` (最重要，基础原理)
            -   `[Synergy]` (系统级复现)
            -   `[Functional]` (问题-方案型论文)
            -   `[Component]` (跨学科应用)
        -   **丢弃 (Drop)**: 
            -   `[Trace]` (申请人), `[Competitor]`, `[Precision]` (权利要求), `[Broad]` (依赖IPC)。

        ### 2. 核心任务：去专利化 (De-patentization)
        专利术语是为了模糊保护范围，学术术语是为了精确描述原理。你必须进行“翻译”：

        -   **词汇替换 (Vocabulary Swap)**:
            -   *Generic* -> *Specific*: "数据处理装置" -> "FPGA" / "DSP" / "GPU".
            -   *Functional* -> *Algorithmic*: "自适应调整步骤" -> "强化学习" / "PID控制" / "遗传算法".
            -   *Structure* -> *Mechanism*: "啮合部件" -> "齿轮传动机理" / "摩擦学特性".
        
        -   **句式简化 (Simplification)**:
            -   学术搜索引擎不支持复杂的嵌套逻辑 (S/P/nD)。
            -   **策略**: 提取 2-4 个最核心的实质性名词，用简单的 AND 组合。

        ### 3. 语法适配 (Syntax Adaptation)

        #### A. CNKI (中文学术)
        -   **字段**: 使用 `SU` (主题) 而非 `TI` (篇名)，因为 `SU` 包含关键词和摘要，召回率更好。
        -   **逻辑**: 使用 `*` 代表 AND, `+` 代表 OR。
        -   **格式**: `SU=('关键词1' + '同义词') * SU=('关键词2')`.
        -   *示例*: `SU=('视觉SLAM' + 'VSLAM') * SU=('闭环检测')`.

        #### B. Google Scholar (英文/国际)
        -   **核心指令**: 使用 `intitle:` 锁定 1 个最核心的词（通常是 Block B）。
        -   **逻辑**: 其他词使用自然语言组合，用空格隔开（默认 AND）。
        -   **短语**: 必须使用双引号 `""` 锁定专有名词。
        -   **格式**: `intitle:"Core_Term" "Secondary_Term" Function_Term`.
        -   *示例*: `intitle:"Visual SLAM" "Loop Closure" Optimization`.

        ### 4. 意图映射 (Intent Mapping)
        
        -   **[Fundamental] 基础原理**:
            -   CNKI: `SU=(Block B_Keywords)`.
            -   Scholar: `intitle:"Block B_Term"`.
        
        -   **[Functional] 功能泛化 (Block A + C)**:
            -   *场景*: "无人机(A)的散热(C)研究"。
            -   CNKI: `SU=(Block A) * SU=(Block C)`.
            -   Scholar: `"Block A" "Block C"`.
        
        -   **[Synergy] / [Component] 组合**:
            -   *场景*: "Block B 在 Block C 中的应用"。
            -   CNKI: `SU=(Block B) * SU=(Block C)`.
            -   Scholar: `intitle:"Block B" "Block C"`.

        ### 输出格式 (JSON List Only)
        为每个有效的 Plan 生成 **两条** 记录 (一条 CNKI，一条 Scholar)。
        [
            {
                "name": "CNKI题名检索-SLAM算法",
                "db": "CNKI",
                "intent": "Fundamental",
                "query": "SU=('视觉SLAM' + '视觉里程计') * SU=('特征点提取')"
            },
            {
                "name": "Scholar标题检索-SLAM",
                "db": "Google Scholar",
                "intent": "Fundamental",
                "query": "intitle:\"Visual SLAM\" \"Feature Extraction\""
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

    def _get_critical_date_clause(self, db_type: str) -> str:
        """
        计算查新截止日期 (Critical Date) 并根据数据库类型返回限制子句。
        逻辑：有优先权取优先权日，否则取申请日。
        """
        biblio = self.patent_data.get("bibliographic_data", {})

        # 1. 获取日期字符串 (格式假设为 YYYY.MM.DD)
        # 优先权日 > 申请日
        raw_date = biblio.get("priority_date") or biblio.get("application_date")

        if not raw_date:
            return ""

        # 2. 格式化日期 (移除点号，转为 YYYYMMDD)
        # 假设输入是 "2023.03.29" -> "20230329"
        clean_date = raw_date.replace(".", "").replace("-", "").strip()

        if not clean_date.isdigit() or len(clean_date) != 8:
            logger.warning(f"日期格式异常: {raw_date}，跳过时间限制注入")
            return ""

        # 3. 根据数据库类型生成不同的语法
        # 查新逻辑：检索 公开日(PD) 早于 截止日 的文献
        if db_type in ["CNTXT", "VEN", "CNIPA", "EPO"]:
            # 标准专利数据库语法
            return f" AND PD < {clean_date}"
        elif db_type in ["Google Scholar", "NPL"]:
            # 学术引擎通常不支持直接在 query 中精准写 AND PD<...
            # 这里可以选择不加，或者加一个备注，或者尝试通用语法
            return ""  # NPL 建议由用户在 UI 界面设置年份过滤器，Query 中不硬编码

        return ""

    def _inject_date_constraints(self, plan: Dict[str, List[Dict]]) -> None:
        """
        遍历生成的策略，向专利数据库的检索式中注入时间限制。
        """
        strategies = plan.get("strategies", [])

        for strategy in strategies:
            queries = strategy.get("queries", [])
            for q_item in queries:
                db_type = q_item.get("db", "")
                original_query = q_item.get("query", "")

                # 仅对非空 query 且是专利库的注入
                if original_query:
                    date_clause = self._get_critical_date_clause(db_type)
                    if date_clause:
                        q_item["query"] = f"({original_query}){date_clause}"

    def _clean_for_search_query(self, text: str) -> str:
        """
        辅助函数：清洗用于语义检索的文本
        """
        if not text:
            return ""

        text = re.sub(r"\*\*(.+?)\*\*\s*\[\d+\]", r"\1", text)

        return text.strip()

    def _inject_semantic_strategy(self, strategy_plan: Dict) -> None:
        # 1. 获取技术手段文本
        tech_means = self.report_data.get("technical_means", "")
        if not tech_means:
            # 降级策略：如果 technical_means 为空，使用 technical_scheme 或 abstract
            tech_means = self.report_data.get(
                "technical_scheme"
            ) or self.report_data.get("ai_abstract", "")

        # 清洗 markdown 标记
        clean_query = self._clean_for_search_query(tech_means)

        # 2. 构造语义检索节点结构
        semantic_strategy = {
            "name": "语义检索",
            "description": "基于核心技术手段的自然语言输入，用于智能检索系统快速圈定X类文献。",
            "queries": [
                {"db": "Smart Search", "step": "自然语言输入", "query": clean_query}
            ],
        }

        # 3. 插入到策略列表头部
        if isinstance(strategy_plan, dict):
            # 确保 strategies 键存在且为列表
            if "strategies" not in strategy_plan or not isinstance(
                strategy_plan["strategies"], list
            ):
                strategy_plan["strategies"] = []

            # 插入到索引 0
            strategy_plan["strategies"].insert(0, semantic_strategy)
        # 如果 strategy_plan 为空或异常，初始化它
        else:
            strategy_plan = {"strategies": [semantic_strategy]}
