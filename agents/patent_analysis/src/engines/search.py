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

    def build_execution_plan(self, search_matrix: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        阶段三：基于检索要素矩阵生成审查检索执行计划。
        """
        logger.info("基于检索要素矩阵的高阶属性生成布尔检索执行计划")
        if not isinstance(search_matrix, list) or not search_matrix:
            logger.warning("检索要素矩阵为空，无法生成执行计划")
            return []

        biblio = self.patent_data.get("bibliographic_data", {})
        applicants = biblio.get("applicants", [])
        inventors = biblio.get("inventors", [])

        applicant_names: List[str] = []
        if isinstance(applicants, list):
            for item in applicants:
                if isinstance(item, dict):
                    name = self._normalize_inline_text(item.get("name"))
                else:
                    name = self._normalize_inline_text(item)
                if name:
                    applicant_names.append(name)

        inventor_names: List[str] = []
        if isinstance(inventors, list):
            for item in inventors:
                name = self._normalize_inline_text(item)
                if name:
                    inventor_names.append(name)

        inventory_lines: List[str] = []
        b_blocks_found = set()
        for item in search_matrix:
            if not isinstance(item, dict):
                continue
            name = self._normalize_inline_text(item.get("element_name"))
            block = self._normalize_inline_text(item.get("block_id")).upper()
            freq = self._normalize_inline_text(item.get("term_frequency")).lower() or "low"
            tier = self._normalize_inline_text(item.get("priority_tier")).lower() or "core"
            if not name:
                continue
            inventory_lines.append(
                f"- ID: [{name}] | 归属: {block} | 词频: {freq} | 优先级: {tier}"
            )
            if block.startswith("B"):
                b_blocks_found.add(block)

        if not inventory_lines:
            logger.warning("检索要素矩阵缺少可用 element_name，无法生成执行计划")
            return []

        b_block_str = ", ".join(sorted(b_blocks_found)) if b_blocks_found else "B"

        context_str = f"""
        [案情基础信息]
        申请人：{", ".join(applicant_names) if applicant_names else "无"}
        发明人：{", ".join(inventor_names) if inventor_names else "无"}
        主要分类号：{", ".join(self.base_ipcs[:5])}
        核心突破点子块 (Block B): {b_block_str}

        [包含高阶属性的检索要素库 (请严格从中挑选要素名称)]
        {chr(10).join(inventory_lines)}
        """

        system_prompt = """
        你是一位极其严谨的资深专利审查检索专家。你的任务是基于用户提供的【案情基础信息】与【检索要素库】，制定一份布尔检索执行计划清单。

        ### 审查级检索铁律（绝对遵守）：
        1. **核心效果解耦（Block B 隔离原则）**：
           - **致命禁止**：绝对不允许将【案情基础信息】中列出的不同核心突破点子块（例如 B1 和 B2）的要素用 AND 连在同一个检索式中！这会导致召回降为 0。
           - **正确做法**：必须为每一个独立的核心突破点子块（如 B1、B2）分别独立设计检索步骤。
        2. **高低频要素的战术管控 (term_frequency & priority_tier)**：
           - `词频: low` 或 `优先级: core` 的要素是特异性极强的“破袭武器”，应作为穿透新颖性的首选检索项。
           - `词频: high` 或 `优先级: filter` 的要素非常泛化。**绝对禁止单独搜索它们**，它们只能在核心子块召回量巨大时，作为 Block C 追加到 AND 逻辑后方用于“降噪限定”。
        3. **执行条件的清晰判定 (condition)**：
           - 必须为每个步骤指明“在何种前提下执行”。例如“前置步骤命中超过 X 篇时执行”、“若步骤一未查到 X 类文献则无条件执行”。
        4. **Block C 的规范用法**：
           - **限定降噪**：[Block A/B 要素] AND [Block C 要素]（针对海量命中的收敛）。
           - **功能泛化替换**：丢弃 Block B 结构，仅用 [Block A] AND [Block C] 寻找不同结构但实现相同功能的对比文件。

        ### 任务要求与输出约束
        - 按检索计划完整输出全部必要步骤，不限制步骤数量。
        - 严格使用逻辑算符 (AND, OR) 将要素名称（加方括号）连接。
        - 只允许使用【检索要素库】中给出的要素名称。
        - 必须输出纯 JSON 数组，严禁包含 Markdown 格式块或多余文字。
        - 每个步骤必须包含以下字段：
          - `step_name`: 步骤名称（必须体现具体针对哪个 Block）。
          - `condition`: 执行判定条件。
          - `search_logic`: 布尔组配式（如 "[无人机] AND [迷宫式密封环]"）。
          - `rationale`: 设计该表达式的战术意图与审查逻辑依据。
          - `database`: "专利数据库" 或 "非专利学术库"。
        """

        try:
            response = self.llm_service.invoke_text_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": context_str},
                ],
                task_kind="execution_plan_generation",
                temperature=0.1,
            )
            return self._normalize_execution_plan(response)
        except Exception as exc:
            logger.error(f"执行计划生成失败: {exc}")
            return []

    def _build_matrix_context(self) -> str:
        """
        阶段一上下文：构建全维度的技术理解环境 (基于 TCS 评分分级)
        将评分结果映射为检索块 (Block A/B1..Bn/C)，指导布尔检索策略生成。
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
            desc = self._normalize_inline_text(info.get("description", "无特定描述"))
            raw_rationale = self._normalize_inline_text(info.get("rationale", ""))
            rationale = (
                (raw_rationale[:150] + "...") if len(raw_rationale) > 150 else raw_rationale
            )

            # 独权前序公知特征 -> 锁定场景
            if claim_source == "independent" and (not is_distinguishing):
                block_a_preamble.append(f"- 【{feat_name}】: {desc}")

            # 较高分但未进入核心效果的特征 -> 用于降噪/限定
            if score in (3, 4):
                block_c_content.append(
                    f"- 【{feat_name}】 (TCS: {score})\n"
                    f"    限定描述: {desc}\n"
                    f"    机理/功能: {rationale or '无'}"
                )

        block_b_sections: List[str] = []
        for cluster in effect_clusters:
            block_id = cluster["block_id"]
            effect_id = ",".join(cluster["effect_cluster_ids"])
            score = cluster["score"]
            effect_text = cluster["effect_text"]
            feature_lines: List[str] = []
            for feat_name in cluster["features"]:
                info = feature_details.get(feat_name, {})
                desc = self._normalize_inline_text(info.get("description", "无特定描述"))
                rationale = self._normalize_inline_text(info.get("rationale", ""))
                hub_mark = " [★跨效果 Hub 特征]" if feat_name in hub_features else ""
                feature_lines.append(
                    f"  - 【{feat_name}】{hub_mark}\n"
                    f"      结构/步骤细节: {desc}\n"
                    f"      技术机理: {rationale or '无'}"
                )
            if not feature_lines:
                feature_lines = ["  - （未显式提取到特征，需依靠常识或上下文推断）"]

            block_b_sections.append(
                f"-[{block_id}/{effect_id}] 核心效果 (Score {score}): {effect_text}\n"
                f"  贡献特征集合:\n{chr(10).join(feature_lines)}"
            )

        effects_summary = []
        for e in report.get("technical_effects", []):
            score = self._safe_int(e.get("tcs_score"), default=0)
            if score >= 3:
                effects_summary.append(f"- [Score {score}] {self._normalize_inline_text(e.get('effect', ''))}")
        
        bg_terms = []
        for bg in report.get("background_knowledge",[]):
            term = bg.get("term", "")
            if term:
                bg_terms.append(f"- {term}: {bg.get('definition', '')}")

        # 提供全局粗略范围，但限制字数避免冲淡核心特征
        tech_means_summary = self._normalize_inline_text(report.get("technical_means", "未定义"))[:600]

        return f"""
        [基础档案]
        发明名称: {biblio.get('invention_title', '未知')}
        初始IPC/CPC参考: {', '.join(self.base_ipcs[:5])}

        === 1. Block A: 技术领域与前序公知环境 (Subject & Field) ===
        [检索主语 (核心产品/方法)]
        {report.get("claim_subject_matter", "未定义")}
        [所属技术领域 (用于锁定大类)]
        {report.get("technical_field", "未定义")}[独权前序特征 (背景基准)]
        {chr(10).join(block_a_preamble) if block_a_preamble else "（无明显前序特征）"}

        === 2. Block B: 核心创新点 (Key Features - Vital Clusters) ===
        *** 必须将核心效果按子块严密拆分：B1..Bn。绝不能把不同效果对应的特征混杂在同一个B块内。 ***
        {chr(10).join(block_b_sections) if block_b_sections else "（未识别到核心效果子块）"}

        === 3. Block C: 功能与限定 (Functional - Enabler/Improver) ===
        *** TCS 3-4分特征 (作为实施例细化条件、降噪限定) ***
        {chr(10).join(block_c_content) if block_c_content else "（无补充限定特征）"}

        === 4. 补充上下文与技术问题 ===
        [待解决的技术问题] {report.get('technical_problem', '未定义')}
        [技术方案摘要] {tech_means_summary}...
        [背景公知术语 (现有技术，若提取为要素必须设为 high/filter)]
        {chr(10).join(bg_terms) if bg_terms else "（未提供背景术语）"}
        """

    def _build_search_matrix(self, context: str) -> List[Dict]:
        """
        阶段一：基于专家策略构建检索要素表 (Search Matrix)
        """
        logger.info("基于 TCS 指导策略构建检索要素矩阵")

        system_prompt = """
        你是一位拥有 20 年实战经验的全球顶级专利检索专家（精通 CNIPA, EPO, USPTO 审查逻辑与布尔检索架构）。
        你的任务是基于提供的【经过 TCS 评分分级的技术交底信息】，构建高水平布尔检索《检索要素矩阵》。

        ### 检索矩阵架构设计原则：A + B1..Bn + C
        你的输出必须严密契合以下模块，通常包含 5-10 个检索要素：
        1. **Block A (Subject/环境要素)**：必须有且仅有 1 个，作为整体技术领域或应用场景的锚点。
        2. **Block B 子块 (B1..Bn / 核心突破点)**：每个 B_i 对应一个核心效果。必须将该效果的贡献特征全量转化为要素，且**坚决不能把不同效果的特征混在同一个 B 子块中**（保证并行或交叉检索的灵活性）。
        3. **Block C (Functional/周边与限定要素)**：提取用于降噪、后置筛选的常规特征或功能限定特征。

        ### 关键业务规则（必须绝对服从）：
        1. **Hub 特征复用**：若某特征同时支撑多个效果，可在不同 B_i 子块中重复出现，并将其 `is_hub_feature` 设为 `true`。
        2. **词频控制与优先级判断**：
           - `term_frequency`: `low` (生僻/特异性强词汇，优先用于召回), `high` (常见/泛化词汇，常用于降噪限定)。
           - `priority_tier`: `core` (破新颖性的核心特征), `assist` (使能/配合特征), `filter` (应用领域/兜底降噪特征)。
        3. **要素分类 (`element_type`)**：精准归入以下5类，决定你的同义词扩展方向：
           - `Product_Structure`: 实体件/装置/部件。
           - `Method_Process`: 动作/步骤/工艺。
           - `Algorithm_Logic`: 算法/协议/模型架构。
           - `Material_Composition`: 物质/材料/化学成分。
           - `Parameter_Condition`: 物理参数/范围/阈值。

        ### 检索词扩展铁律 (Expansion Rules) - 【核心成败关键】
        你输出的 `keywords_zh` 和 `keywords_en` 将直接用于布尔组配，必须严格遵守：
        1. **中文扩展 (CN) 穷尽原则**：法定规范词汇 + 行业俗称 + 上下位概念（如 `["手机", "智能终端", "移动设备"]`）。遇到功能性结构必须进行结构/功能等效替换（如 `["弹簧", "弹性件", "偏置件", "复位件"]`）。
        2. **英文扩展 (EN) 截词与变体原则**：
           - 强制采用专利英语 (Patentese)。
           - **必须使用截词符** (`*` 或 `+` 或 `?`) 覆盖词根的多词性变体。例如：使用 `mix*` 涵盖 mixing, mixer, mixture；使用 `sensor*`；使用 `configur*`。
        3. **【致命错误防范（绝对禁止）】**：
           - **严禁**在数组的单一字符串内包含逻辑词（禁止输出 `"手机 OR 终端"`，必须输出 `["手机", "终端"]`）。
           - **严禁**输出无独立检索意义的短句子（禁止输出 `"该方法包括"`, `"相连接"`, `"配置为"`）。
           - 关键词要素必须是“词”或“短语”，绝不能是长句。

        ### 分类号分配原则 (ipc_cpc_ref)
        - Block A 优先分配【应用类 IPC】；Block B/C 优先分配【功能/结构类 IPC】。
        - 格式严控：必须符合国际标准 `[部类][大类][小类][大组]/[小组]`（如 `H04W 72/04`，注意大组与小组间有且仅有一个斜杠，前面必须有唯一的空格）。
        - 精度要求：只输出具有强相关性的 1-3 个分类号，如果拿不准小组，保留到大组级别（如 `G06F 17/00`）。

        ### 输出格式要求
        - 必须且只能输出一个 **JSON 数组 (List)**。
        - **绝对不要使用 Markdown 代码块（如 ```json）**，不要包含任何前语、后言或解释性文字。

        # 标准输出 Schema 示例：[
          {
            "element_name": "迷宫式密封环",
            "element_role": "KeyFeature",
            "block_id": "B1",
            "effect_cluster_ids": ["E1"],
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

            effect_cluster_ids: List[str] = []
            raw_cluster_ids = item.get("effect_cluster_ids")
            if isinstance(raw_cluster_ids, list):
                for value in raw_cluster_ids:
                    cluster_id = str(value or "").strip().upper()
                    if re.fullmatch(r"E\d+", cluster_id) and cluster_id not in effect_cluster_ids:
                        effect_cluster_ids.append(cluster_id)
            if block_id.startswith("B") and not effect_cluster_ids:
                if block_id[1:].isdigit():
                    effect_cluster_ids = [f"E{block_id[1:]}"]
            if not block_id.startswith("B") and block_id != "C":
                effect_cluster_ids = []

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
                    "effect_cluster_ids": effect_cluster_ids,
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

    def _normalize_execution_plan(self, raw_plan: Any) -> List[Dict[str, str]]:
        if not isinstance(raw_plan, list):
            logger.warning("执行计划输出类型异常，回退为空列表")
            return []

        normalized: List[Dict[str, str]] = []
        for item in raw_plan:
            if not isinstance(item, dict):
                continue

            normalized.append(
                {
                    "step_name": self._normalize_inline_text(item.get("step_name")) or "布尔交叉检索",
                    "condition": self._normalize_inline_text(item.get("condition"))
                    or "满足上一阶段执行阈值后执行",
                    "search_logic": self._normalize_inline_text(item.get("search_logic"))
                    or "未提取到逻辑表达式",
                    "rationale": self._normalize_inline_text(item.get("rationale"))
                    or "依据核心技术手段进行检索排查",
                    "database": self._normalize_inline_text(item.get("database")) or "专利数据库",
                }
            )

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
                    "effect_cluster_ids": [f"E{i}"],
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
        """
        重构点：为语义检索提供高纯度上下文。
        废除盲目追加全局 technical_means 的做法，防止各子块的 Embedding 向量同质化。
        只提供强相关的 主语 + 问题 + 当前特征 + 运行机理。
        """
        feature_details = bundle["feature_details"]
        hub_features = bundle["hub_features"]

        subject_matter = self._normalize_inline_text(self.report_data.get("claim_subject_matter", "未定义"))
        technical_problem = self._normalize_inline_text(self.report_data.get("technical_problem", "未定义"))

        lines = [
            f"[应用场景/主语] {subject_matter}",
            f"[待解决的具体问题] {technical_problem}",
            f"[{cluster['block_id']}/{','.join(cluster['effect_cluster_ids'])}] 目标核心效果: {cluster['effect_text']}",
        ]

        features = cluster.get("features", [])
        if features:
            lines.append("[实现该效果的专有技术手段及机理]")
            for feat in features:
                info = feature_details.get(feat, {})
                desc = self._normalize_inline_text(info.get("description", ""))
                rationale = self._normalize_inline_text(info.get("rationale", ""))
                hub_mark = " (Hub特征-跨效果协同)" if feat in hub_features else ""

                lines.append(f"- 结构/方法特征: {feat}{hub_mark}")
                if desc:
                    lines.append(f"  > 实施细节: {desc}")
                if rationale:
                    lines.append(f"  > 作用机理/互动关系: {rationale}")
        else:
            lines.append("[专有技术手段] (未显式提供，请依靠领域常识推演)")

        return "\n".join(lines)

    def _generate_semantic_query(
        self,
        raw_text: str,
        *,
        block_id: Optional[str] = None,
        effect_cluster_ids: Optional[List[str]] = None,
        effect_text: Optional[str] = None,
    ) -> str:
        """
        通过独立的 LLM 调用，将特定子块的特征+机理重写为高密度的向量检索 Query。
        """
        effect_id_display = ",".join(effect_cluster_ids or []) or "-"
        logger.info(f"调用 LLM 生成高密度语义检索 Query: {block_id or '-'} / {effect_id_display}")

        if not raw_text.strip():
            return ""

        system_prompt = """
        你是一位专门优化专利向量检索（Dense Retrieval / Embedding）质量的顶级 AI 专家。
        你的任务是将输入的某个【核心效果子块（如 B1/E1）】的离散特征与机理文本，重写为一段【极高信息密度的单一效果语义检索 Query】。
        这段 Query 将直接输入到 BGE-M3 等 Embedding 模型中用于检索高相关性的对比文件。

        ### 核心指导逻辑（Embedding 友好原则）：
        向量模型对 "技术手段(Means) -> 作用机理(Mechanism) -> 技术效果(Effect)" 的三元组因果结构最为敏感。
        你必须把输入信息融合成具有强逻辑连接的客观技术陈述句，最大化模型对该子块【创新本质】的注意力权重。

        ### 必须遵守的处理铁律：
        1. **绝对聚焦单一效果**：输出必须且只能围绕当前输入的核心效果展开，切忌发散到其他无关效果上。
        2. **极致降噪与提纯**：
           - 彻底清除任何主观修饰性套话（如“本发明创造性地提出了”、“有效地解决了痛点”、“极为重要”、“从物理学角度看”等）。
           - 剔除专利八股文（如“一种...的装置，包括：”、“根据权利要求所述的”）。
           - 清理所有格式符号、引用标号（如 `[1]`, `**`, `(★)`）。
        3. **无损保留技术硬核**：
           - **绝对保留**所有的“物理量、数学模型、特定材料、专有结构名词、算法名称、化学式”。
           - 必须清晰、连贯地描述【特征之间的位置关系/连接关系/数据流向】以及【如何引发特定机理】。
        4. **语言风格要求**：
           - 采用紧凑的客观技术陈述句，禁止使用列表格式。
           - 推荐句式：“一种应用于[场景]的技术。通过[技术特征/结构]，利用/基于[运作机理]，实现/达到[技术效果]。”
           - 字数浓缩在 200 - 300 字之间，确保输出内容的每个 Token 都是纯粹的技术干货。

        ### 示例对比：
        *   **原始输入**: "[应用场景] 旋转机械监测。[问题]早期轴承故障信号极易被背景噪声淹没。目标核心效果: 精准捕捉微裂纹。实施细节: 引入了 **自适应共振解调算法** [3](★区别特征)。作用机理: 利用 **包络检波器**[4] 将高频载波中的低频故障冲击进行非线性映射，配合 **多级带通滤波器** [5] 级联作用剥离强背景干扰。"
        *   **标准输出 (JSON)**:
        {{
            "semantic_query": "一种应用于旋转机械监测的轴承故障检测技术。通过引入自适应共振解调算法，利用包络检波器对高频载波中的低频故障冲击特征进行非线性映射，并结合多级带通滤波器的级联过滤机制，将微小的微伏级故障特征从强背景噪声中彻底分离，从而实现对早期轴承微裂纹的精确捕捉与检测。"
        }}

        ### 输出格式：
        必须输出为纯 JSON 格式，且只包含唯一的键 `semantic_query`。严禁使用 Markdown 代码块 (如 ```json)，严禁包含任何前言或解释性文字。
        """

        try:
            response = self.llm_service.invoke_text_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": (
                            f"子块标识: {block_id or 'B?'} / {effect_id_display}\n"
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
                logger.warning("LLM 返回的语义检索查询未命中 semantic_query 键，使用正则降级处理。")
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
                effect_cluster_ids=cluster["effect_cluster_ids"],
                effect_text=cluster["effect_text"],
            )
            if not clean_query:
                continue
            queries.append(
                {
                    "block_id": cluster["block_id"],
                    "effect_cluster_ids": cluster["effect_cluster_ids"],
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
