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
from typing import List, Dict, Set, Tuple, Optional
from PIL import Image, ImageDraw, ImageFont
from paddleocr import PaddleOCR
from tqdm import tqdm
from config import settings
from loguru import logger
from src.utils.llm import get_llm_service


class VisualProcessor:
    """视觉处理核心类，负责 OCR、标注和图片批处理"""

    def __init__(
        self, patent_data: Dict, parts_db: Dict, raw_img_dir: Path, out_dir: Path
    ):
        """
        :param patent_data: 专利结构化数据 (JSON)
        :param parts_db: 零部件知识库
        :param raw_img_dir: 原始图片目录
        :param out_dir: 输出目录 (annotated_dir)
        """

        # 支持模式: 'local', 'vlm', 'online' (对应提供的 AI Studio 接口)
        self.engine_type = os.getenv("OCR_ENGINE", "local").lower()

        self.patent_data = patent_data
        self.parts_db = parts_db
        self.raw_img_dir = raw_img_dir
        self.out_dir = out_dir

        if self.engine_type == "local":
            logger.info("[Vision] Initializing local PaddleOCR engine...")
            self.ocr_engine = PaddleOCR(
                text_detection_model_name="PP-OCRv5_server_det",
                text_recognition_model_name="PP-OCRv5_server_rec",
                use_doc_orientation_classify=False,  # 不使用文档方向分类模型
                use_doc_unwarping=False,  # 不使用文本图像矫正模型
                use_textline_orientation=False,  # 不使用文本行方向分类模型
                text_rec_score_thresh=0.8,  # 识别置信度
                text_det_thresh=0.2,  # 文本检测像素阈值
                text_det_box_thresh=0.3,  # 文本检测框阈值
                text_det_unclip_ratio=2.0,  # 文本检测扩张系数
                text_det_limit_side_len=1216 # 增大图像尺寸
            )

        elif self.engine_type == "online":
            logger.info("[Vision] Using AI Studio Online OCR API.")
            self.api_url = settings.OCR_BASE_URL
            self.api_token = settings.OCR_API_KEY
            self.ocr_engine = None

        else:
            self.ocr_engine = None
            logger.info("[Vision] Using VLM (Vision Language Model) for OCR.")

    def process_patent_images(self) -> Dict[str, List[str]]:
        """
        处理专利图片的入口函数
        :return: image_parts 字典 {图片绝对路径: [包含的组件ID列表]}
        """
        # 1. 从 JSON 中提取需要处理的目标文件名集合
        target_filenames = self._extract_target_filenames()
        logger.info(f"[Vision] Found {len(target_filenames)} target images to analyze.")

        # 2. 准备结果容器
        image_parts = {}
        processed_files = set()

        # 3. 遍历原始目录下的所有图片
        if not self.raw_img_dir.exists():
            logger.warning(f"[Vision] Raw image dir not found: {self.raw_img_dir}")
            return {}

        all_images = list(self.raw_img_dir.glob("*.*"))

        for img_path in tqdm(all_images, desc="Processing Images"):
            filename = img_path.name
            out_path = self.out_dir / filename

            # 如果部件数据存在并且是目标图片 (摘要图或附图)
            if self.parts_db and filename in target_filenames:
                part_ids = self._process_single_image(img_path, out_path)
                if part_ids:
                    image_parts[filename] = part_ids
                processed_files.add(filename)
            else:
                # 非目标图片，直接拷贝
                shutil.copy2(img_path, out_path)

        return image_parts

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

    def _process_single_image(self, img_path: Path, out_path: Path) -> List[str]:
        """对单张图片进行 OCR 并根据 parts_db 进行标注"""
        try:
            # 根据引擎分发任务
            if self.engine_type == "local":
                ocr_results = self._run_local_ocr(str(img_path))
            elif self.engine_type == "online":
                ocr_results = self._run_online_ocr(str(img_path))
            else:
                ocr_results = self._run_vlm_ocr(str(img_path))

            # 修复粘连：拆分 "101 106" 这种结果
            ocr_results = self._expand_merged_ocr_results(ocr_results)

            valid_labels = []
            found_pids = []

            for item in ocr_results:
                text = item["text"]

                # 步骤 A: 正则清洗，只保留 数字 和 字母 (移除标点、括号等干扰)
                # 例如: "(16a)" -> "16a", "10." -> "10", "11-A" -> "11A"
                clean_text = re.sub(r"[^a-zA-Z0-9]", "", text)

                # 步骤 B: 强制转小写（既然 parts_db key 不包含大写，这里统一转小写以兼容图纸上的大写标注）
                # 例如: "11A" -> "11a"
                match_key = clean_text.lower()

                # 情况 1: 在零部件库中找到了 (用于生成标注图 + 记录存在性)
                if match_key in self.parts_db:
                    valid_labels.append(
                        {
                            "text": self.parts_db[match_key]["name"],
                            "box": item["box"],
                        }
                    )
                    found_pids.append(match_key)

                # 情况 2: 库里没有，但是格式非常像一个部件编号 (用于形式审查报错)
                # 只有当它"长得像编号"且之前没添加过时才记录
                elif self._is_potential_part_id(match_key):
                    # 注意：这里不加入 valid_labels，因为没有中文名，画在图上没意义
                    # 但是要加入 found_pids，这样 FormalExaminer 就会发现它在 parts_db 里不存在
                    found_pids.append(match_key)

            # 3. 绘图或复制
            # 如果有有效标注，或者至少识别出了一些东西（避免空图被覆盖），则进行标注
            if len(valid_labels) > 0:
                self._annotate_image(str(img_path), valid_labels, str(out_path))
            else:
                # 无有效信息，复制原图
                shutil.copy2(img_path, out_path)

            return found_pids

        except Exception as e:
            logger.error(f"[Vision] Failed to process {img_path.name}: {e}")
            # 出错兜底：复制原图
            if img_path.exists():
                shutil.copy2(img_path, out_path)
            return []

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
        if text.startswith('0'):
            return False

        # --- 规则 3: 长度过滤 ---
        # 附图标记通常很短，如 "1", "10", "102", "12a"
        if len(text) > 6:
            return False

        # --- 规则 4: 排除明显的单位或序数词 ---
        # text 已经是小写且去除了标点
        
        # 常见物理单位 (排除 mm, cm, kg, hz 等)
        if text.endswith(('mm', 'cm', 'km', 'kg', 'hz', 'kv', 'mg', 'ml')):
            return False
            
        # 常见序数词缩写 (1st, 2nd, 3rd, 4th)
        if text.endswith(('st', 'nd', 'rd', 'th')):
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
        min_side = min(h, w)

        if min_side < target_min_side:
            scale_factor = target_min_side / min_side
            scale_factor = min(scale_factor, 4.0)  # 限制最大 4 倍
        else:
            scale_factor = 1.0

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

        # # A. 微量加粗 (Erosion)
        # # 注意：在白底黑字中，Erosion(腐蚀) 会让黑色区域变大(加粗)，Dilation(膨胀) 会让黑色区域变小(变细)
        # # 我们使用一个极小的 kernel (2x2) 进行一次腐蚀，让细断的笔画连起来
        # kernel = np.ones((2, 2), np.uint8)
        # result = cv2.erode(result, kernel, iterations=1)

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
            logger.warning(f"Failed to preprocess image: {img_path}")
            return []

        try:
            result = self.ocr_engine.predict(processed_img)
        except Exception as e:
            logger.error(f"PaddleOCR inference failed: {e}")
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
                logger.error(f"[OnlineOCR] Failed to encode image: {img_path}")
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
                "textDetUnclipRatio": 2.0,
                "textRecScoreThresh": 0.8,
                "textDetLimitSideLen": 1216
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
            logger.error(f"[OnlineOCR] Request failed for {Path(img_path).name}: {e}")
            return []

    def _run_vlm_ocr(self, img_path: str) -> List[Dict]:
        """使用视觉模型进行高精度专利附图标记识别"""
        try:
            llm_service = get_llm_service()

            # 读取图片尺寸用于坐标反归一化
            img = cv2.imread(img_path)
            if img is None:
                logger.error(f"[VLM] Failed to read image: {img_path}")
                return []
            h, w = img.shape[:2]

            system_prompt = """
            你是一个资深的专利附图审查专家。你的唯一任务是提取专利技术图纸中的"附图标记"(Reference Numerals)。
            
            ### 核心定义
            附图标记是用于标识图纸中零部件的数字或字母数字组合（如 "10", "205", "14a", "12B"）。
            
            ### 视觉识别策略
            1. **跟随引线 (Critical)**：绝大多数附图标记都连接着一条细线（引线/指引线）指向零件。请务必追踪所有引线的末端。
            2. **扫描孤立字符**：部分标记可能位于零件内部或圆圈/方框内，没有引线。
            3. **区分尺寸标注**：忽略带有双向箭头、尺寸界线或表示长度/角度的数字（这些是尺寸，不是部件编号）。
            
            ### 输出规范
            请输出一个纯 JSON 列表。
            - **坐标系**：使用 [0, 1000] 的归一化坐标系（左上角为0,0，右下角为1000,1000）。
            - **Box 格式**：[xmin, ymin, xmax, ymax]。
            - **Box 要求**：
                - **极度紧密 (Tight Fit)**：检测框必须紧贴字符边缘，**严禁**包含引线、箭头或周围空白。
                - **分离重叠**：如果 "10" 和 "11" 靠得很近但含义独立，请分别输出两个框，不要合并。
            - **Text 清洗**：
                - 去除非字母数字字符（如括号 "()"、点 "."）。
                - 统一转换为小写。
            
            ### JSON 格式示例
            ```json
            [
              {"text": "10", "box": [100, 200, 150, 250]},
              {"text": "15a", "box": [300, 400, 380, 420]}
            ]
            ```
            """

            user_prompt = f"""
            请对这张图片进行详尽的扫描，找出所有的附图标记。
            图片分辨率：{w}x{h}
            
            请特别注意：
            1. **不遗漏**：图中可能有几十个编号，请仔细检查每一个角落，特别是密集的区域。
            2. **排除干扰**：不要识别图名（如 "FIG. 1"）、页码或底部的说明文字。
            3. **准确性**：确保 bounding box 没有切断数字，也没有包含连接它的线条。
            
            请直接返回 JSON 数据，不要包含任何 Markdown 格式或解释性语言。
            """

            content = llm_service.analyze_image_with_thinking(
                img_path, system_prompt, user_prompt
            )

            content = content.replace("```json", "").replace("```", "").strip()

            try:
                data = json.loads(content)
            except json.JSONDecodeError as e:
                logger.error(
                    f"[VLM] JSON parse error: {e}. Raw content snippet: {content[:100]}..."
                )
                return []

            formatted = []
            for item in data:
                norm_box = item.get("box", [])

                if len(norm_box) == 4:
                    x_coords = [norm_box[0], norm_box[2]]
                    y_coords = [norm_box[1], norm_box[3]]

                    x1 = int(min(x_coords) / 1000 * w)
                    x2 = int(max(x_coords) / 1000 * w)
                    y1 = int(min(y_coords) / 1000 * h)
                    y2 = int(max(y_coords) / 1000 * h)

                    # 边界安全检查
                    x1, y1 = max(0, x1), max(0, y1)
                    x2, y2 = min(w, x2), min(h, y2)

                    # 过滤无效框
                    if x2 <= x1 or y2 <= y1:
                        continue

                    formatted.append(
                        {"text": str(item.get("text", "")), "box": [x1, y1, x2, y2]}
                    )

            logger.info(
                f"[VLM] Successfully extracted {len(formatted)} labels from {Path(img_path).name}"
            )
            return formatted

        except Exception as e:
            logger.error(f"[VLM] Error processing {Path(img_path).name}: {e}")
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
            logger.error(f"Annotation failed for {img_path}: {e}")
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
