import json
from loguru import logger

from config import settings
from src.parser import PDFParser
from src.transformer import PatentTransformer
from src.knowledge import KnowledgeExtractor
from src.vision import VisualProcessor
from src.generator import ContentGenerator
from src.search import SearchStrategyGenerator
from src.renderer import ReportRenderer

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
        transformer = PatentTransformer()
        patent_data = transformer.transform(md_content)
        paths["patent_json"].write_text(json.dumps(patent_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- Step 3: 知识提取 ---
    parts_db = {}
    if paths["parts_json"].exists():
        logger.info("Step 3: Loading parts DB...")
        parts_db = json.loads(paths["parts_json"].read_text(encoding="utf-8"))
    else:
        logger.info("Step 3: Extracting knowledge...")
        extractor = KnowledgeExtractor()
        parts_db = extractor.extract_entities(patent_data)
        paths["parts_json"].write_text(json.dumps(parts_db, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- Step 4: 视觉处理 (OCR + Annotate) ---
    logger.info("Step 4: Processing images (OCR & Annotation)...")

    image_parts = {}
    if paths["image_parts_json"].exists():
        logger.info("Step 4: Loading image parts json...")
        image_parts = json.loads(paths["image_parts_json"].read_text(encoding="utf-8"))
    else:
        logger.info("Step 4: Generating image parts json...")
        # 传入：专利数据(获取目标图片)、知识库(用于标注)、原始图片目录、输出目录
        image_parts = VisualProcessor.process_patent_images(
            patent_data=patent_data,
            parts_db=parts_db,
            raw_img_dir=paths["raw_images_dir"],
            out_dir=paths["annotated_dir"]
        )
        paths["image_parts_json"].write_text(json.dumps(image_parts, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- Step 5: 内容生成 ---
    logger.info("Step 5: Generating report...")
    report_json = {}
    if paths["report_json"].exists():
        logger.info("Step 5: Loading report json...")
        report_json = json.loads(paths["report_json"].read_text(encoding="utf-8"))
    else:
        logger.info("Step 5: Generating report json...")
        generator = ContentGenerator(
            patent_data=patent_data, 
            parts_db=parts_db, 
            image_parts=image_parts,
            annotated_dir=paths["annotated_dir"]
        )
        report_json = generator.generate_report_json()
        paths["report_json"].write_text(json.dumps(report_json, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- Step 6: 检索策略生成 ---
    logger.info("Step 6: Generating Search Strategy & Queries...")
    search_json = {}
    if paths["search_strategy_json"].exists():
        logger.info("Step 6: Loading search strategy json...")
        search_json = json.loads(paths["search_strategy_json"].read_text(encoding="utf-8"))
    else:
        logger.info("Step 6: Generating search strategy json...")
        # 初始化生成器
        search_gen = SearchStrategyGenerator(patent_data, report_json)
        
        # 执行生成
        search_json = search_gen.generate_strategy()
        
        # 4. 写入独立的 JSON 文件
        paths["search_strategy_json"].write_text(
            json.dumps(search_json, ensure_ascii=False, indent=2), 
            encoding="utf-8"
        )

    # --- Step 7: 渲染 MD 和 PDF ---
    logger.info("Step 7: Rendering Report (MD & PDF)...")
    renderer = ReportRenderer(patent_data)
    renderer.render(
        report_data=report_json,
        search_data=search_json,
        md_path=paths["final_md"],
        pdf_path=paths["final_pdf"]
    )
    
    logger.success(f"Pipeline Completed! Output: {paths['final_pdf']}")

if __name__ == "__main__":
    main()
