import cv2
import re
import os
import json
import shutil
import numpy as np
from pathlib import Path
from typing import List, Dict, Set
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
                lang="ch",
                use_doc_orientation_classify=False,  # 通过 use_doc_orientation_classify 参数指定不使用文档方向分类模型
                use_doc_unwarping=False,  # 通过 use_doc_unwarping 参数指定不使用文本图像矫正模型
                use_textline_orientation=False,  # 通过 use_textline_orientation 参数指定不使用文本行方向分类模型
            )
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

            # 如果是目标图片 (摘要图或附图)
            if filename in target_filenames:
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
            # 1. OCR 识别
            ocr_results = (
                self._run_local_ocr(str(img_path))
                if self.engine_type == "local"
                else self._run_vlm_ocr(str(img_path))
            )

            # 2. 匹配知识库
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

                if match_key in self.parts_db:
                    # 准备标注数据：替换 OCR 文本为 组件名
                    valid_labels.append(
                        {
                            "text": self.parts_db[match_key][
                                "name"
                            ],  # 使用组件名称标注
                            "box": item["box"],
                        }
                    )
                    found_pids.append(match_key)

            # 3. 绘图或复制
            if len(ocr_results) and len(valid_labels) / len(ocr_results) > 0.3:
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

    def _run_local_ocr(self, img_path: str) -> List[Dict]:
        """运行 OCR 返回原始结果"""
        result = self.ocr_engine.predict(img_path, text_rec_score_thresh=0.9)

        if not result or not result[0]:
            return []

        # 格式化输出: [{'text':Str, 'box': [x1,y1,x2,y2]}, ...]
        texts = result[0].get("rec_texts", [])
        boxes = result[0].get("rec_boxes", []).tolist()

        formatted = []
        for text, box in zip(texts, boxes):
            formatted.append({"text": text, "box": box})

        return formatted

    def _run_vlm_ocr(self, img_path: str) -> List[Dict]:
        """使用视觉模型"""
        try:
            llm_service = get_llm_service()

            # 读取图片尺寸用于坐标反归一化
            img = cv2.imread(img_path)
            if img is None:
                logger.error(f"[VLM] Failed to read image: {img_path}")
                return []
            h, w = img.shape[:2]

            prompt = """
            任务：找出图片中所有的零部件编号（数字）。
            
            要求：
            1. 返回纯 JSON 列表。
            2. 格式：[{"text": "数字内容", "box": [ymin, xmin, ymax, xmax]}]。
            3. 坐标系：使用 0-1000 的归一化坐标。
            4. 精度要求：box 必须【紧致地包裹】数字像素，不要包含过多空白背景。
            5. 如果数字周围有引线，box 不要包含引线，只包含数字本身。
            6. "box" 顺序：注意通常是 [xmin, ymin, xmax, ymax]。
            
            返回示例：
            [{"text": "10", "box": [100, 100, 150, 120]}]
            """

            content = llm_service.analyze_image_with_thinking(img_path, prompt)

            content = content.replace("```json", "").replace("```", "").strip()

            data = json.loads(content)

            formatted = []
            for item in data:
                norm_box = item.get("box", [])
                if len(norm_box) == 4:
                    x1 = int(norm_box[0] / 1000 * w)
                    y1 = int(norm_box[1] / 1000 * h)
                    x2 = int(norm_box[2] / 1000 * w)
                    y2 = int(norm_box[3] / 1000 * h)

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
        processor = LabelPlacer(img_path)
        result_img = processor.place_labels(labels)
        cv2.imwrite(output_path, result_img)


class LabelPlacer:
    """避障标签放置算法"""

    def __init__(self, image_path):
        # 解决中文路径读取问题
        self.original_img = cv2.imdecode(np.fromfile(image_path, dtype=np.uint8), -1)
        if self.original_img is None:
            raise ValueError(f"Image not found: {image_path}")

        # 处理可能的 Alpha 通道
        if self.original_img.shape[2] == 4:
            self.original_img = cv2.cvtColor(self.original_img, cv2.COLOR_BGRA2BGR)

        self.h, self.w = self.original_img.shape[:2]

        # 构建代价地图
        gray = cv2.cvtColor(self.original_img, cv2.COLOR_BGR2GRAY)
        _, self.line_mask = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
        kernel = np.ones((3, 3), np.uint8)
        self.obstacle_map = cv2.dilate(self.line_mask, kernel, iterations=1)
        self.occupied_map = np.zeros((self.h, self.w), dtype=np.uint8)

    def mark_existing_boxes(self, boxes):
        for box in boxes:
            x1, y1, x2, y2 = map(int, box)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(self.w, x2), min(self.h, y2)
            # 标记为完全不可用
            cv2.rectangle(self.occupied_map, (x1, y1), (x2, y2), 255, -1)
            cv2.rectangle(self.obstacle_map, (x1, y1), (x2, y2), 255, -1)

    def find_best_position(self, anchor_box, text_w, text_h):
        ax1, ay1, ax2, ay2 = map(int, anchor_box)
        padding = 5
        # 候选位置：右、左、下、上
        candidates = [
            (ax2 + padding, ay1),
            (ax1 - text_w - padding, ay1),
            (ax1, ay2 + padding),
            (ax1, ay1 - text_h - padding),
        ]

        best_pos = None
        min_cost = float("inf")

        for x, y in candidates:
            x, y = int(x), int(y)
            # 越界检查
            if x < 0 or y < 0 or x + text_w > self.w or y + text_h > self.h:
                continue

            # 碰撞检查（是否与其他标签重叠）
            roi_occ = self.occupied_map[y : y + text_h, x : x + text_w]
            if np.count_nonzero(roi_occ) > 0:
                continue

            # 遮挡检查（是否遮挡了原图的线条）
            roi_obs = self.obstacle_map[y : y + text_h, x : x + text_w]
            cost = np.count_nonzero(roi_obs)

            if cost < min_cost:
                min_cost = cost
                best_pos = (x, y)
                if cost == 0:
                    break  # 找到完美位置，提前退出

        return best_pos

    def place_labels(self, labels_data):
        img_pil = Image.fromarray(cv2.cvtColor(self.original_img, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(img_pil)

        try:
            font = ImageFont.truetype(str(settings.FONT_PATH), settings.FONT_SIZE)
        except Exception:
            # logger.warning("Font file error, using default.")
            font = ImageFont.load_default()

        # 1. 标记所有 OCR 识别框区域为不可用（避免把字写在原本的数字上）
        all_boxes = [item["box"] for item in labels_data]
        self.mark_existing_boxes(all_boxes)

        # 2. 放置标签
        for item in labels_data:
            text = item["text"]
            box = item["box"]

            # 获取文本尺寸
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]

            pos = self.find_best_position(box, text_w, text_h)

            if pos:
                x, y = pos
                # 绘制背景框 (可选，增加可读性)
                draw.rectangle(
                    (x, y, x + text_w, y + text_h), fill="white", outline=None
                )
                # 绘制文字
                draw.text((x, y), text, font=font, fill=settings.LABEL_COLOR)
                # 更新占用地图
                self_box = [x, y, x + text_w, y + text_h]
                self.mark_existing_boxes([self_box])
            else:
                # 找不到位置时，可以选择不做任何事，或者强制绘制在某个位置
                pass

        return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
