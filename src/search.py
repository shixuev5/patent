import re
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
        主流程入口：只生成语义匹配相关的内容
        """
        logger.info("开始构建语义匹配策略...")

        # 构建检索要素表
        def _execute_phase1():
            matrix_context = self._build_matrix_context()
            return self._build_search_matrix(matrix_context)

        search_matrix = self.cache.run_step("step1_matrix", _execute_phase1)
        if not search_matrix:
            logger.warning("Search Matrix 为空，策略生成将受限。")
            search_matrix = []

        # 语义检索
        semantic_strategy = self.cache.run_step(
            "step2_semantic", self._build_semantic_strategy
        )

        # 合并结果
        return {
            "search_matrix": search_matrix,
            "semantic_strategy": semantic_strategy,
        }

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

        # --- 基于 TCS 评分的精确逻辑 ---
        for feat_name, info in feature_details.items():
            score = feature_max_scores.get(feat_name, 0)
            desc = info.get("description", "无描述").replace("\n", " ").strip()
            raw_rationale = info.get("rationale", "").replace("\n", " ").strip()
            rationale = (
                (raw_rationale[:150] + "...")
                if len(raw_rationale) > 150
                else raw_rationale
            )

            # 格式: [名称] - [描述] (TCS Score: X)
            if score >= 3:
                entry = (
                    f"- 【{feat_name}】 (TCS: {score})\n"
                    f"    定义: {desc}\n"
                    f"    原理: {rationale}"
                )
            else:
                continue

            if score >= 5:
                block_b_content.append(entry)
            elif score >= 3:
                block_c_content.append(entry)

        # 4. 提取效果描述 (用于 Block C 的补充)
        effects_summary = []
        for e in report.get("technical_effects", []):
            score = e.get("tcs_score", 0)
            if score >= 3:  # 只关注有价值的效果
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

    def _generate_semantic_query(self, raw_text: str) -> str:
        """
        通过独立的 LLM 调用，将原始技术交底内容重写为高密度的向量检索 Query
        """
        logger.info("[SearchAgent] Step 2: Generating Semantic Search Query via LLM...")

        if not raw_text.strip():
            return ""

        system_prompt = """
        你是一位专门优化专利向量检索（Dense Retrieval / Embedding）质量的 AI 专家。
        你的输入是一段【经过专家深度剖析的技术机理文本】（包含人为加入的引用标号、分析标签和叙述性过渡语）。
        你的任务是将这段文本“降噪”并“压实”为一段【极高信息密度的纯粹语义检索 Query】，最大化 Embedding 模型对核心技术特征的注意力权重。

        ### 处理规则（严格遵守）：
        1. **符号降噪（必须执行）**：彻底清除所有的 Markdown 格式（如 `**`）、引用标号（如 `[1]`）以及分析标签（如 `(★区别特征)`）。
        2. **剥离叙述性过渡语**：直接陈述技术事实！强行删掉原文中的背景铺垫、主观评价和过渡套话（如“针对...的【核心问题】”、“本发明并未采用...而是引入了”、“其核心在于”、“从物理学角度看”等）。
        3. **无损保留机理（IPO逻辑）**：绝对不能删减原文中的“物理/数学/算法机制”、“结构协同关系”以及“解决的具体问题”。向量模型极度依赖这些机理词汇来计算相似度。
        4. **语言风格**：必须是连贯紧凑的客观陈述句。严禁使用第一人称（“本发明”），严禁输出为列表格式。
        5. **字数控制**：浓缩至 100 - 150 字左右，确保每一段 Token 都是纯粹的“技术干货”。

        ### 示例对比（请深刻体会“剥离过渡语”的含义）：
        *   **原始输入**: "针对早期轴承故障信号极易被背景噪声淹没的【核心问题】，本发明并未采用传统的时域阈值判定，而是引入了 **自适应共振解调算法** [3](★区别特征)。从信息论角度看，该算法利用 **包络检波器** [4] 将高频载波中的低频故障冲击特征进行非线性映射，配合 **多级带通滤波器** [5] 的级联作用，成功将微弱的微伏级故障特征从强干扰背景中剥离，实现了对早期微裂纹的精准捕捉。"
        *   **输出 (JSON)**: 
        {
            "semantic_query": "一种基于自适应共振解调算法的轴承故障检测技术。利用包络检波器将高频载波中的低频故障冲击特征进行非线性映射，并配合多级带通滤波器的级联作用，将微伏级微弱故障特征从强干扰背景噪声中剥离，实现早期微裂纹的精准捕捉。"
        }

        ### 输出格式：
        必须输出为纯 JSON 格式，包含唯一的键 `semantic_query`。严禁使用 Markdown 代码块 (如 ```json )，严禁包含任何解释性文字。
        """

        try:
            response = self.llm_service.chat_completion_json(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"原始输入文本：\n{raw_text}"},
                ],
                temperature=0.1
            )

            # 提取清洗后的文本
            if isinstance(response, dict) and "semantic_query" in response:
                return response["semantic_query"].strip()
            else:
                logger.warning(
                    "LLM 返回的 Semantic Query 格式不符合预期，回退到基础代码清理。"
                )
                return self._fallback_clean_text(raw_text)

        except Exception as e:
            logger.error(f"语义检索 Query 生成失败: {str(e)}")
            return self._fallback_clean_text(raw_text)

    def _fallback_clean_text(self, text: str) -> str:
        """
        降级方案：当 LLM 调用失败时使用的基础正则清理逻辑
        """
        text = re.sub(r"\(?[★☆][^\)]+\)?", "", text)  # 清理 (★区别特征)
        text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)  # 清理 **加粗**
        text = re.sub(r"\s*\[\d+[a-zA-Z]*\]", "", text)  # 清理 [1]
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def _build_semantic_strategy(self) -> Dict[str, Any]:
        """
        阶段二：构建语义检索策略节点
        """
        # 1. 获取原始核心技术文本
        raw_tech_text = self.report_data.get("technical_means", "")
        if not raw_tech_text:
            raw_tech_text = self.report_data.get(
                "technical_scheme", ""
            ) or self.report_data.get("ai_abstract", "")

        clean_query = self._generate_semantic_query(raw_tech_text)

        return {
            "name": "语义检索",
            "description": "基于核心技术手段的自然语言高密度提炼，用于快速召回 X 类/ Y 类文献。",
            "content": clean_query,
        }
