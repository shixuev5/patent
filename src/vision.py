import cv2
import re
import os
import json
import shutil
import math
import numpy as np
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
                text_det_thresh=0.1, # 文本检测像素阈值
                text_det_box_thresh=0.2, # 文本检测框阈值
                text_det_unclip_ratio=2.0 # 文本检测扩张系数
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
    
    def _run_local_ocr(self, img_path: str) -> List[Dict]:
        """运行 OCR 返回原始结果"""
        
        result = self.ocr_engine.predict(img_path)

        if not result or not result[0]:
            return []

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

            system_prompt = """
            你是一个专业的机械图纸分析专家，专门识别专利图中的零部件编号。
            
            任务要求：
            1. 识别图中所有的零部件编号（通常为数字或数字+字母的组合）
            2. 零部件编号特征：
                - 通常位于引线端点处
                - 大小适中，与图中的尺寸标注有明显区别
                - 常见格式：纯数字（如"10", "23"）、数字+小写字母（如"11a", "16b"）
                - 可能包含罗马数字或带括号，但输出时要去除非字母数字字符
            3. 排除内容：
                - 图纸标题、页码、图号（如"FIG.1"、"图2"）
                - 尺寸标注（通常带箭头或尺寸线）
                - 技术说明文字、表格内容
                - 阴影线、剖面线等图形元素
            
            输出格式要求：
            1. 返回纯JSON列表，格式：[
                {
                    "text": "清理后的编号字符串", 
                    "box": [x_min, y_min, x_max, y_max]
                },
                ...
            ]
            2. 坐标系：归一化到0-1000的坐标系（原图宽度对应1000，高度按比例）
            3. Bounding Box要求：
                - 紧贴数字边界，不留过多空白
                - 只包含数字本身，不包含引线、箭头等
                - 顺序：[x_min, y_min, x_max, y_max]（左上角到右下角）
            4. 文本处理：
                - 去除所有标点符号、括号、空格
                - 统一转为小写字母
                - 例如："(16a)" -> "16a"，"10." -> "10"，"11-A" -> "11a"
            
            质量控制：
                1. 每个检测框必须包含有效的零部件编号
                2. 数字字符应清晰可辨
                3. 避免重复检测同一编号
                4. 如果遇到模糊或不确定的编号，跳过不检测
            
            示例输出：
            [
                {"text": "10", "box": [245, 120, 265, 140]},
                {"text": "11a", "box": [320, 280, 345, 300]},
                {"text": "23", "box": [510, 410, 530, 430]}
            ]
            """
            
            user_prompt = f"""
            请分析这张专利图纸，识别图中所有用于标识零部件的编号。
            
            图片信息：宽度{w}像素，高度{h}像素
            
            重点关注：
            1. 引线末端的小数字
            2. 圆圈或方框内的编号
            3. 零件剖面图中的标识符
            4. 装配图中的组件序号
            
            忽略：
            1. 图纸边框外的文字
            2. 比例尺、单位标注
            3. 材料说明、技术要求
            4. 视图方向的指示（如"前视图"、"A-A剖面"）
            
            请严格按照要求的JSON格式返回结果，确保box坐标准确且文本已清理。
            """

            content = llm_service.analyze_image_with_thinking(img_path, system_prompt, user_prompt)

            content = content.replace("```json", "").replace("```", "").strip()

            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                # 简单的尝试修复或忽略
                logger.warning(f"[VLM] Failed to parse JSON response for {img_path}")
                return []

            formatted = []
            for item in data:
                norm_box = item.get("box", [])
                if len(norm_box) == 4:
                    # 坐标转换并确保为整数
                    x1 = int(norm_box[0] / 1000 * w)
                    y1 = int(norm_box[1] / 1000 * h)
                    x2 = int(norm_box[2] / 1000 * w)
                    y2 = int(norm_box[3] / 1000 * h)
                    
                    xmin, xmax = sorted([x1, x2])
                    ymin, ymax = sorted([y1, y2])

                    formatted.append(
                        {"text": str(item.get("text", "")), "box": [xmin, ymin, xmax, ymax]}
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
