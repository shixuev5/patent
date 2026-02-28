# src/checker.py

from typing import Dict, List, Any
from loguru import logger


class FormalExaminer:
    """
    专利形式缺陷审查器
    """

    def __init__(self, parts_db: Dict[str, Any], image_parts: Dict[str, List[str]]):
        """
        初始化审查器
        :param parts_db: 文本提取的部件库 (key: id, value: dict(name=...))
        :param image_parts: 视觉识别的部件映射 (filename: [id_list])
        """
        self.parts_db = parts_db
        self.image_parts = image_parts
        
        # 预处理：提取集合
        self.text_ids = set(self.parts_db.keys())
        self.image_ids = set()
        for _, ids_list in self.image_parts.items():
            valid_ids = [str(i).strip() for i in ids_list if i]
            self.image_ids.update(valid_ids)

    def check(self) -> Dict[str, str]:
        """
        执行检查并返回结果字典
        """
        logger.info("Running Formal Defect Checks...")
        results = {}
        results["consistency"] = self._check_consistency()
        return results

    def _check_consistency(self) -> str:
        """
        [内部方法] 附图标记一致性检查逻辑
        """
        missing_in_images = sorted(list(self.text_ids - self.image_ids))
        undefined_in_text = sorted(list(self.image_ids - self.text_ids))

        # 1. 检查通过
        if not missing_in_images and not undefined_in_text:
            return "✅ **检查通过**：说明书文字部分与附图标记完全对应，未发现形式缺陷。"

        # 2. 存在问题，组装报告
        lines = []
        lines.append("经一致性比对，发现说明书文字与附图标记存在以下不对应情况：")

        # Case A: 说明书有，附图没有
        if missing_in_images:
            lines.append(f"\n**1. 说明书文字部分存在，但附图中未标记 ({len(missing_in_images)}处)：**")
            for idx, mid in enumerate(missing_in_images):
                # 限制显示前 20 条，避免报告过长
                if idx >= 20:
                    lines.append(f"- ... (共 {len(missing_in_images)} 条，仅显示前 20 条)")
                    break
                
                # 获取部件名称
                part_info = self.parts_db.get(mid, {})
                name = part_info.get("name", "未知名称")
                
                # 使用指定话术
                lines.append(f"- 说明书中标号 {mid}-{name} 在说明书附图中未标记。")

        # Case B: 附图有，说明书没有
        if undefined_in_text:
            lines.append(f"\n**2. 附图存在标记，但说明书文字部分未定义 ({len(undefined_in_text)}处)：**")
            for idx, uid in enumerate(undefined_in_text):
                if idx >= 20:
                    lines.append(f"- ... (共 {len(undefined_in_text)} 条，仅显示前 20 条)")
                    break
                
                # 使用指定话术
                lines.append(f"- 说明书附图标记 {uid} 在说明书文字部分未定义。")

        lines.append("\n> *注：以上结果基于OCR识别与文本解析自动生成，请人工核查确认。*")
            
        return "\n".join(lines)