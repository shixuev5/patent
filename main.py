import json
import argparse
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from loguru import logger
from config import settings

# 引入各个处理模块
from src.search_clients.factory import SearchClientFactory
from src.parser import PDFParser
from src.transformer import PatentTransformer
from src.knowledge import KnowledgeExtractor
from src.vision import VisualProcessor
from src.checker import FormalExaminer
from src.generator import ContentGenerator
from src.graph.entrypoint import run_search_graph
from src.renderer import ReportRenderer


class PatentPipeline:
    """
    单条专利处理流水线。
    负责：下载 -> 解析 -> 提取 -> 检索 -> 生成报告
    """

    def __init__(self, pn: str):
        self.pn = pn.strip()
        # 获取基于 PN 的项目路径配置 (例如: output/CN123456)
        self.paths = settings.get_project_paths(self.pn)
        
        # 确保根目录存在
        self.paths["root"].mkdir(parents=True, exist_ok=True)
        self.paths["annotated_dir"].mkdir(exist_ok=True)
        
        # raw.pdf 的目标路径
        self.raw_pdf_path = self.paths["raw_pdf"]

    def run(self) -> dict:
        """
        执行完整的处理流程
        :return: 包含处理状态和结果路径的字典
        """
        logger.info(f"[{self.pn}] Starting pipeline processing...")
        
        try:
            # --- Step 0: 下载专利文件 ---
            self._step_download()

            # --- Step 1: 解析 PDF ---
            if not self.paths["raw_md"].exists():
                logger.info(f"[{self.pn}] Step 1: Parsing PDF...")
                # Mineru 解析 PDF
                PDFParser.parse(self.raw_pdf_path, self.paths["mineru_dir"])
            else:
                logger.info(f"[{self.pn}] Step 1: PDF already parsed, skipping.")
                
            md_content = self.paths["raw_md"].read_text(encoding="utf-8")

            # --- Step 2: 专利结构化转换 ---
            patent_data = {}
            if self.paths["patent_json"].exists():
                logger.info(f"[{self.pn}] Step 2: Loading patent JSON...")
                patent_data = json.loads(self.paths["patent_json"].read_text(encoding="utf-8"))
            else:
                logger.info(f"[{self.pn}] Step 2: Transforming MD to structured JSON...")
                transformer = PatentTransformer()
                patent_data = transformer.transform(md_content)
                self.paths["patent_json"].write_text(
                    json.dumps(patent_data, ensure_ascii=False, indent=2), encoding="utf-8"
                )

            # --- Step 3: 知识提取 ---
            parts_db = {}
            if self.paths["parts_json"].exists():
                logger.info(f"[{self.pn}] Step 3: Loading parts DB...")
                parts_db = json.loads(self.paths["parts_json"].read_text(encoding="utf-8"))
            else:
                logger.info(f"[{self.pn}] Step 3: Extracting knowledge...")
                extractor = KnowledgeExtractor()
                parts_db = extractor.extract_entities(patent_data)
                self.paths["parts_json"].write_text(
                    json.dumps(parts_db, ensure_ascii=False, indent=2), encoding="utf-8"
                )

            # --- Step 4: 视觉处理 (OCR + Annotate) ---
            image_parts = {}
            if self.paths["image_parts_json"].exists():
                logger.info(f"[{self.pn}] Step 4: Loading image parts json...")
                image_parts = json.loads(self.paths["image_parts_json"].read_text(encoding="utf-8"))
            else:
                logger.info(f"[{self.pn}] Step 4: Processing images...")
                processor = VisualProcessor(
                    patent_data=patent_data,
                    parts_db=parts_db,
                    raw_img_dir=self.paths["raw_images_dir"],
                    out_dir=self.paths["annotated_dir"],
                )
                image_parts = processor.process_patent_images()
                self.paths["image_parts_json"].write_text(
                    json.dumps(image_parts, ensure_ascii=False, indent=2), encoding="utf-8"
                )

            # --- Step 5: 形式缺陷检查 ---
            logger.info(f"[{self.pn}] Step 5: Running Formal Defect Check...")
            # 即使文件存在也重新跑检查，因为检查逻辑可能很快且需要最新状态
            examiner = FormalExaminer(parts_db=parts_db, image_parts=image_parts)
            check_result = examiner.check()
            self.paths["check_json"].write_text(
                json.dumps(check_result, ensure_ascii=False, indent=2), encoding="utf-8"
            )

            # --- Step 6: 报告内容生成 ---
            report_json = {}
            if self.paths["report_json"].exists():
                logger.info(f"[{self.pn}] Step 6: Loading report json...")
                report_json = json.loads(self.paths["report_json"].read_text(encoding="utf-8"))
            else:
                logger.info(f"[{self.pn}] Step 6: Generating report json...")
                cache_file = self.paths["root"].joinpath("report_intermediate.json")
                generator = ContentGenerator(
                    patent_data=patent_data,
                    parts_db=parts_db,
                    image_parts=image_parts,
                    annotated_dir=self.paths["annotated_dir"],
                    cache_file=cache_file,
                )
                report_json = generator.generate_report_json()
                self.paths["report_json"].write_text(
                    json.dumps(report_json, ensure_ascii=False, indent=2), encoding="utf-8"
                )

            # --- Step 7: 查新检索 (LangGraph) ---
            logger.info(f"[{self.pn}] Step 7: Running Graph Search...")
            search_strategy_data = {}
            examination_results = []
            
            # # 检查缓存
            # if self.paths["examination_results_json"].exists() and self.paths["search_strategy_json"].exists():
            #     logger.info(f"[{self.pn}] Loading cached search results...")
            #     search_strategy_data = json.loads(self.paths["search_strategy_json"].read_text(encoding="utf-8"))
            # else:
            #     search_strategy_data, examination_results = run_search_graph(
            #         patent_data=patent_data, report_data=report_json
            #     )
            #     self.paths["search_strategy_json"].write_text(
            #         json.dumps(search_strategy_data, ensure_ascii=False, indent=2), encoding="utf-8",
            #     )
            #     self.paths["examination_results_json"].write_text(
            #         json.dumps(examination_results, ensure_ascii=False, indent=2), encoding="utf-8",
            #     )

            # --- Step 8: 渲染 ---
            logger.info(f"[{self.pn}] Step 8: Rendering Report...")
            renderer = ReportRenderer(patent_data)
            renderer.render(
                report_data=report_json,
                check_result=check_result,
                search_data=search_strategy_data,
                md_path=self.paths["final_md"],
                pdf_path=self.paths["final_pdf"],
            )

            logger.success(f"[{self.pn}] Completed! Output: {self.paths['final_pdf']}")
            return {"status": "success", "pn": self.pn, "output": str(self.paths["final_pdf"])}

        except Exception as e:
            logger.exception(f"[{self.pn}] Pipeline failed: {str(e)}")
            return {"status": "failed", "pn": self.pn, "error": str(e)}

    def _step_download(self):
        """处理文件下载与归档"""
        if self.raw_pdf_path.exists():
            logger.info(f"[{self.pn}] Step 0: raw.pdf already exists in output dir, skipping download.")
            return

        logger.info(f"[{self.pn}] Step 0: Downloading patent document...")
        
        # 获取单例 Client
        client = SearchClientFactory.get_client("zhihuiya")
        
        # 直接下载到目标 output 目录
        success = client.download_patent_document(self.pn, str(self.raw_pdf_path))
        
        if not success:
            raise Exception(f"Download failed for {self.pn} (API returned error or empty path)")


