import json
from loguru import logger
from openai import OpenAI

from config import settings
from src.parser import PDFParser
from src.transformer import PatentTransformer
from src.knowledge import KnowledgeExtractor
from src.vision import VisualProcessor
from src.generator import ContentGenerator

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

    # --- Step 2: 专利结构化转换 ---
    patent_data = {}
    if paths["patent_json"].exists():
        logger.info("Step 2: Loading patent JSON...")
        patent_data = json.loads(paths["patent_json"].read_text(encoding="utf-8"))
    else:
        logger.info("Step 2: Transforming MD to structured JSON...")
        transformer = PatentTransformer(client)
        patent_data = transformer.transform(md_content)
        paths["patent_json"].write_text(json.dumps(patent_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- Step 3: 知识提取 ---
    parts_db = {}
    if paths["parts_json"].exists():
        logger.info("Step 3: Loading parts DB...")
        parts_db = json.loads(paths["parts_json"].read_text(encoding="utf-8"))
    else:
        logger.info("Step 3: Extracting knowledge...")
        extractor = KnowledgeExtractor(client)
        parts_db = extractor.extract_entities(patent_data)
        paths["parts_json"].write_text(json.dumps(parts_db, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- Step 4: 视觉处理 (OCR + Annotate) ---
    logger.info("Step 4: Processing images (OCR & Annotation)...")

    # 传入：专利数据(获取目标图片)、知识库(用于标注)、原始图片目录、输出目录
    image_meta = VisualProcessor.process_patent_images(
        patent_data=patent_data,
        parts_db=parts_db,
        raw_img_dir=paths["raw_images_dir"],
        out_dir=paths["annotated_dir"]
    )

    # --- Step 5: 内容生成与组装 ---
    logger.info("Step 5: Generating report...")
    generator = ContentGenerator(client, parts_db)
    summary_info = generator.generate_patent_summary(md_content, input_pdf.stem)
    clusters = generator.cluster_images(image_meta)
    generator.render_markdown(clusters, image_meta, paths["final_md"], paths["final_pdf"], summary_info=summary_info)
    
    logger.success(f"Pipeline Completed! Output: {paths['final_md']}")

if __name__ == "__main__":
    main()
