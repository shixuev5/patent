import cv2
import re
import os
import json
import shutil
import math
import time
import numpy as np
import base64
import requests
from pathlib import Path
from typing import Any, List, Dict, Set, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from PIL import Image, ImageDraw, ImageFont

from agents.common.utils.langchain_compat import install_langchain_compat

install_langchain_compat()

from paddleocr import PaddleOCR
from config import settings
from loguru import logger
from agents.common.utils.concurrency import submit_with_current_context
from agents.common.utils.llm import get_llm_service


class VisualProcessor:
    """视觉处理核心类，负责 OCR、标注和图片批处理 (两阶段架构分离 CPU 与 IO)"""

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
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                text_rec_score_thresh=0.7,
                text_det_thresh=0.2,
                text_det_box_thresh=0.3,
                text_det_unclip_ratio=1.5,
            )
            self._ocr_engine_lock = Lock()

        elif self.engine_type == "online":
            logger.info("[视觉] 使用 AI Studio 在线 OCR 接口。")
            self.ocr_engine = None
            self._ocr_engine_lock = None

    def extract_image_labels(self) -> Tuple[Dict[str, List[str]], Dict[str, List[Dict[str, Any]]]]:
        """提取图片中的部件标号与可视化标签 (按引擎类型动态调度架构)。"""
        target_filenames = self._extract_target_filenames()
        logger.info(f"[视觉] 检测到 {len(target_filenames)} 张目标图片待分析。")

        if not self.raw_img_dir.exists():
            logger.warning(f"[视觉] 原始图片目录不存在：{self.raw_img_dir}")
            return {}, {}

        all_images = sorted(self.raw_img_dir.glob("*.*"), key=lambda p: p.name)
        if not all_images:
            return {}, {}

        # 预过滤出真正需要处理的图片
        target_images =[img for img in all_images if self.parts_db and img.name in target_filenames]
        max_workers = self._resolve_max_workers(len(all_images))
        
        # 初始化结果字典，默认跳过VLM的为 None
        ocr_results_map = {img.name: None for img in all_images}

        # =========================================================
        # 阶段一：OCR 初筛提取 (根据引擎动态决定并发策略)
        # =========================================================
        if self.engine_type == "local":
            logger.info("[视觉] 阶段一：本地模式，串行执行 OCR 以保护 CPU/GPU 资源...")
            for img_path in target_images:
                try:
                    ocr_results_map[img_path.name] = self._run_ocr_pipeline(str(img_path))
                except Exception as e:
                    logger.error(f"[视觉] 本地 OCR 处理异常 {img_path.name}: {e}")
                    ocr_results_map[img_path.name] = []
        else:
            logger.info(f"[视觉] 阶段一：在线模式，并发执行 OCR 请求 (并发数={max_workers})...")
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="vision_ocr") as executor:
                future_map = {
                    submit_with_current_context(
                        executor, self._run_ocr_pipeline, str(img_path)
                    ): img_path.name
                    for img_path in target_images
                }
                for future in as_completed(future_map):
                    filename = future_map[future]
                    try:
                        ocr_results_map[filename] = future.result()
                    except Exception as e:
                        logger.error(f"[视觉] 在线 OCR 并发异常 {filename}: {e}")
                        ocr_results_map[filename] =[]

        # =========================================================
        # 阶段二：网络 IO 密集型任务 (并发调用 VLM 大模型)
        # =========================================================
        logger.info(f"[视觉] 阶段二：开始并发请求 VLM 大模型进行图像清洗与分类 (并发数={max_workers})...")
        static_system_prompt = self._build_static_system_prompt()
        
        image_parts: Dict[str, List[str]] = {}
        image_labels: Dict[str, List[Dict[str, Any]]] = {}
        
        vlm_jobs: List[Tuple[str, str, List[Dict]]] = []
        for img_path in all_images:
            filename = img_path.name
            raw_ocr = ocr_results_map.get(filename)
            if raw_ocr is None:
                continue
            vlm_jobs.append((filename, str(img_path), raw_ocr))

        if vlm_jobs:
            warm_filename, warm_img_path, warm_raw_ocr = vlm_jobs[0]
            try:
                part_ids, labels = self._run_vlm_pipeline(
                    warm_img_path, warm_raw_ocr, static_system_prompt
                )
                if part_ids:
                    image_parts[warm_filename] = part_ids
                if labels:
                    image_labels[warm_filename] = labels
            except Exception as e:
                logger.error(f"[视觉] VLM 预热调用异常 {warm_filename}: {e}")

            remaining_jobs = vlm_jobs[1:]
            if remaining_jobs:
                with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="vision_vlm") as executor:
                    future_map = {
                        submit_with_current_context(
                            executor,
                            self._run_vlm_pipeline,
                            img_path,
                            raw_ocr,
                            static_system_prompt,
                        ): filename
                        for filename, img_path, raw_ocr in remaining_jobs
                    }
                    for future in as_completed(future_map):
                        filename = future_map[future]
                        try:
                            part_ids, labels = future.result()
                            if part_ids:
                                image_parts[filename] = part_ids
                            if labels:
                                image_labels[filename] = labels
                        except Exception as e:
                            logger.error(f"[视觉] 并发 VLM 异常 {filename}: {e}")

        return image_parts, image_labels
    
    def annotate_from_image_labels(self, image_labels: Dict[str, List[Dict[str, Any]]]) -> None:
        """根据已识别标签生成标注图；无标签的图片直接复制。"""
        if not self.raw_img_dir.exists():
            return

        self.out_dir.mkdir(parents=True, exist_ok=True)
        all_images = sorted(self.raw_img_dir.glob("*.*"), key=lambda p: p.name)
        for img_path in all_images:
            filename = img_path.name
            out_path = self.out_dir / filename
            labels = image_labels.get(filename, []) if isinstance(image_labels, dict) else[]
            try:
                if labels:
                    self._annotate_image(str(img_path), labels, str(out_path))
                else:
                    shutil.copy2(img_path, out_path)
            except Exception as e:
                logger.error(f"[视觉] 标注阶段处理失败 {filename}: {e}")
                if img_path.exists():
                    shutil.copy2(img_path, out_path)

    @staticmethod
    def _resolve_max_workers(total_images: int) -> int:
        configured = max(1, int(getattr(settings, "VLM_MAX_WORKERS", 4) or 4))
        return min(configured, max(1, total_images))

    def _extract_target_filenames(self) -> Set[str]:
        """解析 JSON 数据，提取摘要图和附图的文件名"""
        filenames = set()
        abs_fig = self.patent_data.get("bibliographic_data", {}).get("abstract_figure")
        if abs_fig:
            fname = self._clean_md_path(abs_fig)
            if fname:
                filenames.add(fname)

        drawings = self.patent_data.get("drawings",[])
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

    def _run_ocr_pipeline(self, img_path: str) -> List[Dict]:
        """纯 OCR 提取管道（智能放大 + 除法去底 + 粘连切分）"""
        if self.engine_type == "local":
            raw_ocr = self._run_local_ocr(img_path)
        else:
            raw_ocr = self._run_online_ocr(img_path)
        
        # 修复粘连：将 "101 106" 拆分为独立的坐标框，为 VLM 降低认知负担
        raw_ocr = self._expand_merged_ocr_results(raw_ocr)
        return raw_ocr

    def _run_vlm_pipeline(self, img_path: str, raw_ocr: List[Dict], static_system_prompt: str) -> Tuple[List[str], List[Dict]]:
        """纯 VLM 提取管道：利用缓存的 System Prompt 结合当前图片和 OCR 数据纠错"""
        try:
            llm_service = get_llm_service()
            img = cv2.imread(img_path)
            if img is None:
                logger.error(f"[视觉] 读取图片失败：{img_path}")
                return [],[]
            h, w = img.shape[:2]

            ocr_context = json.dumps(raw_ocr, ensure_ascii=False)
            
            # 缩减 User Prompt 的长度，只传入图片特征数据
            user_prompt = f"""
            图片分辨率：{w}x{h}
            【初筛 OCR 结果 (含坐标，可能包含大量坐标刻度/等高线噪点)】：
            {ocr_context}
            
            请严格按照要求分类，并清洗修正 OCR 结果。
            """

            # 使用安全重试调用机制
            content = self._safe_invoke_vlm(llm_service, img_path, static_system_prompt, user_prompt)
            data = self._parse_vlm_response(content)
            
            image_type = data.get("image_type", "other")
            reasoning = data.get("reasoning", "无推理过程")
            marks = data.get("marks",[])
            img_name = Path(img_path).name

            logger.info(f"[视觉] 图纸分析 {img_name} | 分类: {image_type} | 理由: {reasoning}")

            # 核心防御：如果是图表曲线/等高线/坐标系等，绝对不要提取数字，防止AI 审查误报
            if image_type == "chart_graph":
                logger.info(f"[视觉] {img_name} 为数据图表，主动跳过标记提取。")
                return [],[]

            valid_labels = []
            found_pids =[]
            seen_pids = set()

            for item in marks:
                text = str(item.get("text", ""))
                box = item.get("box")
                if not text or not isinstance(box, list) or len(box) != 4:
                    continue

                # 归一化保留数字/字母
                match_key = self._normalize_pid(text)
                if not match_key:
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

                # 直接信任 VLM 的提取结果并记入审查队列，废弃脆弱的正则过滤
                if match_key not in seen_pids:
                    found_pids.append(match_key)
                    seen_pids.add(match_key)

                # 如果在说明书零部件库中找到了，用于生成标注图
                if match_key in self.parts_db:
                    label_text = self.parts_db[match_key].get("name") or match_key
                    valid_labels.append({
                        "text": label_text,
                        "box":[x1, y1, x2, y2],
                    })

            return found_pids, valid_labels

        except Exception as e:
            logger.error(f"[视觉] 混合纠错提取异常 {Path(img_path).name}: {e}")
            return [],[]

    def _safe_invoke_vlm(self, llm_service, img_path: str, sys_prompt: str, usr_prompt: str) -> str:
        """带退避重试机制的 LLM 调用（抗并发限流保护）"""
        max_retries = 3
        base_wait = 2
        
        for attempt in range(max_retries):
            try:
                return llm_service.invoke_vision_image(
                    img_path, sys_prompt, usr_prompt, task_kind="vision_ocr_correction"
                )
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = base_wait * (2 ** attempt)  # 2s, 4s, 8s
                    logger.warning(f"[视觉] VLM API 调用受阻，{wait_time}秒后重试 ({attempt+1}/{max_retries}): {e}")
                    time.sleep(wait_time)
                else:
                    logger.error(f"[视觉] VLM API 达到最大重试次数，调用失败: {e}")
                    raise

    def _build_static_system_prompt(self) -> str:
        """构建静态前缀提示词，触发大型模型底层 Prompt Caching 从而降本提速"""
        parts_context = self._build_parts_context(max_chars=7000)
        
        return f"""你是专利视觉审查专家。你的核心任务是对专利附图进行【分类审查】，并结合【说明书部件库】和【初筛OCR结果】，提取图片中真实存在的【附图标记】。

【任务1：专利图纸分类判断 (image_type)】
你必须先判断当前图片的类型：
1. structure: 机械/结构图纸。特征是有实物轮廓、剖面线、带【引出线】的数字标号。
2. flowchart_block: 流程图/系统框图。特征是包含矩形/菱形框、连接箭头，框内包含模块/步骤编号（如 S101, 100）。
3. chart_graph: 数据图表。特征是坐标轴(X/Y)、刻度数字、波形曲线、等高线、实验数据散点、柱状图等。
4. circuit: 电路原理图。特征是包含电气符号、电阻电容标号（如 R1, C2）。
5. other: 界面截图、化学式、无法归类的其他图纸。

【任务2：提取附图标记原则】
- 优先提取【说明书部件库】中出现过的标号并修正坐标。
- 【说明书部件库】是该专利的全局字典，而当前输入的单张图片通常【只包含其中的一小部分】。你必须基于视觉真实所见进行提取，如果图中根本没有某个标号，【绝对不要】为了迎合部件库而强行捏造或脑补它的坐标！
- 真正的物理零部件标记通常带有【引出线】（引出线形式包括：平滑曲线、折线、带实心/空心箭头的指示线）。请仔细甄别，不要将带箭头的零部件引出线误判为尺寸标注。
- 【严格排除（任何情况都不要提取）】：图名（如 FIG.1, 图2）、正文段落文字、尺寸标注（10mm, R5）、剖面线字母（A-A）、表示运动或视角方向的大箭头旁边的编号。
- 【极度危险警告】：如果你判断图纸类型为 `chart_graph`，则图中的坐标刻度（如 10, 100, 500）、等高线数值、时间轴数值【绝对不是附图标记】！此时你的 marks 数组必须输出为空 `[]`，不可产生AI 审查误报。
- 【终极核对】：在输出前，请务必再次全局扫描图片，特别是图形中央和线条密集区。
- 仅提取标号本身，不要带上中文名称。

【输出格式要求】：
仅输出合法的 JSON，不要带有 ```json 的 Markdown 包装。格式如下：
{{
    "reasoning": "请简要分析图片的视觉特征，判断它的分类，解释哪些是真正的标记，哪些是刻度或尺寸需被剔除。",
    "image_type": "structure|flowchart_block|chart_graph|circuit|other",
    "marks":[
        {{"text": "10", "box":[xmin, ymin, xmax, ymax]}}
    ]
}}

===================
【说明书部件库 (Ground Truth)】（动态注入内容，请牢记以下部件并在图中重点寻找）：
{parts_context}
"""

    @staticmethod
    def _normalize_pid(value: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        clean_text = re.sub(r"[^a-zA-Z0-9]", "", raw)
        return clean_text.lower()

    def _preprocess_for_ocr(self, img_path: str) -> Tuple[Optional[np.ndarray], float]:
        """OCR 专用预处理：智能放大 + 背景除法清洗"""
        if not os.path.exists(img_path):
            return None, 1.0

        img = cv2.imread(img_path)
        if img is None:
            return None, 1.0

        h, w = img.shape[:2]
        target_min_side = 1600
        det_max_side_limit = 4000
        min_side = min(h, w)
        max_side = max(h, w)

        if min_side < target_min_side:
            scale_factor = target_min_side / min_side
            scale_factor = min(scale_factor, 4.0)
        else:
            scale_factor = 1.0

        if max_side * scale_factor > det_max_side_limit:
            scale_factor = det_max_side_limit / max_side

        if scale_factor > 1.0:
            processed_img = cv2.resize(
                img, None, fx=scale_factor, fy=scale_factor, interpolation=cv2.INTER_LANCZOS4
            )
        else:
            processed_img = img.copy()

        if len(processed_img.shape) == 3:
            gray = cv2.cvtColor(processed_img, cv2.COLOR_BGR2GRAY)
        else:
            gray = processed_img

        dilated = cv2.dilate(gray, np.ones((25, 25), np.uint8))
        bg_blur = cv2.medianBlur(dilated, 21)

        diff = 255 - cv2.absdiff(gray, bg_blur)
        norm_img = cv2.normalize(diff, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX, dtype=cv2.CV_8UC1)
        _, result = cv2.threshold(norm_img, 230, 255, cv2.THRESH_TRUNC)
        final_result = cv2.normalize(result, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX, dtype=cv2.CV_8UC1)

        dark_ratio = float(np.mean(final_result < 170))
        if dark_ratio < 0.12:
            kernel = np.ones((2, 2), np.uint8)
            final_result = cv2.erode(final_result, kernel, iterations=1)

        return final_result, scale_factor

    def _expand_merged_ocr_results(self, results: List[Dict]) -> List[Dict]:
        expanded = []
        for item in results:
            text = item["text"]
            box = item["box"] 

            if re.search(r"[a-zA-Z0-9]+\s+[a-zA-Z0-9]+", text):
                parts = text.split()
                xmin, ymin, xmax, ymax = box
                total_width = xmax - xmin
                total_chars = len(text)
                current_char_idx = 0

                for part in parts:
                    if not part.strip():
                        continue
                    start_idx = text.find(part, current_char_idx)
                    if start_idx == -1:
                        continue
                    end_idx = start_idx + len(part)

                    new_xmin = xmin + int((start_idx / total_chars) * total_width)
                    new_xmax = xmin + int((end_idx / total_chars) * total_width)
                    new_box =[new_xmin, ymin, new_xmax, ymax]
                    expanded.append({"text": part, "box": new_box})
                    current_char_idx = end_idx
            else:
                expanded.append(item)
        return expanded

    def _run_local_ocr(self, img_path: str) -> List[Dict]:
        processed_img, scale = self._preprocess_for_ocr(img_path)
        if processed_img is None:
            logger.warning(f"[视觉] 图片预处理失败：{img_path}")
            return[]

        ocr_input = processed_img
        if len(ocr_input.shape) == 2:
            ocr_input = cv2.cvtColor(ocr_input, cv2.COLOR_GRAY2BGR)
        elif len(ocr_input.shape) == 3 and ocr_input.shape[2] == 4:
            ocr_input = cv2.cvtColor(ocr_input, cv2.COLOR_BGRA2BGR)

        try:
            # 串行执行无需强制抢占 Lock，但为保持健壮性依旧保留锁定
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

        formatted =[]
        for text, box in zip(texts, boxes):
            xmin, ymin, xmax, ymax = box
            xmin = max(0, int(xmin / scale))
            ymin = max(0, int(ymin / scale))
            xmax = int(xmax / scale)
            ymax = int(ymax / scale)
            formatted.append({"text": text, "box": [xmin, ymin, xmax, ymax]})

        return formatted

    def _run_online_ocr(self, img_path: str) -> List[Dict]:
        processed_img, scale = self._preprocess_for_ocr(img_path)
        if processed_img is None:
            return[]

        try:
            success, encoded_img = cv2.imencode(".jpg", processed_img)
            if not success:
                logger.error(f"[在线OCR] 图片编码失败：{img_path}")
                return[]

            file_data = base64.b64encode(encoded_img.tobytes()).decode("ascii")

            headers = {
                "Authorization": f"token {self.api_token}",
                "Content-Type": "application/json",
            }
            payload = {
                "file": file_data,
                "fileType": 1,
                "useDocOrientationClassify": False,
                "useDocUnwarping": False,
                "useTextlineOrientation": False,
                "textDetThresh": 0.2,
                "textDetBoxThresh": 0.3,
                "textDetUnclipRatio": 1.5,
                "textRecScoreThresh": 0.7,
            }

            response = requests.post(self.api_url, json=payload, headers=headers)
            response.raise_for_status()
            result_json = response.json()

            ocr_results = result_json.get("result", {}).get("ocrResults", [])
            ocr_result = ocr_results[0].get("prunedResult", {})

            texts = ocr_result.get("rec_texts",[])
            boxes = ocr_result.get("rec_boxes", [])

            formatted =[]
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
            return[]

    def _build_parts_context(self, max_chars: int = 7000) -> str:
        if not self.parts_db:
            return "（无部件上下文）"

        lines: List[str] =[]
        current_length = 0
        for pid, info in self.parts_db.items():
            if not isinstance(info, dict):
                continue
            name = str(info.get("name", "未知部件") or "未知部件").strip()
            function = str(info.get("function", "未提及") or "未提及").strip()
            hierarchy = str(info.get("hierarchy", "未提及") or "未提及").strip()
            spatial = str(info.get("spatial_connections", "未提及") or "未提及").strip()
            motion = str(info.get("motion_state", "未提及") or "未提及").strip()
            line = f"- 标号:{pid}; 名称:{name}; 功能:{function}; 层级:{hierarchy}; 空间连接:{spatial}; 运动状态:{motion}"

            next_length = current_length + len(line) + 1
            if next_length > max_chars:
                lines.append("- ... (parts_context 已截断)")
                break
            lines.append(line)
            current_length = next_length

        return "\n".join(lines) if lines else "（无部件上下文）"

    @staticmethod
    def _parse_vlm_response(content: str) -> Dict:
        text = str(content or "").strip()
        if not text:
            return {"image_type": "other", "marks":[]}

        cleaned = text.replace("```json", "").replace("```", "").strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and start < end:
            try:
                parsed = json.loads(cleaned[start : end + 1])
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        return {"image_type": "other", "reasoning": "JSON解析失败", "marks":[]}

    def _annotate_image(self, img_path: str, labels: list, output_path: str):
        try:
            processor = LabelPlacer(img_path)
            result_img = processor.place_labels(labels)
            cv2.imwrite(output_path, result_img)
        except Exception as e:
            logger.error(f"[视觉] 标注绘制失败：{img_path}，错误：{e}")
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
            draw.text((text_x, text_y), text, font=font, fill=(0, 0, 255))

            # 更新占用
            self.mark_existing_boxes([[x, y, x + text_w, y + text_h]])

        return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
