import re
from typing import Any, Dict, List, Optional
from loguru import logger
from agents.common.utils.llm import get_llm_service

VALID_ELEMENT_ROLES = {"Subject", "KeyFeature", "Functional"}
VALID_ELEMENT_TYPES = {
    "Product_Structure",
    "Method_Process",
    "Algorithm_Logic",
    "Material_Composition",
    "Parameter_Condition",
}
ELEMENT_TYPE_LOWER_MAP = {item.lower(): item for item in VALID_ELEMENT_TYPES}
VALID_TERM_FREQUENCY = {"low", "high"}
VALID_PRIORITY_TIERS = {"core", "assist", "filter"}


class SearchStrategyGenerator:
    def __init__(self, patent_data: Dict, report_data: Dict):
        self.llm_service = get_llm_service()
        self.patent_data = patent_data
        self.report_data = report_data

        self.base_ipcs = self.patent_data.get("bibliographic_data", {}).get(
            "ipc_classifications", []
        )

    def build_search_matrix(self) -> List[Dict[str, Any]]:
        matrix_context = self._build_matrix_context()
        search_matrix = self._build_search_matrix(matrix_context)
        if not search_matrix:
            logger.warning("检索要素矩阵为空，策略生成将受限。")
            return []
        return search_matrix

    def build_semantic_strategy(self) -> Dict[str, Any]:
        return self._build_semantic_strategy()

    def _build_matrix_context(self) -> str:
        """
        阶段一上下文：构建全维度的技术理解环境 (基于 TCS 评分分级)
        将评分结果映射为检索块 (Block A/B1..Bn/C)，指导检索策略生成。
        """
        biblio = self.patent_data.get("bibliographic_data", {})
        report = self.report_data
        cluster_bundle = self._build_effect_clusters()
        feature_details = cluster_bundle["feature_details"]
        feature_max_scores = cluster_bundle["feature_max_scores"]
        effect_clusters = cluster_bundle["effect_clusters"]
        hub_features = cluster_bundle["hub_features"]

        block_a_preamble: List[str] = []
        block_c_content: List[str] = []

        for feat_name, info in feature_details.items():
            claim_source = str(info.get("claim_source", "")).strip().lower()
            is_distinguishing = bool(info.get("is_distinguishing", False))
            score = feature_max_scores.get(feat_name, 0)
            desc = self._normalize_inline_text(info.get("description", "无描述"))
            raw_rationale = self._normalize_inline_text(info.get("rationale", ""))
            rationale = (
                (raw_rationale[:150] + "...") if len(raw_rationale) > 150 else raw_rationale
            )

            if claim_source == "independent" and (not is_distinguishing):
                block_a_preamble.append(f"- 【{feat_name}】: {desc}")

            if score in (3, 4):
                block_c_content.append(
                    f"- 【{feat_name}】 (TCS: {score})\n"
                    f"    定义: {desc}\n"
                    f"    原理: {rationale or '无'}"
                )

        block_b_sections: List[str] = []
        for cluster in effect_clusters:
            block_id = cluster["block_id"]
            effect_id = cluster["effect_cluster_id"]
            score = cluster["score"]
            effect_text = cluster["effect_text"]
            feature_lines: List[str] = []
            for feat_name in cluster["features"]:
                info = feature_details.get(feat_name, {})
                desc = self._normalize_inline_text(info.get("description", "无描述"))
                rationale = self._normalize_inline_text(info.get("rationale", ""))
                hub_mark = " [Hub]" if feat_name in hub_features else ""
                feature_lines.append(
                    f"  - 【{feat_name}】{hub_mark}\n"
                    f"      定义: {desc}\n"
                    f"      原理: {rationale or '无'}"
                )
            if not feature_lines:
                feature_lines = ["  - （未提供贡献特征，请从技术手段中抽取）"]
            block_b_sections.append(
                f"- [{block_id}/{effect_id}] Score {score} 效果: {effect_text}\n"
                f"{chr(10).join(feature_lines)}"
            )

        effects_summary = []
        for e in report.get("technical_effects", []):
            score = self._safe_int(e.get("tcs_score"), default=0)
            if score >= 3:
                effects_summary.append(f"- [Score {score}] {self._normalize_inline_text(e.get('effect', ''))}")

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

        === 2. Block B: 核心创新点 (Key Features - Vital Clusters) ===
        *** 核心效果按子块拆分：B1..Bn（每个子块对应一个核心效果） ***
        {chr(10).join(block_b_sections) if block_b_sections else "（未识别到核心效果子块）"}

        === 3. Block C: 功能与限定 (Functional - Enabler/Improver) ===
        *** TCS Score 3-4分特征 (作为限定条件或降噪词) ***
        {chr(10).join(block_c_content) if block_c_content else "（未识别到3-4分特征）"}

        [关键技术效果参考 (Effects)]
        {chr(10).join(effects_summary) if effects_summary else "（未识别到有效技术效果）"}

        [Hub特征提示]
        {", ".join(sorted(hub_features)) if hub_features else "无"}

        === 4. 补充上下文 (Technical Context) ===
        {report.get('technical_means', '未定义')[:1200]}
        """

    def _build_search_matrix(self, context: str) -> List[Dict]:
        """
        阶段一：基于专家策略构建检索要素表 (Search Matrix)
        """
        logger.info("基于 TCS 指导策略构建检索要素矩阵")

        system_prompt = """
        你是一位拥有 20 年实战经验的全球顶级专利检索专家（精通 CNIPA, EPO, USPTO 审查与检索逻辑）。
        你的任务是基于提供的【经过 TCS (技术贡献评分) 预处理的技术交底信息】，构建用于布尔检索的《检索要素矩阵 (search_strategy.v2)》。

        ### 核心提取策略：A + B1..Bn + C
        你必须按以下结构输出检索要素（总数建议 5-10）：
        1. **Block A (Subject)**：必须且仅有 1 个，作为技术领域锚点。
        2. **Block B 子块 (B1..Bn)**：每个 B_i 对应一个核心效果 E_i。来自核心效果贡献特征（优先 TCS 5分）。
        3. **Block C (Functional)**：用于降噪、后置交集筛选。

        ### 关键业务规则（必须遵守）
        1. **贡献特征全收录**：核心效果的贡献特征都应进入矩阵，不能遗漏。
        2. **Hub 特征允许复用**：同一特征贡献多个核心效果时，允许在多个 B_i 中重复出现，并标记 `is_hub_feature=true`。
        3. **避免过度 AND**：B_i 子块用于并行检索，不要把不同核心效果强行合并成一个“超长且全AND”的检索视角。
        4. **频率标签由你判断**：必须为每个要素输出 `term_frequency`：
           - `low`: 低频、特异性强，优先用于召回锚定。
           - `high`: 高频、泛化强，优先用于降噪限定。
        5. **优先级标签**：必须输出 `priority_tier`，合法值：
           - `core`: 核心破新颖性特征（通常 Block B）
           - `assist`: 关键使能/协同特征
           - `filter`: 场景或功能限定（通常 Block A/C）

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
        - 必须且只能输出一个 **JSON 数组 (List)**，每个元素必须包含：
          - `element_name`
          - `element_role` (`Subject|KeyFeature|Functional`)
          - `block_id` (`A|B1|B2|...|C`)
          - `effect_cluster_id` (`E1|E2|...`；非核心块可留空字符串)
          - `is_hub_feature` (`true|false`)
          - `term_frequency` (`low|high`)
          - `priority_tier` (`core|assist|filter`)
          - `element_type`
          - `keywords_zh`
          - `keywords_en`
          - `ipc_cpc_ref`
        - 绝对不要使用 Markdown 代码块（如 ```json），不要包含任何前言、后语或解释性文字。

        # 标准输出 Schema 示例：[
          {
            "element_name": "迷宫式密封环",
            "element_role": "KeyFeature",
            "block_id": "B1",
            "effect_cluster_id": "E1",
            "is_hub_feature": false,
            "term_frequency": "low",
            "priority_tier": "core",
            "element_type": "Product_Structure",
            "keywords_zh":["迷宫式密封", "曲折流道", "迂回通道", "密封环", "防漏件"],
            "keywords_en": ["labyrinth*", "tortuous path*", "seal* ring*", "leak* prevent*"],
            "ipc_cpc_ref":["F16J 15/44", "F16J 15/447"]
          }
        ]
        """

        response = self.llm_service.invoke_text_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context},
            ],
            task_kind="search_matrix_reasoning",
            temperature=0.2,
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

            raw_block_id = str(item.get("block_id") or "").strip().upper()
            block_id = self._normalize_block_id(raw_block_id, element_role)

            effect_cluster_id = str(item.get("effect_cluster_id") or "").strip().upper()
            if effect_cluster_id and not re.fullmatch(r"E\d+", effect_cluster_id):
                effect_cluster_id = ""
            if block_id.startswith("B") and not effect_cluster_id:
                effect_cluster_id = f"E{block_id[1:]}" if block_id[1:].isdigit() else ""
            if not block_id.startswith("B"):
                effect_cluster_id = ""

            term_frequency = str(item.get("term_frequency") or "").strip().lower()
            if term_frequency not in VALID_TERM_FREQUENCY:
                term_frequency = "low" if element_role == "KeyFeature" else "high"

            priority_tier = str(item.get("priority_tier") or "").strip().lower()
            if priority_tier not in VALID_PRIORITY_TIERS:
                if element_role == "KeyFeature":
                    priority_tier = "core"
                elif element_role == "Subject":
                    priority_tier = "filter"
                else:
                    priority_tier = "assist"

            normalized.append(
                {
                    "element_name": element_name,
                    "element_role": element_role,
                    "block_id": block_id,
                    "effect_cluster_id": effect_cluster_id,
                    "is_hub_feature": bool(item.get("is_hub_feature", False)),
                    "term_frequency": term_frequency,
                    "priority_tier": priority_tier,
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

    def _normalize_block_id(self, raw_block_id: str, element_role: str) -> str:
        if raw_block_id == "A":
            return "A"
        if raw_block_id == "C":
            return "C"
        if re.fullmatch(r"B\d+", raw_block_id):
            return raw_block_id
        if element_role == "Subject":
            return "A"
        if element_role == "KeyFeature":
            return "B1"
        return "C"

    def _safe_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except Exception:
            return default

    def _normalize_inline_text(self, value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def _build_effect_clusters(self) -> Dict[str, Any]:
        report = self.report_data or {}
        technical_features = report.get("technical_features", [])
        technical_effects = report.get("technical_effects", [])
        feature_details = {
            self._normalize_inline_text(item.get("name")): item
            for item in technical_features
            if isinstance(item, dict) and self._normalize_inline_text(item.get("name"))
        }
        feature_max_scores: Dict[str, int] = {}
        all_effects: List[Dict[str, Any]] = []
        for idx, effect in enumerate(technical_effects, start=1):
            if not isinstance(effect, dict):
                continue
            score = self._safe_int(effect.get("tcs_score"), default=0)
            effect_text = self._normalize_inline_text(effect.get("effect"))
            contributors_raw = effect.get("contributing_features") or []
            contributors: List[str] = []
            if isinstance(contributors_raw, list):
                for name in contributors_raw:
                    feat = self._normalize_inline_text(name)
                    if feat and feat not in contributors:
                        contributors.append(feat)
                        feature_max_scores[feat] = max(feature_max_scores.get(feat, 0), score)
            all_effects.append(
                {
                    "effect_index": idx,
                    "effect_text": effect_text or f"效果{idx}",
                    "score": score,
                    "features": contributors,
                }
            )

        core_effects = [e for e in all_effects if e["score"] >= 5]
        if not core_effects:
            max_score = max([e["score"] for e in all_effects], default=0)
            if max_score > 0:
                core_effects = [e for e in all_effects if e["score"] == max_score]
        if not core_effects:
            fallback_effect = {
                "effect_index": 1,
                "effect_text": self._normalize_inline_text(report.get("technical_problem")) or "核心效果",
                "score": 0,
                "features": [],
            }
            core_effects = [fallback_effect]

        core_effects = sorted(core_effects, key=lambda item: item["effect_index"])

        feature_occurrences: Dict[str, int] = {}
        for effect in core_effects:
            for feat in effect["features"]:
                feature_occurrences[feat] = feature_occurrences.get(feat, 0) + 1
        hub_features = {name for name, cnt in feature_occurrences.items() if cnt >= 2}

        effect_clusters: List[Dict[str, Any]] = []
        for i, effect in enumerate(core_effects, start=1):
            effect_clusters.append(
                {
                    "block_id": f"B{i}",
                    "effect_cluster_id": f"E{i}",
                    "effect_text": effect["effect_text"],
                    "score": effect["score"],
                    "features": effect["features"],
                }
            )

        return {
            "feature_details": feature_details,
            "feature_max_scores": feature_max_scores,
            "effect_clusters": effect_clusters,
            "hub_features": hub_features,
            "all_effects": all_effects,
        }

    def _build_semantic_cluster_text(self, cluster: Dict[str, Any], bundle: Dict[str, Any]) -> str:
        feature_details = bundle["feature_details"]
        hub_features = bundle["hub_features"]
        lines = [
            f"[{cluster['block_id']}/{cluster['effect_cluster_id']}] 核心效果: {cluster['effect_text']}",
            f"[评分] TCS={cluster['score']}",
        ]
        features = cluster.get("features", [])
        if features:
            lines.append("[贡献特征]")
            for feat in features:
                info = feature_details.get(feat, {})
                desc = self._normalize_inline_text(info.get("description", ""))
                rationale = self._normalize_inline_text(info.get("rationale", ""))
                hub_mark = " (Hub)" if feat in hub_features else ""
                line = f"- {feat}{hub_mark}"
                if desc:
                    line += f"；定义: {desc}"
                if rationale:
                    line += f"；机理: {rationale}"
                lines.append(line)
        else:
            lines.append("[贡献特征] 无")
        raw_tech_text = self._normalize_inline_text(
            self.report_data.get("technical_means")
            or self.report_data.get("technical_scheme")
            or self.report_data.get("ai_abstract")
            or ""
        )
        if raw_tech_text:
            lines.append("[补充技术上下文]")
            lines.append(raw_tech_text[:1000])
        return "\n".join(lines)

    def _generate_semantic_query(
        self,
        raw_text: str,
        *,
        block_id: Optional[str] = None,
        effect_cluster_id: Optional[str] = None,
        effect_text: Optional[str] = None,
    ) -> str:
        """
        通过独立的 LLM 调用，将原始技术交底内容重写为高密度的向量检索 Query
        """
        logger.info(f"调用 LLM 生成语义检索查询: {block_id or '-'} / {effect_cluster_id or '-'}")

        if not raw_text.strip():
            return ""

        system_prompt = """
        你是一位专门优化专利向量检索（Dense Retrieval / Embedding）质量的 AI 专家。
        你的输入是某一个核心效果子块（例如 B1/E1）的技术机理文本。
        你的任务是将文本“降噪并压实”为一段【极高信息密度的单一效果语义检索 Query】，最大化 Embedding 模型对该子块核心特征的注意力权重。
        注意：输出必须只服务于该核心效果，不能混入其他效果语义。

        ### 处理规则（严格遵守）：
        1. **符号降噪（必须执行）**：彻底清除所有的 Markdown 格式（如 `**`）、引用标号（如 `[1]`）以及分析标签（如 `(★区别特征)`）。
        2. **剥离叙述性过渡语**：直接陈述技术事实！强行删掉原文中的背景铺垫、主观评价和过渡套话（如“针对...的【核心问题】”、“本发明并未采用...而是引入了”、“其核心在于”、“从物理学角度看”等）。
        3. **无损保留机理（IPO逻辑）**：绝对不能删减原文中的“物理/数学/算法机制”、“结构协同关系”以及“解决的具体问题”。
        4. **语言风格**：必须是连贯紧凑的客观陈述句。严禁使用第一人称（“本发明”），严禁输出为列表格式。
        5. **专有词保留**：低频专有技术词（材料/器件/算法名）保持原词，不做上位替换。
        6. **字数控制**：浓缩至 100 - 180 字左右，确保每一段 Token 都是纯粹的“技术干货”。

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
            response = self.llm_service.invoke_text_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": (
                            f"子块: {block_id or 'B?'} / {effect_cluster_id or 'E?'}\n"
                            f"核心效果: {effect_text or '未提供'}\n"
                            f"原始输入文本：\n{raw_text}"
                        ),
                    },
                ],
                task_kind="semantic_query_rewrite",
                temperature=0.1,
            )

            # 提取清洗后的文本
            if isinstance(response, dict) and "semantic_query" in response:
                return response["semantic_query"].strip()
            else:
                logger.warning(
                    "LLM 返回的语义检索查询格式不符合预期，回退到基础代码清理。"
                )
                return self._fallback_clean_text(raw_text)

        except Exception as e:
            logger.error(f"语义检索查询生成失败: {str(e)}")
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
        bundle = self._build_effect_clusters()
        effect_clusters = bundle["effect_clusters"]

        queries: List[Dict[str, Any]] = []
        for cluster in effect_clusters:
            raw_text = self._build_semantic_cluster_text(cluster, bundle)
            clean_query = self._generate_semantic_query(
                raw_text,
                block_id=cluster["block_id"],
                effect_cluster_id=cluster["effect_cluster_id"],
                effect_text=cluster["effect_text"],
            )
            if not clean_query:
                continue
            queries.append(
                {
                    "query_id": cluster["block_id"],
                    "effect_cluster_id": cluster["effect_cluster_id"],
                    "effect": cluster["effect_text"],
                    "tcs_score": cluster["score"],
                    "content": clean_query,
                }
            )

        return {
            "name": "语义检索",
            "description": "按核心效果子块拆分生成语义查询，每段对应一个 Block B 子块。",
            "queries": queries,
        }
