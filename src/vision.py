import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from paddleocr import PaddleOCR
from config import settings
from loguru import logger

# 全局初始化 OCR 模型，避免多线程中重复加载
# 注意：PaddleOCR 实例在多进程下可能需要放在函数内部，但在 ThreadPool 下全局即可
ocr_engine = PaddleOCR(
    text_detection_model_name="PP-OCRv5_server_det",
    text_recognition_model_name="PP-OCRv5_server_rec",
    use_doc_orientation_classify=False, # 通过 use_doc_orientation_classify 参数指定不使用文档方向分类模型
    use_doc_unwarping=False, # 通过 use_doc_unwarping 参数指定不使用文本图像矫正模型
    use_textline_orientation=False, # 通过 use_textline_orientation 参数指定不使用文本行方向分类模型
)

class VisualProcessor:
    
    @staticmethod
    def run_ocr(img_path: str):
        """运行 OCR 返回原始结果"""
        result = ocr_engine.predict(img_path)
        if not result or not result[0]:
            return []
        
        # 格式化输出: [{'text':Str, 'box': [x1,y1,x2,y2]}, ...]
        texts = result[0].get('rec_texts', [])
        boxes = result[0].get('rec_boxes', []).tolist()

        formatted = []
        for (text, box) in zip(texts, boxes):
            formatted.append({ 'text': text, 'box': box })
        return formatted

    @staticmethod
    def annotate_image(img_path: str, labels: list, output_path: str):
        """
        在图片上绘制标签
        labels: [{'text': '中文名', 'box': [x1,y1,x2,y2]}, ...]
        """
        processor = LabelPlacer(img_path)
        result_img = processor.place_labels(labels)
        cv2.imwrite(output_path, result_img)


class LabelPlacer:
    """保留你原有的避障绘图算法，稍作封装"""
    def __init__(self, image_path):
        self.original_img = cv2.imread(image_path)
        if self.original_img is None:
            raise ValueError(f"Image not found: {image_path}")
        self.h, self.w = self.original_img.shape[:2]
        
        # 构建代价地图
        gray = cv2.cvtColor(self.original_img, cv2.COLOR_BGR2GRAY)
        _, self.line_mask = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)
        kernel = np.ones((3,3), np.uint8)
        self.obstacle_map = cv2.dilate(self.line_mask, kernel, iterations=1)
        self.occupied_map = np.zeros((self.h, self.w), dtype=np.uint8)

    def mark_existing_boxes(self, boxes):
        for box in boxes:
            x1, y1, x2, y2 = map(int, box)
            # 边界保护
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(self.w, x2), min(self.h, y2)
            cv2.rectangle(self.occupied_map, (x1, y1), (x2, y2), 255, -1)
            cv2.rectangle(self.obstacle_map, (x1, y1), (x2, y2), 255, -1)

    def find_best_position(self, anchor_box, text_w, text_h):
        # ... (保留你原有的 find_best_position 逻辑) ...
        # 这里为了节省篇幅，假设完全复用你之前的贪婪算法代码
        ax1, ay1, ax2, ay2 = map(int, anchor_box)
        padding = 5
        candidates = [
            (ax2 + padding, ay1), # 右
            (ax1 - text_w - padding, ay1), # 左
            (ax1, ay2 + padding), # 下
            (ax1, ay1 - text_h - padding) # 上
        ]
        
        best_pos = None
        min_cost = float('inf')

        for x, y in candidates:
            x, y = int(x), int(y)
            if x < 0 or y < 0 or x + text_w > self.w or y + text_h > self.h:
                continue
            
            roi_occ = self.occupied_map[y:y+text_h, x:x+text_w]
            if np.count_nonzero(roi_occ) > 0: continue # 碰撞

            roi_obs = self.obstacle_map[y:y+text_h, x:x+text_w]
            cost = np.count_nonzero(roi_obs)
            
            if cost < min_cost:
                min_cost = cost
                best_pos = (x, y)
                if cost == 0: break
        
        return best_pos

    def place_labels(self, labels_data):
        # ... (保留你原有的绘图逻辑) ...
        img_pil = Image.fromarray(cv2.cvtColor(self.original_img, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(img_pil)
        
        try:
            font = ImageFont.truetype(str(settings.FONT_PATH), settings.FONT_SIZE)
        except OSError:
            logger.warning("Font file not found, using default.")
            font = ImageFont.load_default()

        # 1. 标记所有 OCR 框为障碍
        all_boxes = [item['box'] for item in labels_data]
        self.mark_existing_boxes(all_boxes)

        # 2. 放置标签
        for item in labels_data:
            text = item['text']
            box = item['box']
            bbox = font.getbbox(text)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            
            pos = self.find_best_position(box, w, h)
            if pos:
                x, y = pos
                # 绘制文字
                draw.text((x, y), text, font=font, fill=settings.LABEL_COLOR)
                # 更新占用
                cv2.rectangle(self.occupied_map, (x, y), (x+w, y+h), 255, -1)
        
        return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)