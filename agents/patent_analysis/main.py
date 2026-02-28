import json
import argparse
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Event
from typing import Optional

from loguru import logger
from config import settings

# 引入各个处理模块
from agents.patent_analysis.src.search_clients.factory import SearchClientFactory
from agents.patent_analysis.src.parser import PDFParser
from agents.patent_analysis.src.transformer import PatentTransformer
from agents.patent_analysis.src.knowledge import KnowledgeExtractor
from agents.patent_analysis.src.vision import VisualProcessor
from agents.patent_analysis.src.checker import FormalExaminer
from agents.patent_analysis.src.generator import ContentGenerator
from agents.patent_analysis.src.search import SearchStrategyGenerator
from agents.patent_analysis.src.renderer import ReportRenderer


class PipelineCancelled(RuntimeError):
    pass


class PatentPipeline:
    """
    单条专利处理流水线。
    负责：下载 -> 解析 -> 提取 -> 检索 -> 生成报告
    """

    def __init__(self, pn: str, upload_file_path: str = None, cancel_event: Optional[Event] = None, task_id: str = None):
        self.pn = pn.strip()
        self.upload_file_path = upload_file_path
        self.cancel_event = cancel_event
        self.task_id = task_id
        # 获取基于 PN 的项目路径配置 (例如: output/CN123456)
        self.paths = settings.get_project_paths(self.pn)

        # 确保根目录存在
        self.paths["root"].mkdir(parents=True, exist_ok=True)
        self.paths["annotated_dir"].mkdir(exist_ok=True)

        # raw.pdf 的目标路径
        self.raw_pdf_path = self.paths["raw_pdf"]

        # 初始化任务管理器
        if self.task_id:
            from agents.patent_analysis.src.storage import get_pipeline_manager
            self.task_manager = get_pipeline_manager()

    def _check_cancelled(self):
        if self.cancel_event and self.cancel_event.is_set():
            raise PipelineCancelled("任务已取消")

    def _update_step_status(self, step_name: str, status: str):
        """更新任务步骤状态"""
        if self.task_id and self.task_manager:
            # 根据 DEFAULT_PIPELINE_STEPS 找到对应的中文步骤名
            from agents.patent_analysis.src.storage import DEFAULT_PIPELINE_STEPS
            step_name_map = {en: zh for en, zh in DEFAULT_PIPELINE_STEPS}
            chinese_step_name = step_name_map.get(step_name, step_name)
            self.task_manager.update_progress(self.task_id, 0, chinese_step_name, step_status=status)

    def run(self) -> dict:
        """
        执行完整的处理流程
        :return: 包含处理状态和结果路径的字典
        """
        logger.info(f"[{self.pn}] Starting pipeline processing...")

        try:
            self._check_cancelled()
            # --- Step 0: 下载专利文件 ---
            self._update_step_status("download", "processing")
            self._step_download()
            self._update_step_status("download", "completed")
            self._check_cancelled()

            # --- Step 1: 解析 PDF ---
            self._update_step_status("parse", "processing")
            if not self.paths["raw_md"].exists():
                logger.info(f"[{self.pn}] Step 1: Parsing PDF...")
                # Mineru 解析 PDF
                PDFParser.parse(self.raw_pdf_path, self.paths["mineru_dir"])
            else:
                logger.info(f"[{self.pn}] Step 1: PDF already parsed, skipping.")

            md_content = self.paths["raw_md"].read_text(encoding="utf-8")
            self._update_step_status("parse", "completed")
            self._check_cancelled()

            # --- Step 2: 专利结构化转换 ---
            self._update_step_status("transform", "processing")
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
            self._update_step_status("transform", "completed")
            self._check_cancelled()

            # --- Step 3: 知识提取 ---
            self._update_step_status("extract", "processing")
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
            self._update_step_status("extract", "completed")
            self._check_cancelled()

            # --- Step 4: 视觉处理 (OCR + Annotate) ---
            self._update_step_status("vision", "processing")
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
            self._update_step_status("vision", "completed")
            self._check_cancelled()

            # --- Step 5: 形式缺陷检查 ---
            self._update_step_status("check", "processing")
            logger.info(f"[{self.pn}] Step 5: Running Formal Defect Check...")
            # 即使文件存在也重新跑检查，因为检查逻辑可能很快且需要最新状态
            examiner = FormalExaminer(parts_db=parts_db, image_parts=image_parts)
            check_result = examiner.check()
            self.paths["check_json"].write_text(
                json.dumps(check_result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self._update_step_status("check", "completed")
            self._check_cancelled()

            # --- Step 6: 报告内容生成 ---
            self._update_step_status("generate", "processing")
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
            self._update_step_status("generate", "completed")
            self._check_cancelled()

            # --- Step 7: 检索策略生成 ---
            self._update_step_status("search", "processing")
            logger.info("Step 7: Generating Search Strategy & Queries...")
            search_json = {}
            if self.paths["search_strategy_json"].exists():
                logger.info("Step 7: Loading search strategy json...")
                search_json = json.loads(
                    self.paths["search_strategy_json"].read_text(encoding="utf-8")
                )
            else:
                logger.info("Step 7: Generating search strategy json...")

                # 初始化生成器
                cache_file = self.paths["root"].joinpath("search_strategy_intermediate.json")
                search_gen = SearchStrategyGenerator(patent_data, report_json, cache_file)

                # 执行生成
                search_json = search_gen.generate_strategy()

                # 4. 写入独立的 JSON 文件
                self.paths["search_strategy_json"].write_text(
                    json.dumps(search_json, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            self._update_step_status("search", "completed")
            self._check_cancelled()

            # --- Step 8: 渲染 ---
            self._update_step_status("render", "processing")
            logger.info(f"[{self.pn}] Step 8: Rendering Report...")
            renderer = ReportRenderer(patent_data)
            renderer.render(
                report_data=report_json,
                check_result=check_result,
                search_data=search_json,
                md_path=self.paths["final_md"],
                pdf_path=self.paths["final_pdf"],
            )
            self._update_step_status("render", "completed")

            logger.success(f"[{self.pn}] Completed! Output: {self.paths['final_pdf']}")
            return {"status": "success", "pn": self.pn, "output": str(self.paths["final_pdf"])}

        except PipelineCancelled as exc:
            logger.warning(f"[{self.pn}] Pipeline cancelled: {str(exc)}")
            return {"status": "cancelled", "pn": self.pn, "error": str(exc)}
        except Exception as e:
            logger.exception(f"[{self.pn}] Pipeline failed: {str(e)}")
            return {"status": "failed", "pn": self.pn, "error": str(e)}

    def _step_download(self):
        """处理文件下载与归档（支持上传文件或下载专利）"""
        self._check_cancelled()
        if self.raw_pdf_path.exists():
            logger.info(f"[{self.pn}] Step 0: raw.pdf already exists in output dir, skipping download.")
            return

        # 如果有上传文件，直接使用上传的文件
        if self.upload_file_path and Path(self.upload_file_path).exists():
            logger.info(f"[{self.pn}] Step 0: Using uploaded file: {self.upload_file_path}")
            import shutil
            shutil.copy2(self.upload_file_path, self.raw_pdf_path)
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
        logger.warning("  python -m agents.patent_analysis.main --pn CN116745575A")
        logger.warning("  python -m agents.patent_analysis.main --file list.txt")
        return

    logger.info(f"Total patents to process: {len(pns)}")

    # 2. 批量执行
    results = []
    start_time = time.time()

    # 使用线程池并发处理
    with ThreadPoolExecutor(max_workers=1) as executor:
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
