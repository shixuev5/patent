import re
from typing import Any, Dict, List, Set, Tuple
from loguru import logger


class FormalExaminer:
    """
    专利形式缺陷审查器
    """

    def __init__(self, parts_db: Dict[str, Any], image_parts: Dict[str, List[str]]):
        """
        :param parts_db: 文本提取的部件库 (key: id, value: dict(name=...))
        :param image_parts: 视觉识别的部件映射 (filename: [id_list])
        """
        raw_parts = parts_db or {}
        self.parts_db: Dict[str, Any] = {}
        for pid, info in raw_parts.items():
            normalized = self._norm_pid(pid)
            if not normalized:
                continue
            if normalized in self.parts_db and self.parts_db[normalized] != info:
                logger.warning(
                    f"部件标号归一化冲突：原始标号 {pid!r} 归一化为 {normalized!r}，将覆盖已有定义。"
                )
            self.parts_db[normalized] = info
        self.image_parts = image_parts or {}

        self.text_ids: Set[str] = set(self.parts_db.keys())
        self.image_ids: Set[str] = set()

        for ids_list in self.image_parts.values():
            for pid in ids_list or []:
                normalized = self._norm_pid(pid)
                if normalized:
                    self.image_ids.add(normalized)

    @staticmethod
    def _norm_pid(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        return re.sub(r"[^a-zA-Z0-9]", "", raw).lower()

    def check(self) -> Dict[str, Any]:
        """
        执行规则检查并返回旧版结构：
        {
          "consistency": "..."
        }
        """
        logger.info("开始执行形式缺陷检查（规则版）")
        missing_in_images, undefined_in_text = self._collect_consistency_issues()
        consistency = self._build_consistency_markdown(
            missing_in_images, undefined_in_text
        )
        return {"consistency": consistency}

    def _collect_consistency_issues(self) -> Tuple[List[str], List[str]]:
        missing_in_images = sorted(self.text_ids - self.image_ids)
        undefined_in_text = sorted(self.image_ids - self.text_ids)
        return missing_in_images, undefined_in_text

    def _build_consistency_markdown(
        self, missing_in_images: List[str], undefined_in_text: List[str]
    ) -> str:
        if not missing_in_images and not undefined_in_text:
            return "✅ **检查通过**：说明书文字部分与附图标记完全对应，未发现形式缺陷。"

        lines: List[str] = [
            "经一致性比对，发现说明书文字与附图标记存在以下不对应情况：\n"
        ]

        if missing_in_images:
            lines.append(
                f"**1. 说明书文字部分存在，但附图中未标记 ({len(missing_in_images)}处)：**"
            )
            for idx, pid in enumerate(missing_in_images):
                if idx >= 20:
                    lines.append(
                        f"- ... (共 {len(missing_in_images)} 条，仅显示前 20 条)"
                    )
                    break
                part_info = (
                    self.parts_db.get(pid, {})
                    if isinstance(self.parts_db, dict)
                    else {}
                )
                part_name = str(part_info.get("name", "未知名称")).strip() or "未知名称"
                lines.append(f"- 说明书中标号 {pid}-{part_name} 在说明书附图中未标记。")

        if undefined_in_text:
            if missing_in_images:
                lines.append("")
            lines.append(
                f"**2. 附图存在标记，但说明书文字部分未定义 ({len(undefined_in_text)}处)：**"
            )
            for idx, pid in enumerate(undefined_in_text):
                if idx >= 20:
                    lines.append(
                        f"- ... (共 {len(undefined_in_text)} 条，仅显示前 20 条)"
                    )
                    break
                lines.append(f"- 说明书附图标记 {pid} 在说明书文字部分未定义。")

        lines.append(
            "\n> *注：以上结果基于OCR识别与文本解析自动生成，请人工核查确认。*"
        )
        return "\n".join(lines)
