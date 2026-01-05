import json
import shutil
from pathlib import Path
from loguru import logger
from openai import OpenAI
from tqdm import tqdm

from config import settings
from src.parser import PDFParser
from src.knowledge import KnowledgeExtractor
from src.vision import VisualProcessor
from src.generator import ContentGenerator

def process_single_image(img_path: Path, annotated_dir: Path, parts_db: dict):
    """单个图片的 OCR + 标注 任务"""
    try:
        # 1. OCR 识别
        ocr_results = VisualProcessor.run_ocr(str(img_path))
        
        # 2. 匹配知识库
        valid_labels = []
        found_pids = []
        
        for item in ocr_results:
            text = item['text']
            # 简单清洗：只留数字
            clean_text = "".join(filter(str.isdigit, text))
            
            if clean_text in parts_db:
                # 准备标注数据：替换 OCR 文本为 组件名
                valid_labels.append({
                    'text': parts_db[clean_text]['name'],
                    'box': item['box']
                })
                found_pids.append(clean_text)
        
        # 3. 绘图或复制
        out_filename = f"annotated_{img_path.name}"
        out_path = annotated_dir / out_filename
        
        if valid_labels:
            VisualProcessor.annotate_image(str(img_path), valid_labels, str(out_path))
            return str(out_path), found_pids
        else:
            # 无有效信息，复制原图
            shutil.copy(img_path, out_path)
            return str(out_path), []

    except Exception as e:
        logger.error(f"Image process failed {img_path}: {e}")
        return None, []

def main():
    # 0. 检查输入
    input_pdf = next(settings.INPUT_DIR.glob("*.pdf"), None)
    if not input_pdf:
        logger.error("input 目录下未找到 PDF 文件")
        return

    # 初始化项目路径
    paths = settings.get_project_paths(input_pdf.stem)
    paths["root"].mkdir(parents=True, exist_ok=True)
    paths["annotated_dir"].mkdir(exist_ok=True)
    
    # 初始化 AI 客户端
    client = OpenAI(api_key=settings.LLM_API_KEY, base_url=settings.LLM_BASE_URL)

    # --- Step 1: 解析 PDF ---
    if not paths["raw_md"].exists():
        logger.info("Step 1: Parsing PDF...")
        # Mineru 会自动创建父级目录
        PDFParser.parse(input_pdf, paths["mineru_dir"])
    else:
        logger.info("Step 1: PDF already parsed, skipping.")

    if not paths["raw_md"].exists():
        logger.error("Markdown file not found. Pipeline stopped.")
        return
    
    md_content = paths["raw_md"].read_text(encoding="utf-8")

    # --- Step 2: 知识提取 ---
    parts_db = {}
    if paths["parts_json"].exists():
        logger.info("Step 2: Loading parts DB...")
        parts_db = json.loads(paths["parts_json"].read_text(encoding="utf-8"))
    else:
        logger.info("Step 2: Extracting knowledge...")
        extractor = KnowledgeExtractor(client)
        parts_db = extractor.extract_entities(md_content)
        paths["parts_json"].write_text(json.dumps(parts_db, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- Step 3: 视觉处理 (OCR + Annotate) ---
    logger.info("Step 3: Processing images...")
    image_files = list(paths["raw_images_dir"].glob("*.*"))
    image_meta = {}

    for img_path in tqdm(image_files, desc="OCR Processing"):
        # 直接调用处理函数
        res = process_single_image(img_path, paths["annotated_dir"], parts_db)
        
        if res and res[0]:
            abs_path, pids = res
            # 记录结果
            image_meta[abs_path] = pids

    # --- Step 4: 内容生成与组装 ---
    logger.info("Step 4: Generating report...")
    generator = ContentGenerator(client, parts_db)
    summary_info = generator.generate_patent_summary(md_content, input_pdf.stem)
    clusters = generator.cluster_images(image_meta)
    generator.render_markdown(clusters, image_meta, paths["final_md"], paths["final_pdf"], summary_info=summary_info)
    
    logger.success(f"Pipeline Completed! Output: {paths['final_md']}")

if __name__ == "__main__":
    main()