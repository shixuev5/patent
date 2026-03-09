import re
from typing import Any, Dict, List, Optional

from loguru import logger

from agents.common.utils.llm import get_llm_service
from config import settings


class KnowledgeExtractor:
    """专利多维部件知识提取器。"""

    def __init__(
        self,
        llm_service: Optional[Any] = None,
        model: Optional[str] = None,
    ):
        self.llm_service = llm_service or get_llm_service()
        self.model = model or settings.LLM_MODEL_REASONING or settings.LLM_MODEL

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
        system_prompt = """
你是资深的专利审查专家和机械/电子系统分析师。
任务：阅读专利说明书内容，提取所有带有【附图标记】的实际零部件，并输出多维知识图谱。

【严格抽取规则】（违背将导致解析失败）：
1. 目标排除：绝不能提取图号（如"图1","FIG.2"）、步骤号（如"S101"）、尺寸数值或公式符号。只提取真实的物理部件。
2. 极度精炼：为防止输出超长，所有描述字段必须高度浓缩（每个字段限15字以内），只提取核心名词和动词。
3. 全局整合：同一标号如果多次提及，请自动汇总其关系并只输出一条记录。
4. 缺失处理：若上下文中未提及某维度信息，字段值请输出为 null。

【必须提取的字段与规范】：
1. id: 附图标记，只保留字母和数字（如 "10", "11a"）。
2. name: 部件标准名称。
3. function: 核心作用（限10字）。
4. hierarchy: 父级部件ID（仅字母数字，如"100"）；若无明确父级则填 null。
5. spatial_connections: 空间位置与连接对象（如"位于1顶部且连接20"）。
6. motion_state: 工作时的动态表现（如"旋转","滑动","静止"）。
7. attributes: 形状或材质（如"圆柱形","弹性"）。

请严格输出 JSON 对象（勿加 Markdown 标签），结构如下：
{
  "parts":[
    {
      "id": "10",
      "name": "驱动电机",
      "function": "提供输入动力",
      "hierarchy": "100",
      "spatial_connections": "固定于底座1左侧接减速器20",
      "motion_state": "输出轴连续旋转",
      "attributes": "圆柱状外壳"
    }
  ]
}
"""

        user_prompt = (
            "请严格按要求提取以下专利文本中的部件及多维关系：\n\n"
            f"{text}"
        )

        try:
            data = self.llm_service.chat_completion_json(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
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
