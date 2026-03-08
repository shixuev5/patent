import re
from pathlib import Path
from typing import Dict, Any, List
from loguru import logger
from agents.common.utils.llm import get_llm_service
from agents.common.utils.cache import StepCache
from config import settings

VALID_ELEMENT_ROLES = {"Subject", "KeyFeature", "Functional"}
VALID_ELEMENT_TYPES = {
    "Product_Structure",
    "Method_Process",
    "Algorithm_Logic",
    "Material_Composition",
    "Parameter_Condition",
}
ELEMENT_TYPE_LOWER_MAP = {item.lower(): item for item in VALID_ELEMENT_TYPES}


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

        search_matrix = self.cache.run_step("step1_matrix_v2", _execute_phase1)
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
        block_a_preamble = [] # 前序/背景特征 (无视分数，用于圈定环境)
        block_b_content = []  # Score 5 (Vital)
        block_c_content = []  # Score 4 (Enabler) & 3 (Improver)

        # --- 基于 TCS 评分的精确逻辑（与独权前序特征提取合并为单循环） ---
        for feat_name, info in feature_details.items():
            claim_source = str(info.get("claim_source", "")).strip().lower()
            is_distinguishing = bool(info.get("is_distinguishing", False))
            score = feature_max_scores.get(feat_name, 0)
            desc = info.get("description", "无描述").replace("\n", " ").strip()
            raw_rationale = info.get("rationale", "").replace("\n", " ").strip()
            rationale = (
                (raw_rationale[:150] + "...")
                if len(raw_rationale) > 150
                else raw_rationale
            )

            # Block A: 独权前序特征（仅由 claim_source + is_distinguishing 判定）
            if claim_source == "independent" and (not is_distinguishing):
                block_a_preamble.append(f"- 【{feat_name}】: {desc}")

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

        === 1. Block A: 技术领域与前序公知环境 (Subject & Field) ===
        [检索主语 (核心产品/方法)]
        {report.get("claim_subject_matter", "未定义")}
        
        [所属领域 (用于锁定 IPC/CPC 大类)]
        {report.get("technical_field", "未定义")}
        
        [独权前序特征 (Preamble from Independent Claims)]
        {chr(10).join(block_a_preamble) if block_a_preamble else "（未识别到独权前序特征）"}

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
            "步骤 1/2：基于 TCS 指导策略构建检索要素矩阵"
        )

        system_prompt = """
        你是一位拥有 20 年实战经验的全球顶级专利检索专家（精通 CNIPA, EPO, USPTO 审查与检索逻辑）。
        你的任务是基于提供的【经过 TCS (技术贡献评分) 预处理的技术交底信息】，剥离冗余噪音，精准构建用于布尔逻辑检索的**《检索要素矩阵 (Search Matrix)》**。

        ### 核心提取策略：ABC 模块映射 (Total: 4-6 个要素)
        请严格根据上下文中各特征的 TCS 评分，提取并构建以下三个层级的检索要素：

        1. **Block A - 技术领域/保护主题 (Subject)**: 【必须且仅有 1 个】
           - **来源**：上下文的 [检索主语] 或 [所属领域]。
           - **标准**：这是检索的基本盘，用于划定应用场景（如“无人机”、“心脏起搏器”）。严禁提取“一种系统”或“方法”这种无意义的泛词。

        2. **Block B - 核心创新点 (KeyFeature)**: 【2-3 个，绝对最高优先级】
           - **来源**：必须从上下文的 [Block B (Score 5)] 中提取。
           - **标准**：这是破坏新颖性的致命特征！提炼出最核心的物理实体、关键算法或独创步骤。
           - **容错**：如果 Block B 为空，请从[Block C (Score 3-4)] 中提拔得分最高的特征作为 KeyFeature。

        3. **Block C - 功能/修饰/效果 (Functional)**: 【1-2 个】
           - **来源**：从[Block C (Score 3-4)] 或 [关键技术效果] 中提取。
           - **标准**：用于应对海量结果时的“降噪限定”或下位概念（如特定材料“聚氨酯”、特定参数“阈值”或核心效果“降噪”）。

        ### 属性定义与语境切换 (element_type)
        每个提取的要素必须被精准归入以下 5 类之一，这直接决定了你后续同义词扩展的方向：
        - `Product_Structure` (实体结构)：诱发结构件、装置、部件及其俗称的扩展（如 device, member, assembly）。
        - `Method_Process` (方法/动作)：诱发动名词、工艺步骤、制造过程的扩展（如 heating, controlling, etching）。
        - `Algorithm_Logic` (算法/逻辑)：诱发学术名词、标准协议、行业缩写扩展（如 CNN, FFT, 激活函数），绝对避免与硬件实体混淆。
        - `Material_Composition` (材料/组分)：诱发化学式、合金名、聚合物名、商品名扩展（如 PU, 聚四氟乙烯, 合金）。
        - `Parameter_Condition` (参数/限定)：诱发物理量、比值、阈值、范围词的扩展（如 ratio, threshold, 介电常数）。

        ### 检索词扩展铁律 (Expansion Rules) - 【极其重要】
        你输出的 `keywords_zh` 和 `keywords_en` 将直接送入搜索引擎，必须遵守以下铁律：
        1. **中文扩展 (CN)**：
           - 必须包含：学术法定词 + 行业俗称 + 上下位概念（例如：`["移动终端", "智能手机", "手机", "移动设备"]`）。
           - 结构功能互换：如果是弹簧，必须扩展 `["弹簧", "弹性件", "偏置件", "复位件"]`。
        2. **英文扩展 (EN)**：
           - 采用 Patentese 法律英语（如 `plurality of`, `configured to`, `means for`）。
           - **强制使用截词符**（用 `*` 或 `+` 或 `?`），例如 `sensor*`, `configur+`, `encrypt?`。
           - 词根多形态覆盖：动名词/名词变体（例如 `mix*` 涵盖 mixing, mixer, mixture）。
        3. **【防灾性负向约束】**：
           - **严禁**在数组的单一字符串内包含布尔逻辑词（禁止输出 `"手机 OR 终端"`，必须输出 `["手机", "终端"]`）。
           - **严禁**输出毫无独立检索意义的短语（禁止输出 `"该方法包括"`, `"通过...连接"`）。
           - **严禁**将一长串句子作为关键词，关键词必须是“词”或“词组”。

        ### 分类号分配原则 (ipc_cpc_ref)
        - Block A 优先分配【应用类 IPC】（如车辆 B60）。Block B/C 优先分配【功能类 IPC】（如数据处理 G06F）。
        - **格式严控**：必须符合国际标准 `[部类][大类][小类][大组]/[小组]`（如 `H04W 72/04`，必须有大写字母且中间必须有且仅有一个空格）。
        - 精度要求：只输出具有强相关性的 1-3 个分类号，如果拿不准小组，保留到大组级别（如 `H04W 72/00`）。

        ### 输出格式要求
        - 必须且只能输出一个 **JSON 数组 (List)**。
        - 绝对不要使用 Markdown 代码块（如 ```json），不要包含任何前言、后语或解释性文字。

        # 标准输出 Schema 示例：[
          {
            "element_name": "迷宫式密封环",
            "element_role": "KeyFeature",
            "element_type": "Product_Structure",
            "keywords_zh":["迷宫式密封", "曲折流道", "迂回通道", "密封环", "防漏件"],
            "keywords_en": ["labyrinth*", "tortuous path*", "seal* ring*", "leak* prevent*"],
            "ipc_cpc_ref":["F16J 15/44", "F16J 15/447"]
          }
        ]
        """

        response = self.llm_service.chat_completion_json(
            model=settings.LLM_MODEL_REASONING,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context},
            ],
            temperature=0.2
        )

        raw_matrix = response if isinstance(response, list) else []
        return self._normalize_search_matrix(raw_matrix)

    def _normalize_search_matrix(self, raw_matrix: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []

        for item in raw_matrix:
            if not isinstance(item, dict):
                continue

            element_name = str(item.get("element_name") or "").strip()
            if not element_name:
                continue

            raw_role = str(item.get("element_role") or "").strip()
            element_role = raw_role if raw_role in VALID_ELEMENT_ROLES else "Functional"

            raw_type = str(item.get("element_type") or "").strip()
            if raw_type in VALID_ELEMENT_TYPES:
                element_type = raw_type
            else:
                element_type = ELEMENT_TYPE_LOWER_MAP.get(
                    raw_type.lower(), "Product_Structure"
                )

            normalized.append(
                {
                    "element_name": element_name,
                    "element_role": element_role,
                    "element_type": element_type,
                    "keywords_zh": self._normalize_keyword_list(item.get("keywords_zh") or []),
                    "keywords_en": self._normalize_keyword_list(item.get("keywords_en") or []),
                    "ipc_cpc_ref": self._normalize_keyword_list(item.get("ipc_cpc_ref") or []),
                }
            )

        return normalized

    def _normalize_keyword_list(self, value: Any) -> List[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []

        normalized: List[str] = []
        for item in value:
            text = str(item).strip()
            if not text or text in normalized:
                continue
            normalized.append(text)
        return normalized

    def _generate_semantic_query(self, raw_text: str) -> str:
        """
        通过独立的 LLM 调用，将原始技术交底内容重写为高密度的向量检索 Query
        """
        logger.info("步骤 2/2：调用 LLM 生成语义检索 Query")

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
