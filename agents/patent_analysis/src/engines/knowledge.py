import re
from typing import Any, Dict, List, Optional

from loguru import logger

from agents.common.utils.llm import get_llm_service


class KnowledgeExtractor:
    """专利多维部件知识提取器。"""

    def __init__(
        self,
        llm_service: Optional[Any] = None,
        model: Optional[str] = None,
    ):
        self.llm_service = llm_service or get_llm_service()
        self.model = model

    def extract_entities(self, patent_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        """从结构化专利数据中提取多维部件知识图谱。"""
        logger.info("[Knowledge] 开始提取多维部件图谱")

        text_to_analyze = self._prepare_text(patent_data)
        if not text_to_analyze:
            logger.warning("[Knowledge] 可用上下文为空，返回空 parts_db")
            return {}

        raw_entities = self._call_llm_for_extraction(text_to_analyze)
        parts_db = self._post_process_entities(raw_entities)

        logger.success(f"[Knowledge] 提取完成，共 {len(parts_db)} 个部件")
        return parts_db

    def _prepare_text(self, patent_data: Dict[str, Any], max_chars: int = 60000) -> str:
        """
        组装知识提取上下文：abstract + brief_description_of_drawings + detailed_description。
        """
        biblio = patent_data.get("bibliographic_data", {}) or {}
        description = patent_data.get("description", {}) or {}

        abstract = str(biblio.get("abstract", "") or "").strip()
        if isinstance(description, dict):
            brief_desc = str(
                description.get("brief_description_of_drawings", "") or ""
            ).strip()
            detailed_desc = str(description.get("detailed_description", "") or "").strip()
        else:
            brief_desc = ""
            detailed_desc = str(description).strip()

        sections: List[str] = []
        current_length = 0

        def append_section(title: str, content: str) -> None:
            nonlocal current_length
            clean = str(content or "").strip()
            if not clean:
                return
            block = f"### {title}\n{clean}\n"
            remaining = max_chars - current_length
            if remaining <= 0:
                return
            if len(block) > remaining:
                # 保证有意义截断，避免空块
                if remaining < 120:
                    return
                block = f"### {title}\n{clean[: max(0, remaining - 20)]}\n(下文已截断)\n"
            sections.append(block)
            current_length += len(block)

        append_section("摘要", abstract)
        append_section("附图说明", brief_desc)
        append_section("具体实施方式", detailed_desc)

        return "\n".join(sections).strip()

    def _call_llm_for_extraction(self, text: str) -> List[Dict[str, Any]]:
        """调用 LLM 抽取部件，输出对象包裹数组：{"parts": [...]}。"""
        system_prompt = """你是资深的专利审查专家和机械/电子/软件系统架构分析师。
任务：阅读专利说明书内容，提取所有带有【附图标记】的组成部分（涵盖机械零部件、电子元器件、控制/软件功能模块），构建高价值的多维知识关联图谱。

【严格抽取规则】（违背将导致解析失败）：
1. 目标排除：绝不能提取图号（如"图1"）、步骤号（如"S101"）、尺寸数值或单纯的数学符号。
2. 价值浓缩：提取内容必须具有实质技术价值。每个字段限30字以内，剔除无用废话，精准保留核心技术特征、装配关系、运动学特征或数据流向。
3. 强化图谱关联（核心）：在描述连接、位置、动作或功能时，必须尽可能引用其他关联部件的【附图标记】（如"固定于底座1"，"驱动齿轮20"，"接收传感器5的数据"），以此形成真正的网状节点关联。
4. 全局整合：同一标号若在多处提及，必须进行全局信息融合后再输出，只保留一条记录。
5. 缺失处理：若上下文中未提及某维度信息，坚决输出 null，切勿臆测。

【必须提取的字段与高价值规范】：
1. id: 附图标记，只保留字母和数字（如 "10", "11a"）。
2. name: 专利中使用的最准确部件/模块标准名称。
3. function: 核心作用与技术效果（例："将电机2的旋转转化为轴5的直线往复" 或 "分析气象数据以预测设备4的故障"）。
4. hierarchy: 明确的父级总成/系统ID（仅字母数字，如"100"）。仅在明确的"包含/组成"关系时填写，无则填 null。
5. spatial_connections: 空间装配关系及信号/网络/流体连接。格式要求包含目标ID与连接方式（机械部件例："法兰固定于[1]左侧，与[20]啮合"；软硬件例："与[3]通信连接，接收[5]的信号"）。
6. motion_state: 动态表现、运动学自由度或信号交互状态（例："绕轴[10]顺时针连续旋转"、"沿导轨往复滑动" 或 "周期性下发控制指令至[2]"）。
7. attributes: 最具区分度的属性特征，如特殊形状、关键材质、内部构造或软件模块的核心算法特征（例："耐高温钛合金"、"非对称中空圆柱体" 或 "基于神经网络的预测逻辑块"）。

请严格输出 JSON 对象（勿加 Markdown 标签），结构如下：
{
  "parts":[
    {
      "id": "10",
      "name": "驱动电机",
      "function": "为减速器20提供高速旋转动力",
      "hierarchy": "100",
      "spatial_connections": "螺栓固定于底座1左侧，输出端对接20",
      "motion_state": "定子静止，转子双向连续旋转",
      "attributes": "三相交流，带防水外壳"
    }
  ]
}
"""

        user_prompt = (
            "请严格按要求提取以下专利文本中的部件及多维关联图谱：\n\n"
            f"{text}"
        )

        try:
            data = self.llm_service.invoke_text_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                task_kind="knowledge_extract",
                model_override=self.model,
                temperature=0.0,
            )

            if not isinstance(data, dict):
                logger.warning("[Knowledge] LLM 输出不是 JSON 对象，回退空列表")
                return []

            parts = data.get("parts", [])
            if not isinstance(parts, list):
                logger.warning("[Knowledge] LLM 输出缺少 parts 数组，回退空列表")
                return []
            return parts

        except Exception as e:
            logger.error(f"[Knowledge] 调用 LLM 提取失败: {e}")
            return []

    def _post_process_entities(
        self, raw_entities: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """
        后处理：
        1. 过滤无效项
        2. ID 归一化（仅字母数字、小写）
        3. 同 ID 冲突合并（按字段信息量择优）
        """
        parts_db: Dict[str, Dict[str, Any]] = {}

        for item in raw_entities:
            if not isinstance(item, dict):
                continue

            clean_id = self._normalize_part_id(item.get("id"))
            if not clean_id:
                continue
            
            # 过滤常见的非部件脏数据 (以 s 开头且全数字结尾的步骤号，或长段文字)
            if re.match(r"^s\d+$", clean_id) or len(clean_id) > 10:
                continue

            record = self._build_record(item)
            existing = parts_db.get(clean_id)
            parts_db[clean_id] = (
                self._merge_records(existing, record) if existing else record
            )

        return parts_db

    @staticmethod
    def _normalize_part_id(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        return re.sub(r"[^a-zA-Z0-9]", "", raw).lower()

    def _build_record(self, item: Dict[str, Any]) -> Dict[str, Any]:
        name = self._normalize_optional_text(item.get("name"))
        function = self._normalize_optional_text(item.get("function"))
        spatial = self._normalize_optional_text(item.get("spatial_connections"))
        motion = self._normalize_optional_text(item.get("motion_state"))
        attributes = self._normalize_optional_text(item.get("attributes"))

        hierarchy = self._normalize_hierarchy_id(item.get("hierarchy"))

        return {
            "name": name,
            "function": function,
            "hierarchy": hierarchy,
            "spatial_connections": spatial,
            "motion_state": motion,
            "attributes": attributes,
        }

    @classmethod
    def _normalize_hierarchy_id(cls, value: Any) -> Optional[str]:
        raw = str(value or "").strip()
        if cls._is_unknown(raw):
            return None
        normalized = cls._normalize_part_id(raw)
        return normalized or None

    def _merge_records(
        self, old: Dict[str, Any], new: Dict[str, Any]
    ) -> Dict[str, Any]:
        merged = dict(old)
        for field in [
            "name",
            "function",
            "spatial_connections",
            "motion_state",
            "attributes",
        ]:
            merged[field] = self._pick_richer_text(old.get(field), new.get(field))

        merged["hierarchy"] = self._pick_richer_hierarchy(
            old.get("hierarchy"), new.get("hierarchy")
        )
        return merged

    def _pick_richer_text(self, old_value: Any, new_value: Any) -> Optional[str]:
        old_text = self._normalize_optional_text(old_value)
        new_text = self._normalize_optional_text(new_value)
        if self._text_score(new_text) > self._text_score(old_text):
            return new_text
        return old_text

    def _pick_richer_hierarchy(self, old_value: Any, new_value: Any) -> Optional[str]:
        old_text = self._normalize_text(old_value, default="")
        new_text = self._normalize_text(new_value, default="")
        old_norm = None if self._is_unknown(old_text) else old_text
        new_norm = None if self._is_unknown(new_text) else new_text
        if self._text_score(new_norm or "") > self._text_score(old_norm or ""):
            return new_norm
        return old_norm

    @classmethod
    def _normalize_optional_text(cls, value: Any) -> Optional[str]:
        text = str(value or "").strip()
        if cls._is_unknown(text):
            return None
        return text

    @classmethod
    def _normalize_text(cls, value: Any, default: str = "") -> str:
        text = str(value or "").strip()
        return text if text else default

    @classmethod
    def _is_unknown(cls, value: Any) -> bool:
        text = str(value or "").strip().lower()
        return text in {"", "未提及", "未知", "none", "null", "n/a"}

    @classmethod
    def _text_score(cls, value: Any) -> int:
        text = str(value or "").strip()
        if cls._is_unknown(text):
            return 0
        return len(text) + 20