def worker_wrapper(pn: str) -> dict:
    """线程池的包装函数，确保每个线程有独立的 Pipeline 实例"""
    pipeline = PatentPipeline(pn)
    return pipeline.run()


def main():
    parser = argparse.ArgumentParser(description="Patent Analysis Pipeline")
    parser.add_argument("--pn", type=str, help="Single PN or comma-separated PNs (e.g., CN116745575A,CN123)")
    parser.add_argument("--file", type=str, help="Path to a text file containing PNs (one per line)")
    parser.add_argument("--workers", type=int, default=1, help="Number of concurrent workers (default: 1)")
    
    args = parser.parse_args()

    # 1. 解析输入的 PN 列表
    pns = []
    if args.pn:
        # 支持逗号分隔
        parts = args.pn.replace("，", ",").split(",")
        pns.extend([p.strip() for p in parts if p.strip()])
    
    if args.file:
        file_path = Path(args.file)
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8")
            pns.extend([line.strip() for line in content.splitlines() if line.strip()])
        else:
            logger.error(f"File not found: {args.file}")
    
    # 去重
    pns = list(set(pns))
    
    if not pns:
        logger.warning("No PNs provided. Usage examples:")
        logger.warning("  python main.py --pn CN116745575A")
        logger.warning("  python main.py --file list.txt")
        return

    logger.info(f"Total patents to process: {len(pns)}")
    logger.info(f"Concurrency level: {args.workers}")

    # 2. 批量执行
    results = []
    start_time = time.time()

    # 使用线程池并发处理
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_pn = {executor.submit(worker_wrapper, pn): pn for pn in pns}
        
        for future in as_completed(future_to_pn):
            pn = future_to_pn[future]
            try:
                res = future.result()
                results.append(res)
                if res["status"] == "success":
                    logger.success(f"[{pn}] FINISHED")
                else:
                    logger.error(f"[{pn}] FAILED: {res.get('error')}")
            except Exception as exc:
                logger.error(f"[{pn}] Generated an exception: {exc}")
                results.append({"status": "failed", "pn": pn, "error": str(exc)})

    # 3. 统计结果
    duration = time.time() - start_time
    success_count = sum(1 for r in results if r["status"] == "success")
    
    logger.info("="*40)
    logger.info(f"Batch Processing Completed in {duration:.2f}s")
    logger.info(f"Total: {len(pns)}, Success: {success_count}, Failed: {len(pns) - success_count}")
    logger.info("="*40)


if __name__ == "__main__":
    main()