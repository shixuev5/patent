import json
from loguru import logger

from config import settings
from src.parser import PDFParser
from src.transformer import PatentTransformer
from src.knowledge import KnowledgeExtractor
from src.vision import VisualProcessor
from src.checker import FormalExaminer
from src.generator import ContentGenerator
from src.graph.entrypoint import run_search_graph
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
        paths["patent_json"].write_text(
            json.dumps(patent_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # --- Step 3: 知识提取 ---
    parts_db = {}
    if paths["parts_json"].exists():
        logger.info("Step 3: Loading parts DB...")
        parts_db = json.loads(paths["parts_json"].read_text(encoding="utf-8"))
    else:
        logger.info("Step 3: Extracting knowledge...")
        extractor = KnowledgeExtractor()
        parts_db = extractor.extract_entities(patent_data)
        paths["parts_json"].write_text(
            json.dumps(parts_db, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # --- Step 4: 视觉处理 (OCR + Annotate) ---
    logger.info("Step 4: Processing images (OCR & Annotation)...")

    image_parts = {}
    if paths["image_parts_json"].exists():
        logger.info("Step 4: Loading image parts json...")
        image_parts = json.loads(paths["image_parts_json"].read_text(encoding="utf-8"))
    else:
        logger.info("Step 4: Generating image parts json...")
        # 传入：专利数据(获取目标图片)、知识库(用于标注)、原始图片目录、输出目录
        processor = VisualProcessor(
            patent_data=patent_data,
            parts_db=parts_db,
            raw_img_dir=paths["raw_images_dir"],
            out_dir=paths["annotated_dir"],
        )
        image_parts = processor.process_patent_images()
        paths["image_parts_json"].write_text(
            json.dumps(image_parts, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # --- Step 5: 内容生成 ---
    logger.info("Step 5: Running Formal Defect Check...")
    
    examiner = FormalExaminer(parts_db=parts_db, image_parts=image_parts)
    
    # 获取检查结果字典
    check_result = examiner.check()

    paths["check_json"].write_text(json.dumps(check_result, ensure_ascii=False, indent=2), encoding="utf-8")

    # --- Step 6: 内容生成 ---
    logger.info("Step 6: Generating report...")
    report_json = {}
    if paths["report_json"].exists():
        logger.info("Step 6: Loading report json...")
        report_json = json.loads(paths["report_json"].read_text(encoding="utf-8"))
    else:
        logger.info("Step 6: Generating report json...")

        cache_file = paths["root"].joinpath("report_intermediate.json")
        generator = ContentGenerator(
            patent_data=patent_data,
            parts_db=parts_db,
            image_parts=image_parts,
            annotated_dir=paths["annotated_dir"],
            cache_file=cache_file,
        )
        report_json = generator.generate_report_json()
        paths["report_json"].write_text(
            json.dumps(report_json, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # --- Step 7: 查新检索 ---
    logger.info("Step 7: Running LangGraph Search Agent...")

    search_strategy_data = {}
    examination_results = []

    if (
        paths["examination_results_json"].exists()
        and paths["search_strategy_json"].exists()
    ):
        logger.info("Loading cached Graph results...")
        search_strategy_data = json.loads(
            paths["search_strategy_json"].read_text(encoding="utf-8")
        )
        examination_results = json.loads(
            paths["examination_results_json"].read_text(encoding="utf-8")
        )
    else:
        # 调用图入口
        search_strategy_data, examination_results = run_search_graph(
            patent_data=patent_data, report_data=report_json
        )

        # 保存结果 (保持与旧流程兼容的文件结构)
        paths["search_strategy_json"].write_text(
            json.dumps(search_strategy_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        paths["examination_results_json"].write_text(
            json.dumps(examination_results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    logger.success(f"Agent finished. Found {len(examination_results)} relevant docs.")

    # --- Step 8: 渲染 MD 和 PDF ---
    logger.info("Step 8: Rendering Report (MD & PDF)...")
    renderer = ReportRenderer(patent_data)
    renderer.render(
        report_data=report_json,
        check_result=check_result,
        search_data=search_strategy_data,
        md_path=paths["final_md"],
        pdf_path=paths["final_pdf"],
    )

    logger.success(f"Pipeline Completed! Output: {paths['final_pdf']}")


if __name__ == "__main__":
    main()
