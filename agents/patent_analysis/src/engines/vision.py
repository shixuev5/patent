import cv2
import re
import os
import json
import shutil
import math
import numpy as np
import base64
import requests
from pathlib import Path
from typing import Any, List, Dict, Set, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from PIL import Image, ImageDraw, ImageFont
from paddleocr import PaddleOCR
from config import settings
from loguru import logger
from agents.common.utils.llm import get_llm_service


class VisualProcessor:
    """视觉处理核心类，负责 OCR、标注和图片批处理"""

    def __init__(
        self,
        patent_data: Dict,
        parts_db: Dict,
        raw_img_dir: Path,
        out_dir: Path,
        init_ocr: bool = True,
    ):
        """
        :param patent_data: 专利结构化数据 (JSON)
        :param parts_db: 零部件知识库
        :param raw_img_dir: 原始图片目录
        :param out_dir: 输出目录 (annotated_dir)
        """

        # 支持模式: 'local', 'online'。其余值自动回退到 local。
        self.engine_type = os.getenv("OCR_ENGINE", "local").lower()
        if self.engine_type not in {"local", "online"}:
            logger.warning(
                f"[视觉] 不支持的 OCR_ENGINE={self.engine_type!r}，已自动回退为 'local'。"
            )
            self.engine_type = "local"

        self.patent_data = patent_data
        self.parts_db = parts_db
        self.raw_img_dir = raw_img_dir
        self.out_dir = out_dir

        self.api_url = settings.OCR_BASE_URL
        self.api_token = settings.OCR_API_KEY
        self.ocr_engine = None
        self._ocr_engine_lock: Optional[Lock] = None

        if not init_ocr:
            logger.info("[视觉] 仅初始化标注流程，跳过 OCR 引擎加载。")
            return

        if self.engine_type == "local":
            logger.info("[视觉] 正在初始化本地 PaddleOCR 引擎...")
            self.ocr_engine = PaddleOCR(
                text_detection_model_name="PP-OCRv5_server_det",
                text_recognition_model_name="PP-OCRv5_server_rec",
                use_doc_orientation_classify=False,  # 不使用文档方向分类模型
                use_doc_unwarping=False,  # 不使用文本图像矫正模型
                use_textline_orientation=False,  # 不使用文本行方向分类模型
                text_rec_score_thresh=0.7,  # 识别置信度
                text_det_thresh=0.2,  # 文本检测像素阈值
                text_det_box_thresh=0.3,  # 文本检测框阈值
                text_det_unclip_ratio=1.5,  # 文本检测扩张系数
            )
            self._ocr_engine_lock: Optional[Lock] = Lock()

        elif self.engine_type == "online":
            logger.info("[视觉] 使用 AI Studio 在线 OCR 接口。")
            self.ocr_engine = None
            self._ocr_engine_lock = None

    def process_patent_images(self) -> Dict[str, List[str]]:
        """
        兼容入口：先做识别，再做标注。
        """
        image_parts, image_labels = self.extract_image_labels()
        self.annotate_from_image_labels(image_labels)
        return image_parts

    def extract_image_labels(self) -> Tuple[Dict[str, List[str]], Dict[str, List[Dict[str, Any]]]]:
        """
        提取图片中的部件标号与可视化标签，不写入标注图。
        :return: (image_parts, image_labels)
        """
        target_filenames = self._extract_target_filenames()
        logger.info(f"[视觉] 检测到 {len(target_filenames)} 张目标图片待分析。")

        if not self.raw_img_dir.exists():
            logger.warning(f"[视觉] 原始图片目录不存在：{self.raw_img_dir}")
            return {}, {}

        all_images = sorted(self.raw_img_dir.glob("*.*"), key=lambda p: p.name)
        if not all_images:
            return {}, {}

        image_parts: Dict[str, List[str]] = {}
        image_labels: Dict[str, List[Dict[str, Any]]] = {}

        if self.engine_type == "local":
            logger.info(f"[视觉] 本地模式按顺序识别图片，总数={len(all_images)}")
            for img_path in all_images:
                filename = img_path.name
                try:
                    processed_name, part_ids, labels = self._extract_image_task(
                        img_path, target_filenames
                    )
                except Exception as e:
                    logger.error(f"[视觉] 顺序识别异常 {filename}: {e}")
                    continue
                if part_ids:
                    image_parts[processed_name] = part_ids
                if labels:
                    image_labels[processed_name] = labels
            return image_parts, image_labels

        max_workers = self._resolve_max_workers(len(all_images))
        logger.info(
            f"[视觉] 在线模式并行识别图片，总数={len(all_images)}，并发={max_workers}"
        )

        with ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="vision"
        ) as executor:
            future_map = {
                executor.submit(self._extract_image_task, img_path, target_filenames): img_path.name
                for img_path in all_images
            }

            for future in as_completed(future_map):
                filename = future_map[future]
                try:
                    processed_name, part_ids, labels = future.result()
                except Exception as e:
                    logger.error(f"[视觉] 并行识别异常 {filename}: {e}")
                    continue
                if part_ids:
                    image_parts[processed_name] = part_ids
                if labels:
                    image_labels[processed_name] = labels

        return image_parts, image_labels

    def annotate_from_image_labels(
        self, image_labels: Dict[str, List[Dict[str, Any]]]
    ) -> None:
        """
        根据已识别标签生成标注图；无标签的图片直接复制。
        """
        if not self.raw_img_dir.exists():
            logger.warning(f"[视觉] 原始图片目录不存在：{self.raw_img_dir}")
            return

        self.out_dir.mkdir(parents=True, exist_ok=True)
        all_images = sorted(self.raw_img_dir.glob("*.*"), key=lambda p: p.name)
        for img_path in all_images:
            filename = img_path.name
            out_path = self.out_dir / filename
            labels = image_labels.get(filename, []) if isinstance(image_labels, dict) else []
            try:
                if labels:
                    self._annotate_image(str(img_path), labels, str(out_path))
                else:
                    shutil.copy2(img_path, out_path)
            except Exception as e:
                logger.error(f"[视觉] 标注阶段处理失败 {filename}: {e}")
                if img_path.exists():
                    shutil.copy2(img_path, out_path)

    def _extract_image_task(
        self, img_path: Path, target_filenames: Set[str]
    ) -> Tuple[str, List[str], List[Dict[str, Any]]]:
        filename = img_path.name
        if self.parts_db and filename in target_filenames:
            extracted = self._extract_single_image(img_path)
            return filename, extracted["found_pids"], extracted["labels"]
        return filename, [], []

    @staticmethod
    def _resolve_max_workers(total_images: int) -> int:
        raw = str(os.getenv("VISION_MAX_WORKERS", "")).strip()
        if raw.isdigit():
            configured = max(1, int(raw))
        else:
            configured = min(8, max(1, (os.cpu_count() or 1)))
        return min(configured, max(1, total_images))

    def _extract_target_filenames(self) -> Set[str]:
        """解析 JSON 数据，提取摘要图和附图的文件名"""
        filenames = set()

        # 1. 提取摘要附图
        abs_fig = self.patent_data.get("bibliographic_data", {}).get("abstract_figure")
        if abs_fig:
            fname = self._clean_md_path(abs_fig)
            if fname:
                filenames.add(fname)

        # 2. 提取附图列表
        drawings = self.patent_data.get("drawings", [])
        for draw in drawings:
            path_str = draw.get("file_path", "")
            fname = self._clean_md_path(path_str)
            if fname:
                filenames.add(fname)

        return filenames

    def _clean_md_path(self, md_str: str) -> str:
        """从 markdown 链接提取文件名"""
        if not md_str:
            return ""

        match = re.search(r"([^/]+?)$", md_str)
        path = match.group(1) if match else md_str

        return Path(path).name

    def _extract_single_image(self, img_path: Path) -> Dict[str, Any]:
        """对单张图片做 OCR + VLM 纠错并提取标签，不进行绘图。"""
        try:
            # 1) 基础 OCR
            if self.engine_type == "local":
                raw_ocr = self._run_local_ocr(str(img_path))
            else:
                raw_ocr = self._run_online_ocr(str(img_path))

            # 修复粘连：拆分 "101 106" 这种结果
            raw_ocr = self._expand_merged_ocr_results(raw_ocr)

            # 2) VLM 结合 parts_db 进行清洗/纠错/补全
            corrected_results = self._run_hybrid_vlm_correction(str(img_path), raw_ocr)

            valid_labels = []
            found_pids = []
            seen_pids = set()

            for item in corrected_results:
                text = str(item.get("text", ""))
                box = item.get("box")
                if not text or not isinstance(box, list) or len(box) != 4:
                    continue

                # 统一归一化：保留数字/字母并转小写
                # 例如: "(16a)" -> "16a", "11-A" -> "11a"
                match_key = self._normalize_pid(text)
                if not match_key:
                    continue

                # 情况 1: 在零部件库中找到了 (用于生成标注图 + 记录存在性)
                if match_key in self.parts_db:
                    label_text = self.parts_db[match_key].get("name") or match_key
                    valid_labels.append(
                        {
                            "text": label_text,
                            "box": box,
                        }
                    )
                    if match_key not in seen_pids:
                        found_pids.append(match_key)
                        seen_pids.add(match_key)

                # 情况 2: 库里没有，但是格式非常像一个部件编号 (用于形式审查报错)
                # 只有当它"长得像编号"且之前没添加过时才记录
                elif self._is_potential_part_id(match_key):
                    # 注意：这里不加入 valid_labels，因为没有中文名，画在图上没意义
                    # 但是要加入 found_pids，这样 FormalExaminer 就会发现它在 parts_db 里不存在
                    if match_key not in seen_pids:
                        found_pids.append(match_key)
                        seen_pids.add(match_key)

            return {
                "found_pids": found_pids,
                "labels": valid_labels,
            }

        except Exception as e:
            logger.error(f"[视觉] 识别图片失败 {img_path.name}: {e}")
            return {"found_pids": [], "labels": []}

    @staticmethod
    def _normalize_pid(value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        clean_text = re.sub(r"[^a-zA-Z0-9]", "", raw)
        return clean_text.lower()

    def _is_potential_part_id(self, text: str) -> bool:
        """
        判断一个未定义的文本是否像是一个附图标记。

        核心规则：
        1. 必须以数字开头。
        2. 开头数字不能是 '0' (排除 '0', '01', '05' 等 OCR 误识别)。
        """
        if not text:
            return False

        # --- 规则 1: 必须以数字开头 (排除 FIG, A-A, Section) ---
        if not text[0].isdigit():
            return False

        # --- 规则 2: 开头数字不能是 0 (User Request) ---
        # 专利中不使用 '0' 或 '0x' 作为标记
        if text.startswith("0"):
            return False

        # --- 规则 3: 长度过滤 ---
        # 附图标记通常很短，如 "1", "10", "102", "12a"
        if len(text) > 6:
            return False

        # --- 规则 4: 排除明显的单位或序数词 ---
        # text 已经是小写且去除了标点

        # 常见物理单位 (排除 mm, cm, kg, hz 等)
        if text.endswith(("mm", "cm", "km", "kg", "hz", "kv", "mg", "ml")):
            return False

        # 常见序数词缩写 (1st, 2nd, 3rd, 4th)
        if text.endswith(("st", "nd", "rd", "th")):
            return False

        return True

    def _preprocess_for_ocr(self, img_path: str) -> Tuple[Optional[np.ndarray], float]:
        """
        OCR 专用预处理：智能放大 + 背景除法清洗
        :return: (处理后的图像array, 缩放比例)
        """
        if not os.path.exists(img_path):
            return None, 1.0

        img = cv2.imread(img_path)
        if img is None:
            return None, 1.0

        h, w = img.shape[:2]

        # 1. 智能放大逻辑
        # 目标：确保短边至少 1600px，使 10px 的小数字变成 ~25px 以上
        target_min_side = 1600
        # 与 PaddleOCR 文本检测侧长上限对齐，避免预处理放大后再被模型内部缩小
        det_max_side_limit = 4000
        min_side = min(h, w)
        max_side = max(h, w)

        if min_side < target_min_side:
            scale_factor = target_min_side / min_side
            scale_factor = min(scale_factor, 4.0)  # 限制最大 4 倍
        else:
            scale_factor = 1.0

        # 防止放大后超过检测上限，导致二次插值损失细节并增加耗时
        if max_side * scale_factor > det_max_side_limit:
            scale_factor = det_max_side_limit / max_side

        if scale_factor > 1.0:
            # 使用 Lanczos 插值，保持线条平滑
            processed_img = cv2.resize(
                img,
                None,
                fx=scale_factor,
                fy=scale_factor,
                interpolation=cv2.INTER_LANCZOS4,
            )
        else:
            processed_img = img.copy()

        # 转灰度
        if len(processed_img.shape) == 3:
            gray = cv2.cvtColor(processed_img, cv2.COLOR_BGR2GRAY)
        else:
            gray = processed_img

        # 2. 背景除法清洗 (Background Division)
        # 估算背景 (膨胀去除线条)
        dilated = cv2.dilate(gray, np.ones((25, 25), np.uint8))
        bg_blur = cv2.medianBlur(dilated, 21)

        # 核心算法：(原图 / 背景)
        # 255 - absdiff 近似于除法效果，能强制背景变白
        diff = 255 - cv2.absdiff(gray, bg_blur)

        # 线性拉伸到 0-255
        norm_img = cv2.normalize(
            diff, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX, dtype=cv2.CV_8UC1
        )

        # 阈值漂白：去除浅灰色的水印/噪点 (Truncate mode)
        _, result = cv2.threshold(norm_img, 230, 255, cv2.THRESH_TRUNC)

        # 再次拉伸对比度，使文字更黑
        final_result = cv2.normalize(
            result,
            None,
            alpha=0,
            beta=255,
            norm_type=cv2.NORM_MINMAX,
            dtype=cv2.CV_8UC1,
        )

        # 对浅灰底细线稿做极轻加粗，提升小标号(如 1/2/3/S1)可读性
        dark_ratio = float(np.mean(final_result < 170))
        if dark_ratio < 0.12:
            kernel = np.ones((2, 2), np.uint8)
            final_result = cv2.erode(final_result, kernel, iterations=1)

        # # B. 高斯模糊 (Smoothing)
        # # 这一步非常关键：它能消除锯齿，把"马赛克"变成"线条"，极大提升 OCR 识别率
        # result = cv2.GaussianBlur(result, (3, 3), 0)

        return final_result, scale_factor

    def _expand_merged_ocr_results(self, results: List[Dict]) -> List[Dict]:
        """
        修复 OCR 粘连问题：将 "101 106" 或 "10a 10b" 拆分为独立的 box。
        使用线性插值估算每个子串的坐标。
        """
        expanded = []

        for item in results:
            text = item["text"]
            box = item["box"]  # [xmin, ymin, xmax, ymax]

            if re.search(r"[a-zA-Z0-9]+\s+[a-zA-Z0-9]+", text):
                # 按空格拆分
                parts = text.split()

                # 准备坐标计算
                xmin, ymin, xmax, ymax = box
                total_width = xmax - xmin
                total_chars = len(text)  # 原文总长度（含空格）

                current_char_idx = 0

                for part in parts:
                    # 跳过空字符串
                    if not part.strip():
                        continue

                    # 找到该片段在原字符串中的起始位置
                    start_idx = text.find(part, current_char_idx)
                    if start_idx == -1:
                        continue  # 防止极端异常导致系统崩溃
                    end_idx = start_idx + len(part)

                    # 线性插值计算新坐标
                    # 假设字符是等宽的，根据字符索引位置比例切割 box
                    new_xmin = xmin + int((start_idx / total_chars) * total_width)
                    new_xmax = xmin + int((end_idx / total_chars) * total_width)

                    # 构造新 item
                    new_box = [new_xmin, ymin, new_xmax, ymax]

                    expanded.append({"text": part, "box": new_box})

                    # 更新搜索游标，防止重复查找
                    current_char_idx = end_idx
            else:
                # 没有粘连，保留原样
                expanded.append(item)

        return expanded

    def _run_local_ocr(self, img_path: str) -> List[Dict]:
        """运行 OCR 返回原始结果"""

        processed_img, scale = self._preprocess_for_ocr(img_path)

        if processed_img is None:
            logger.warning(f"[视觉] 图片预处理失败：{img_path}")
            return []

        # PaddleOCR 3.x text-det expects 3-channel input (H, W, C).
        # _preprocess_for_ocr may return grayscale (H, W), so convert before predict.
        ocr_input = processed_img
        if len(ocr_input.shape) == 2:
            ocr_input = cv2.cvtColor(ocr_input, cv2.COLOR_GRAY2BGR)
        elif len(ocr_input.shape) == 3 and ocr_input.shape[2] == 4:
            ocr_input = cv2.cvtColor(ocr_input, cv2.COLOR_BGRA2BGR)

        try:
            # PaddleOCR 推理对象并非强线程安全，推理调用加锁，其他步骤保持并行。
            if self._ocr_engine_lock is None:
                result = self.ocr_engine.predict(ocr_input)
            else:
                with self._ocr_engine_lock:
                    result = self.ocr_engine.predict(ocr_input)
        except Exception as e:
            logger.error(f"[视觉] PaddleOCR 推理失败：{e}")
            return []

        if not result or not result[0]:
            return []

        texts = result[0].get("rec_texts", [])
        boxes = result[0].get("rec_boxes", []).tolist()

        formatted = []
        for text, box in zip(texts, boxes):
            xmin, ymin, xmax, ymax = box

            xmin = max(0, int(xmin / scale))
            ymin = max(0, int(ymin / scale))
            xmax = int(xmax / scale)
            ymax = int(ymax / scale)

            formatted.append({"text": text, "box": [xmin, ymin, xmax, ymax]})

        return formatted

    def _run_online_ocr(self, img_path: str) -> List[Dict]:
        """
        调用自定义的 AI Studio OCR 接口
        流程：本地预处理(放大/去底) -> 内存转Base64 -> POST请求 -> 解析坐标 -> 坐标还原
        """
        # 1. 复用图像增强 (关键)
        processed_img, scale = self._preprocess_for_ocr(img_path)
        if processed_img is None:
            return []

        try:
            # 2. 内存编码: Numpy -> JPG Bytes -> Base64 String
            success, encoded_img = cv2.imencode(".jpg", processed_img)
            if not success:
                logger.error(f"[在线OCR] 图片编码失败：{img_path}")
                return []

            file_data = base64.b64encode(encoded_img.tobytes()).decode("ascii")

            # 3. 构造请求
            headers = {
                "Authorization": f"token {self.api_token}",
                "Content-Type": "application/json",
            }

            payload = {
                "file": file_data,
                "fileType": 1,  # 1 for Image
                "useDocOrientationClassify": False,
                "useDocUnwarping": False,
                "useTextlineOrientation": False,
                "textDetThresh": 0.2,
                "textDetBoxThresh": 0.3,
                "textDetUnclipRatio": 1.5,
                "textRecScoreThresh": 0.7,
            }

            # 4. 发送请求
            response = requests.post(self.api_url, json=payload, headers=headers)
            response.raise_for_status()  # 检查 200

            result_json = response.json()

            # 5. 解析结果
            ocr_results = result_json.get("result", {}).get("ocrResults", [])
            ocr_result = ocr_results[0].get("prunedResult", {})

            texts = ocr_result.get("rec_texts", [])
            boxes = ocr_result.get("rec_boxes", [])

            formatted = []
            for text, box in zip(texts, boxes):
                xmin, ymin, xmax, ymax = box

                xmin = max(0, int(xmin / scale))
                ymin = max(0, int(ymin / scale))
                xmax = int(xmax / scale)
                ymax = int(ymax / scale)

                formatted.append({"text": text, "box": [xmin, ymin, xmax, ymax]})

            return formatted

        except Exception as e:
            logger.error(f"[在线OCR] 请求失败 {Path(img_path).name}: {e}")
            return []

    def _build_parts_context(self, max_chars: int = 7000) -> str:
        """
        将 parts_db 压缩为用于 VLM 纠错的上下文，包含多维关系字段并做长度保护。
        """
        if not self.parts_db:
            return "（无部件上下文）"

        lines: List[str] = []
        current_length = 0
        for pid, info in self.parts_db.items():
            if not isinstance(info, dict):
                continue
            name = str(info.get("name", "未知部件") or "未知部件").strip()
            function = str(info.get("function", "未提及") or "未提及").strip()
            hierarchy = str(info.get("hierarchy", "未提及") or "未提及").strip()
            spatial = str(
                info.get("spatial_connections", "未提及") or "未提及"
            ).strip()
            motion = str(info.get("motion_state", "未提及") or "未提及").strip()
            line = (
                f"- 标号:{pid}; 名称:{name}; 功能:{function}; 层级:{hierarchy}; "
                f"空间连接:{spatial}; 运动状态:{motion}"
            )

            next_length = current_length + len(line) + 1
            if next_length > max_chars:
                lines.append("- ... (parts_context 已截断)")
                break
            lines.append(line)
            current_length = next_length

        return "\n".join(lines) if lines else "（无部件上下文）"

    def _run_hybrid_vlm_correction(
        self, img_path: str, raw_ocr: List[Dict]
    ) -> List[Dict]:
        """
        利用 VLM 结合零部件库，对 OCR 结果进行清洗、纠错和漏检补全。
        失败时回退到 raw_ocr。
        """
        try:
            llm_service = get_llm_service()

            # 读取图片尺寸用于坐标边界约束
            img = cv2.imread(img_path)
            if img is None:
                logger.error(f"[视觉] 读取图片失败：{img_path}")
                return raw_ocr
            h, w = img.shape[:2]

            parts_context = self._build_parts_context(max_chars=7000)
            ocr_context = json.dumps(raw_ocr, ensure_ascii=False)

            system_prompt = """
            你是专利视觉审查专家。请结合【说明书部件库】和【初筛OCR结果】，输出图片中真实存在的附图标记。

            传统OCR常见错误：
            1. 错认：如 10->1O、101->10l，或把噪点认成标号；
            2. 漏认：低对比度标号未识别；
            3. 截断：OCR 框未包全字符。

            判定原则：
            - 优先识别部件库中出现过的标号；
            - 带引出线的数字通常是附图标记；
            - 不要把图名（FIG.1）、正文、尺寸线、剖面线字母（A-A）当作标号。

            输出要求：
            - 只输出 JSON 数组，不要 Markdown；
            - 格式必须是：
              [{"text":"10","box":[xmin,ymin,xmax,ymax]}, ...]
            - box 使用像素坐标（与输入图片同坐标系）。
            """

            user_prompt = f"""
            图片分辨率：{w}x{h}

            【说明书部件库 (Ground Truth)】：
            {parts_context}

            【初筛 OCR 结果 (含坐标，可能有误)】：
            {ocr_context}

            请审视图片后修正 OCR：
            - 纠正错字、删除噪点框；
            - 若部件库中的标号明显存在但 OCR 漏检，请补充并给出坐标框。
            """

            content = llm_service.analyze_image_with_thinking(
                img_path, system_prompt, user_prompt
            )

            data = self._parse_vlm_json_array(content)
            if not isinstance(data, list):
                logger.warning(
                    f"[视觉] 混合纠错解析失败，回退原始 OCR：{Path(img_path).name}"
                )
                return raw_ocr

            formatted = []
            for item in data:
                if not isinstance(item, dict):
                    continue

                text = str(item.get("text", "")).strip()
                box = item.get("box", [])
                if not text or not isinstance(box, list) or len(box) != 4:
                    continue

                try:
                    x1 = int(min(float(box[0]), float(box[2])))
                    y1 = int(min(float(box[1]), float(box[3])))
                    x2 = int(max(float(box[0]), float(box[2])))
                    y2 = int(max(float(box[1]), float(box[3])))
                except (TypeError, ValueError):
                    continue

                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w, x2), min(h, y2)
                if x2 <= x1 or y2 <= y1:
                    continue

                formatted.append({"text": text, "box": [x1, y1, x2, y2]})

            logger.info(
                f"[视觉] 混合纠错完成，识别到 {len(formatted)} 个标号：{Path(img_path).name}"
            )
            if not formatted and raw_ocr:
                logger.warning(
                    f"[视觉] 混合纠错返回空结果，回退原始 OCR：{Path(img_path).name}"
                )
                return raw_ocr
            return formatted

        except Exception as e:
            logger.warning(
                f"[视觉] 混合纠错失败，已回退原始 OCR：{Path(img_path).name}，错误：{e}"
            )
            return raw_ocr

    @staticmethod
    def _parse_vlm_json_array(content: str) -> List[Dict]:
        text = str(content or "").strip()
        if not text:
            return []

        cleaned = text.replace("```json", "").replace("```", "").strip()
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass

        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start != -1 and end != -1 and start < end:
            candidate = cleaned[start : end + 1]
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                return []

        return []

    def _annotate_image(self, img_path: str, labels: list, output_path: str):
        """
        在图片上绘制标签
        labels: [{'text': '中文名', 'box': [x1,y1,x2,y2]}, ...]
        """
        try:
            processor = LabelPlacer(img_path)
            result_img = processor.place_labels(labels)
            cv2.imwrite(output_path, result_img)
        except Exception as e:
            logger.error(f"[视觉] 标注绘制失败：{img_path}，错误：{e}")
            # 失败时尝试直接拷贝原图
            shutil.copy2(img_path, output_path)


