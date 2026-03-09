import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger

from config import settings


class FormalExaminer:
    """
    专利形式缺陷审查器
    """

    def __init__(
        self,
        parts_db: Dict[str, Any],
        image_parts: Dict[str, List[str]],
        drawings_dir: Optional[Path] = None,
        drawing_image_names: Optional[Set[str]] = None,
        llm_service: Any = None,
        review_model: Optional[str] = None,
    ):
        """
        初始化审查器

        :param parts_db: 文本提取的部件库 (key: id, value: dict(name=...))
        :param image_parts: 视觉识别的部件映射 (filename: [id_list])
        :param drawings_dir: 说明书附图目录（Mineru 输出 images 目录）
        :param drawing_image_names: 官方附图文件名集合（仅 drawings）
        :param llm_service: 可注入的 LLM 服务（便于测试）
        :param review_model: 复核模型名；为空时使用 settings.VLM_MODEL_MINI
        """
        self.parts_db = parts_db or {}
        self.image_parts = image_parts or {}
        self.drawings_dir = Path(drawings_dir) if drawings_dir else None
        self.drawing_image_names = {
            str(name or "").strip()
            for name in (drawing_image_names or set())
            if str(name or "").strip()
        }
        self.llm_service = llm_service
        self.review_model = (
            str(review_model).strip()
            if review_model is not None
            else str(settings.VLM_MODEL_MINI or "").strip()
        )

        self.text_ids: Set[str] = {self._norm_pid(key) for key in self.parts_db.keys() if key}
        self.image_ids: Set[str] = set()
        self.part_to_images: Dict[str, Set[str]] = {}

        for filename, ids_list in self.image_parts.items():
            clean_name = str(filename or "").strip()
            for pid in ids_list or []:
                normalized = self._norm_pid(pid)
                if not normalized:
                    continue
                self.image_ids.add(normalized)
                self.part_to_images.setdefault(normalized, set()).add(clean_name)

    SECONDARY_REVIEW_SYSTEM_PROMPT = """
你是专业的专利审查员。系统通过机器视觉(OCR)初步提取了附图标记，并与说明书文字进行了比对，发现了一些不一致的疑点。
由于OCR技术存在局限性（容易漏认标号，或把图像噪点/线条错认为标号），你的任务是复核这些疑点，剔除机器误报，只向专利代理师报告真实存在的专利缺陷。

请针对每条疑点返回 JSON，必须符合以下判定规则：

判定规则：
1. 对于 issue_type = "missing_in_images"（文字提到了标号X，但机器说图里没有）：
   - 如果你在提供的附图中能找到标号X -> 专利没问题 -> 返回 "false_alarm"
   - 如果你在附图中确实找不到标号X -> 专利存在缺陷 -> 返回 "defect_confirmed"

2. 对于 issue_type = "undefined_in_text"（机器说图里有个标号Y，但文字没解释）：
   - 如果附图中的Y不是引出线标号（如污渍、尺寸线、正文文字、噪点等）-> 专利没问题 -> 返回 "false_alarm"
   - 如果附图里明确标注了引出线标号Y，但文字缺失 -> 专利存在缺陷 -> 返回 "defect_confirmed"

3. 如果图像模糊、遮挡或信息不足导致无法判断，返回 "uncertain"。

返回格式如下：
{
  "results": [
    {
      "issue_id": "string",
      "user_verdict": "defect_confirmed | false_alarm | uncertain",
      "confidence": "high | medium | low",
      "reason": "一句话说明依据，不超过80字"
    }
  ]
}
仅输出 JSON，不要输出 Markdown，不要输出额外解释。
"""

    @staticmethod
    def _norm_pid(value: Any) -> str:
        return str(value or "").strip().lower()

    def check(self) -> Dict[str, Any]:
        """
        执行检查并返回结果字典
        """
        logger.info("开始执行形式缺陷检查")
        issues, _, _ = self._collect_consistency_issues()
        secondary_review_result = self._run_secondary_review(issues)
        status = str(secondary_review_result.get("status", "skipped"))

        reviewed_items = secondary_review_result.get("items", [])
        if status == "skipped" and not reviewed_items and issues:
            fallback_reason = "图像复核未执行，当前疑点需人工核实。"
            reviewed_items = [
                self._build_review_item(issue, self._review_uncertain(fallback_reason), issue.get("related_images", []))
                for issue in issues
            ]
            secondary_review_result["items"] = reviewed_items
            secondary_review_result["summary"] = self._build_secondary_summary(reviewed_items, status)

        user_actionable_issues = [
            item
            for item in reviewed_items
            if str(item.get("user_verdict", "")).strip() in {"defect_confirmed", "uncertain"}
        ]

        results: Dict[str, Any] = {}
        results["consistency"] = self._build_user_centric_markdown(user_actionable_issues)
        results["raw_issues"] = issues
        results["secondary_review"] = secondary_review_result
        results["user_actionable_issues"] = user_actionable_issues
        return results

    def _collect_consistency_issues(self) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
        missing_in_images = sorted(self.text_ids - self.image_ids)
        undefined_in_text = sorted(self.image_ids - self.text_ids)
        all_drawing_paths = self._list_all_drawing_paths()

        issues: List[Dict[str, Any]] = []

        for pid in missing_in_images:
            part_info = self.parts_db.get(pid, {})
            part_name = part_info.get("name", "未知名称")
            related_names = [p.name for p in all_drawing_paths]
            issues.append(
                {
                    "issue_id": f"missing_in_images:{pid}",
                    "issue_type": "missing_in_images",
                    "part_id": pid,
                    "part_name": part_name,
                    "preliminary_result": "potential_issue",
                    "rule_message": f"说明书中标号 {pid}-{part_name} 在说明书附图中未标记。",
                    "related_images": related_names,
                    "reviewed_image_paths": [str(p) for p in all_drawing_paths],
                }
            )

        for pid in undefined_in_text:
            related_names = sorted(self.part_to_images.get(pid, set()))
            resolved_paths = self._resolve_image_paths(related_names)
            issues.append(
                {
                    "issue_id": f"undefined_in_text:{pid}",
                    "issue_type": "undefined_in_text",
                    "part_id": pid,
                    "part_name": "未定义",
                    "preliminary_result": "potential_issue",
                    "rule_message": f"说明书附图标记 {pid} 在说明书文字部分未定义。",
                    "related_images": [p.name for p in resolved_paths] or related_names,
                    "reviewed_image_paths": [str(p) for p in resolved_paths],
                }
            )

        return issues, missing_in_images, undefined_in_text

    def _list_all_drawing_paths(self) -> List[Path]:
        if not self.drawings_dir or not self.drawings_dir.exists():
            return []

        image_suffixes = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
        paths = [p for p in self.drawings_dir.iterdir() if p.is_file() and p.suffix.lower() in image_suffixes]
        if self.drawing_image_names:
            paths = [p for p in paths if p.name in self.drawing_image_names]
        return sorted(paths, key=lambda p: p.name)

    def _resolve_image_paths(self, image_names: List[str]) -> List[Path]:
        if not self.drawings_dir or not self.drawings_dir.exists():
            return []

        resolved: List[Path] = []
        for name in image_names:
            if self.drawing_image_names and name not in self.drawing_image_names:
                continue
            candidate = self.drawings_dir / name
            if candidate.exists() and candidate.is_file():
                resolved.append(candidate)
        return resolved

    def _build_user_centric_markdown(self, valid_issues: List[Dict[str, Any]]) -> str:
        if not valid_issues:
            return "✅ **检查通过**：说明书文字部分与附图标记一致，未发现明显形式缺陷。（系统已自动过滤机器视觉误识别干扰）"

        lines: List[str] = ["⚠️ **发现附图与文字不一致的缺陷**，请核查：\n"]
        missing_issues = [i for i in valid_issues if i.get("issue_type") == "missing_in_images"]
        undefined_issues = [i for i in valid_issues if i.get("issue_type") == "undefined_in_text"]

        if missing_issues:
            lines.append(f"**1. 文字提到了零件，但附图中可能漏标 ({len(missing_issues)}处)：**")
            for idx, item in enumerate(missing_issues):
                if idx >= 20:
                    lines.append(f"- ... (共 {len(missing_issues)} 条，仅显示前 20 条)")
                    break
                pid = str(item.get("part_id", "")).strip() or "-"
                name = str(item.get("part_name", "")).strip() or "未知名称"
                reason = str(item.get("reason", "")).strip()
                verdict = str(item.get("user_verdict", "uncertain")).strip()
                tag = "🔍 [需人工核实]" if verdict == "uncertain" else "❌ [确认缺陷]"
                if reason:
                    lines.append(f"- {tag} 说明书提到了 `{pid}-{name}`，但在附图中未找到。({reason})")
                else:
                    lines.append(f"- {tag} 说明书提到了 `{pid}-{name}`，但在附图中未找到。")

        if undefined_issues:
            lines.append(f"\n**2. 附图存在引出线标号，但说明书中未定义 ({len(undefined_issues)}处)：**")
            for idx, item in enumerate(undefined_issues):
                if idx >= 20:
                    lines.append(f"- ... (共 {len(undefined_issues)} 条，仅显示前 20 条)")
                    break
                pid = str(item.get("part_id", "")).strip() or "-"
                reason = str(item.get("reason", "")).strip()
                verdict = str(item.get("user_verdict", "uncertain")).strip()
                tag = "🔍 [需人工核实]" if verdict == "uncertain" else "❌ [确认缺陷]"
                if reason:
                    lines.append(f"- {tag} 附图出现了标号 `{pid}`，但说明书文字未作解释。({reason})")
                else:
                    lines.append(f"- {tag} 附图出现了标号 `{pid}`，但说明书文字未作解释。")

        lines.append("\n> *注：以上结果已通过AI视觉复核，剔除了因图像分辨率造成的机器误报。标记为[确认缺陷]的条目请重点关注。*")
        return "\n".join(lines)

    def _run_secondary_review(self, issues: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not issues:
            return {
                "status": "skipped",
                "reason": "no_issues",
                "model": self.review_model or "-",
                "summary": "规则检查未发现疑点，跳过图像复核模型复核。",
                "items": [],
            }

        if not self.review_model:
            return {
                "status": "skipped",
                "reason": "not_configured",
                "model": "-",
                "summary": "未配置 VLM_MODEL_MINI，跳过图像复核模型复核。",
                "items": [],
            }

        if self.llm_service is None:
            from agents.common.utils.llm import get_llm_service
            self.llm_service = get_llm_service()

        reviewed_items: List[Dict[str, Any]] = []
        error_count = 0

        issues_by_image_group: Dict[Tuple[str, ...], List[Dict[str, Any]]] = {}
        for issue in issues:
            reviewed_images = issue.get("related_images", [])
            image_paths = self._normalize_image_paths(issue.get("reviewed_image_paths", []))
            issue["reviewed_image_paths"] = image_paths

            if not image_paths:
                reviewed_items.append(self._build_review_item(issue, self._review_uncertain("未找到可用附图，无法进行视觉复核。"), reviewed_images))
                continue

            group_key = tuple(image_paths)
            issues_by_image_group.setdefault(group_key, []).append(issue)

        if issues_by_image_group:
            total_items = sum(len(group_issues) for group_issues in issues_by_image_group.values())
            logger.info(
                f"图像复核模型批量复核：疑点数={total_items}，请求组数={len(issues_by_image_group)}"
            )

        for image_group, group_issues in issues_by_image_group.items():
            try:
                reviewed_map = self._review_issue_batch(group_issues, list(image_group))
                for issue in group_issues:
                    reviewed_images = issue.get("related_images", [])
                    issue_id = str(issue.get("issue_id", "")).strip()
                    review = reviewed_map.get(issue_id)
                    if not review:
                        review = self._review_uncertain("模型未返回该疑点的复核结果。")
                    reviewed_items.append(self._build_review_item(issue, review, reviewed_images))
            except Exception as ex:
                error_count += len(group_issues)
                logger.warning(
                    f"图像复核模型批量复核失败，image_group_size={len(image_group)} "
                    f"issues={len(group_issues)} error={ex}"
                )
                for issue in group_issues:
                    reviewed_items.append(
                        self._build_review_item(
                            issue,
                            self._review_uncertain(f"模型调用失败：{ex}"),
                            issue.get("related_images", []),
                        )
                    )

        status = "completed"
        if error_count and error_count == len(issues):
            status = "error"
        elif error_count:
            status = "partial"

        return {
            "status": status,
            "reason": "ok" if status == "completed" else "partial_error",
            "model": self.review_model,
            "summary": self._build_secondary_summary(reviewed_items, status),
            "items": reviewed_items,
        }

    def _review_issue_batch(
        self, issues: List[Dict[str, Any]], image_paths: List[str]
    ) -> Dict[str, Dict[str, str]]:
        ordered_issues = sorted(
            issues,
            key=lambda item: (str(item.get("issue_type", "")), str(item.get("part_id", ""))),
        )
        payload = [
            {
                "issue_id": str(item.get("issue_id", "")).strip(),
                "issue_type": str(item.get("issue_type", "")).strip(),
                "part_id": str(item.get("part_id", "")).strip(),
                "part_name": str(item.get("part_name", "")).strip(),
                "rule_message": str(item.get("rule_message", "")).strip(),
            }
            for item in ordered_issues
        ]
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        user_prompt = (
            "请对以下疑点逐条复核，并返回每条疑点的结论。\n"
            "疑点列表(JSON):\n"
            f"{payload_json}\n"
            "注意：issue_id 必须与输入一致，且 results 覆盖全部输入疑点。"
        )

        response = self.llm_service.analyze_images_json_with_thinking(
            image_paths=image_paths,
            system_prompt=self.SECONDARY_REVIEW_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            model=self.review_model,
            temperature=0.0,
        )

        reviewed_map: Dict[str, Dict[str, str]] = {}
        response_items: List[Dict[str, Any]] = []

        if isinstance(response, dict):
            raw_results = response.get("results")
            if isinstance(raw_results, list):
                response_items = [item for item in raw_results if isinstance(item, dict)]
            elif len(ordered_issues) == 1:
                only_issue_id = str(ordered_issues[0].get("issue_id", "")).strip()
                reviewed_map[only_issue_id] = self._normalize_review_result(response)
        elif isinstance(response, list):
            response_items = [item for item in response if isinstance(item, dict)]

        for item in response_items:
            issue_id = str(item.get("issue_id", "")).strip()
            if not issue_id:
                continue
            reviewed_map[issue_id] = self._normalize_review_result(item)

        return reviewed_map

    @staticmethod
    def _normalize_image_paths(image_paths: List[str]) -> List[str]:
        return sorted({str(path).strip() for path in image_paths if str(path).strip()})

    @staticmethod
    def _review_uncertain(reason: str) -> Dict[str, str]:
        return {"user_verdict": "uncertain", "confidence": "low", "reason": str(reason or "").strip() or "无法判断。"}

    def _normalize_review_result(self, data: Dict[str, Any]) -> Dict[str, str]:
        verdict = str(data.get("user_verdict", "uncertain")).strip().lower()
        confidence = str(data.get("confidence", "low")).strip().lower()
        reason = str(data.get("reason", "")).strip()

        if verdict not in {"defect_confirmed", "false_alarm", "uncertain"}:
            verdict = "uncertain"
        if confidence not in {"high", "medium", "low"}:
            confidence = "low"
        if not reason:
            reason = "模型未提供明确依据。"

        return {
            "user_verdict": verdict,
            "confidence": confidence,
            "reason": reason,
        }

    @staticmethod
    def _build_review_item(
        issue: Dict[str, Any], review: Dict[str, str], reviewed_images: List[str]
    ) -> Dict[str, Any]:
        return {
            "issue_id": issue.get("issue_id", ""),
            "issue_type": issue.get("issue_type", ""),
            "part_id": issue.get("part_id", ""),
            "part_name": issue.get("part_name", ""),
            "rule_message": issue.get("rule_message", ""),
            "preliminary_result": issue.get("preliminary_result", ""),
            "user_verdict": review.get("user_verdict", "uncertain"),
            "confidence": review.get("confidence", "low"),
            "reason": review.get("reason", "模型未提供明确依据。"),
            "reviewed_images": reviewed_images,
        }

    def _build_secondary_summary(self, reviewed_items: List[Dict[str, Any]], status: str) -> str:
        if not reviewed_items:
            return "无初步疑点，无需复核。"

        defect_count = sum(1 for item in reviewed_items if item.get("user_verdict") == "defect_confirmed")
        false_alarm_count = sum(1 for item in reviewed_items if item.get("user_verdict") == "false_alarm")
        uncertain_count = sum(1 for item in reviewed_items if item.get("user_verdict") == "uncertain")

        prefix = "图像复核完成"
        if status == "skipped":
            prefix = "图像复核未执行（已全量回退为人工核实）"
        elif status == "partial":
            prefix = "图像复核部分完成（部分异常已回退为人工核实）"
        elif status == "error":
            prefix = "图像复核失败（已全量回退为人工核实）"

        return (
            f"{prefix}：初步规则产生 {len(reviewed_items)} 条疑点。"
            f"AI智能过滤误报 {false_alarm_count} 条，"
            f"确认真实缺陷 {defect_count} 条，"
            f"画面模糊/异常需人工判断 {uncertain_count} 条。"
        )