class LabelPlacer:
    """智能避障标签放置器"""

    def __init__(self, image_path):
        self.original_img = cv2.imdecode(np.fromfile(image_path, dtype=np.uint8), -1)
        if self.original_img is None:
            raise ValueError(f"Image not found: {image_path}")

        # 处理 Alpha 通道，转为 BGR
        if len(self.original_img.shape) == 3 and self.original_img.shape[2] == 4:
            self.original_img = cv2.cvtColor(self.original_img, cv2.COLOR_BGRA2BGR)

        # 确保是 3 通道 BGR (处理灰度图情况)
        if len(self.original_img.shape) == 2:
            self.original_img = cv2.cvtColor(self.original_img, cv2.COLOR_GRAY2BGR)

        self.h, self.w = self.original_img.shape[:2]

        # 自适应字体大小逻辑
        max_side = max(self.h, self.w)
        if max_side < 600:
            self.font_size = 14
        elif max_side < 800:
            self.font_size = 16
        else:
            self.font_size = 20

        # 障碍地图与占用地图
        gray = cv2.cvtColor(self.original_img, cv2.COLOR_BGR2GRAY)
        # 简单的线条提取作为障碍物
        _, self.line_mask = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
        kernel = np.ones((3, 3), np.uint8)
        self.obstacle_map = cv2.dilate(self.line_mask, kernel, iterations=1)
        self.occupied_map = np.zeros((self.h, self.w), dtype=np.uint8)

    def mark_existing_boxes(self, boxes):
        for box in boxes:
            x1, y1, x2, y2 = map(int, box)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(self.w, x2), min(self.h, y2)
            if x2 > x1 and y2 > y1:
                cv2.rectangle(self.occupied_map, (x1, y1), (x2, y2), 255, -1)
                cv2.rectangle(self.obstacle_map, (x1, y1), (x2, y2), 255, -1)

    def is_location_safe(self, x, y, w, h, check_lines=True):
        if x < 0 or y < 0 or x + w > self.w or y + h > self.h:
            return False

        # 1. 绝对不可重叠：其他标签
        roi_occ = self.occupied_map[y : y + h, x : x + w]
        if np.count_nonzero(roi_occ) > 0:
            return False

        # 2. 尽量不遮挡：原图线条
        if check_lines:
            roi_obs = self.obstacle_map[y : y + h, x : x + w]
            # 容忍度设为 5%
            if np.count_nonzero(roi_obs) > (w * h * 0.05):
                return False

        return True

    def search_position(self, center_box, text_w, text_h):
        """
        辐射搜索最佳位置
        :return: (x, y, use_leader_line)
        """
        # 计算原框中心点
        cx = (center_box[0] + center_box[2]) // 2
        cy = (center_box[1] + center_box[3]) // 2

        padding = 3  # 紧贴时的间距

        # --- 策略 1: 紧邻四周 (无引线) ---
        # 优化优先级：正上 -> 正下 -> 正右 -> 正左
        # 优化对齐：上下放置时，水平居中；左右放置时，垂直居中

        candidates_tier1 = [
            # (x, y, 描述)
            (cx - text_w // 2, center_box[1] - text_h - padding),  # 正上 (居中)
            (cx - text_w // 2, center_box[3] + padding),  # 正下 (居中)
            (center_box[2] + padding, cy - text_h // 2),  # 正右 (垂直居中)
            (center_box[0] - text_w - padding, cy - text_h // 2),  # 正左 (垂直居中)
        ]

        for x, y in candidates_tier1:
            # Tier 1 必须严格检查线条遮挡，因为没有引线，直接盖在图上会很丑
            if self.is_location_safe(int(x), int(y), text_w, text_h, check_lines=True):
                return int(x), int(y), False

        # --- 策略 2: 辐射搜索 (有引线) ---

        # 定义优先角度 (度)：优先垂直和斜角，最后才是水平
        # -90=上, 90=下, -45=右上, 45=右下, -135=左上, 135=左下, 0=右, 180=左
        preferred_angles = [-90, 90, -45, 45, -135, 135, 0, 180]

        # 补充角度：如果在上述角度找不到，再尝试中间的角度
        secondary_angles = [a for a in range(0, 360, 20) if a not in preferred_angles]

        # 定义半径：更密集的步长，防止跳得太远
        # 25px, 35px: 非常近的引线，解决 "13" 这种稍微挪一点就好的情况
        radii = [25, 35, 50, 70, 90, 120, 150]

        # 开始搜索：先遍历半径，再遍历角度（保证尽可能近）
        for r in radii:
            # 合并角度列表，优先尝试黄金角度
            check_sequence = preferred_angles + secondary_angles

            for angle in check_sequence:
                theta = math.radians(angle)

                # 计算候选中心
                cand_cx = cx + int(r * math.cos(theta))
                cand_cy = cy + int(r * math.sin(theta))

                # 推算左上角
                x = cand_cx - text_w // 2
                y = cand_cy - text_h // 2

                # 检查: 有引线时，优先找不遮挡线条的区域
                if self.is_location_safe(x, y, text_w, text_h, check_lines=True):
                    return x, y, True

        # --- 策略 3: 放宽限制 (允许遮挡线条) ---
        # 如果干净的地方找不到，就在附近找个能放下的地方（遮住线条也没关系，有白底）
        fallback_radii = [40, 60]
        for r in fallback_radii:
            for angle in preferred_angles:
                theta = math.radians(angle)
                x = cx + int(r * math.cos(theta)) - text_w // 2
                y = cy + int(r * math.sin(theta)) - text_h // 2
                if self.is_location_safe(x, y, text_w, text_h, check_lines=False):
                    return x, y, True

        # --- 策略 4: 终极兜底 ---
        # 放在右上角，不管是否遮挡
        return int(center_box[2] + 10), int(center_box[1] - 10), True

    def place_labels(self, labels_data):
        # 转换为 RGB 供 PIL 处理
        img_rgb = cv2.cvtColor(self.original_img, cv2.COLOR_BGR2RGB)
        img_pil = Image.fromarray(img_rgb)
        draw = ImageDraw.Draw(img_pil)

        try:
            # 字体大小适配
            font = ImageFont.truetype(str(settings.FONT_PATH), self.font_size)
        except:
            font = ImageFont.load_default()

        # 1. 标记所有 OCR 原框
        all_boxes = [item["box"] for item in labels_data]
        self.mark_existing_boxes(all_boxes)

        for item in labels_data:
            text = item["text"]
            box = item["box"]

            src_cx = (box[0] + box[2]) // 2
            src_cy = (box[1] + box[3]) // 2

            # 计算文字尺寸 (稍微留宽一点 padding)
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0] + 8
            text_h = bbox[3] - bbox[1] + 4

            # 搜索位置
            x, y, need_line = self.search_position(box, text_w, text_h)

            # 边界修正
            x = max(0, min(self.w - text_w, x))
            y = max(0, min(self.h - text_h, y))

            if need_line:
                dst_cx = x + text_w // 2
                dst_cy = y + text_h // 2

                # 画引线 (红色)
                draw.line([(src_cx, src_cy), (dst_cx, dst_cy)], fill="red", width=2)

            # 画标签背景 (白色填充 + 红色边框)
            # outline="red" 使标签更醒目
            draw.rectangle((x, y, x + text_w, y + text_h), fill="white", outline=None)

            # 画文字 (垂直居中微调)
            text_x = x + 4
            text_y = y + 2
            draw.text((text_x, text_y), text, font=font, fill=settings.LABEL_COLOR)

            # 更新占用
            self.mark_existing_boxes([[x, y, x + text_w, y + text_h]])

        return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
