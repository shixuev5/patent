import json
import argparse
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Event
from typing import Any, Dict, Optional, Tuple

from loguru import logger
from config import settings
from backend.log_context import bind_task_logger

# 引入各个处理模块
from agents.common.search_clients.factory import SearchClientFactory
from agents.common.parsers.pdf_parser import PDFParser
from agents.common.patent_structuring import extract_structured_data
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

    def __init__(self, pn: Optional[str], upload_file_path: str = None, cancel_event: Optional[Event] = None, task_id: str = None):
        self.pn = str(pn or "").strip()
        self.upload_file_path = upload_file_path
        self.cancel_event = cancel_event
        self.task_id = task_id
        self.workspace_id = self.task_id or (self.pn or "task")
        self.log = bind_task_logger(self.task_id or "-", "patent_analysis", pn=self.pn, stage="pipeline")
        # 工作目录按 task_id 隔离；产物命名仍沿用专利号
        self.paths = settings.get_project_paths(workspace_id=self.workspace_id, artifact_name=self.pn)

        # 确保根目录存在
        self.paths["root"].mkdir(parents=True, exist_ok=True)
        self.paths["annotated_dir"].mkdir(exist_ok=True)

        # raw.pdf 的目标路径
        self.raw_pdf_path = self.paths["raw_pdf"]

        # 初始化任务管理器
        if self.task_id:
            from backend.storage import get_pipeline_manager
            self.task_manager = get_pipeline_manager()

    def _check_cancelled(self):
        if self.cancel_event and self.cancel_event.is_set():
            raise PipelineCancelled("任务已取消")

    def _safe_artifact_name(self, value: str) -> str:
        return "".join([c for c in str(value or "") if c.isalnum() or c in ("-", "_")])

    def _resolve_pn_from_patent_data(self, patent_data: dict) -> str:
        # 规则：有输入 pn 则优先使用；无输入 pn 才从结构化数据读取 publication_number
        input_pn = self._safe_artifact_name(self.pn)
        if input_pn:
            return input_pn

        biblio = patent_data.get("bibliographic_data", {}) if isinstance(patent_data, dict) else {}
        publication_number = self._safe_artifact_name(str(biblio.get("publication_number", "")).strip())
        if publication_number:
            return publication_number
        return self._safe_artifact_name(self.workspace_id)

    def _refresh_output_artifact_paths(self, patent_data: dict):
        resolved_pn = self._resolve_pn_from_patent_data(patent_data)
        if not resolved_pn:
            return
        if resolved_pn != self.pn:
            self._stage_log("transform").info(f"解析到专利号，更新产物命名: {resolved_pn}")
            self.pn = resolved_pn
        self.paths["final_md"] = self.paths["root"] / f"{resolved_pn}.md"
        self.paths["final_pdf"] = self.paths["root"] / f"{resolved_pn}.pdf"

    def _stage_log(self, stage: str):
        return self.log.bind(stage=stage)

    def _update_step_status(self, step_name: str, status: str):
        """更新任务步骤状态"""
        if self.task_id and self.task_manager:
            from backend.storage import DEFAULT_PIPELINE_STEPS
            step_name_map = {en: zh for en, zh in DEFAULT_PIPELINE_STEPS}
            chinese_step_name = step_name_map.get(step_name, step_name)

            total_steps = len(DEFAULT_PIPELINE_STEPS)
            step_index = next((i for i, (en, zh) in enumerate(DEFAULT_PIPELINE_STEPS) if en == step_name), 0)

            if status == "processing":
                progress = int((step_index / total_steps) * 100)
            elif status == "completed":
                progress = int(((step_index + 1) / total_steps) * 100)
            else:
                progress = 0

            self.task_manager.update_progress(self.task_id, progress, chinese_step_name, step_status=status)

    def _run_check_step(self, parts_db: Dict[str, Any], image_parts: Dict[str, Any]) -> Dict[str, Any]:
        self._check_cancelled()
        self._stage_log("check").info("执行形式缺陷检查")
        examiner = FormalExaminer(parts_db=parts_db, image_parts=image_parts)
        check_result = examiner.check()
        self.paths["check_json"].write_text(
            json.dumps(check_result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return check_result

    def _run_generate_step(
        self, patent_data: Dict[str, Any], parts_db: Dict[str, Any], image_parts: Dict[str, Any]
    ) -> Dict[str, Any]:
        self._check_cancelled()
        if self.paths["report_json"].exists():
            self._stage_log("generate").info("加载已有报告 JSON")
            return json.loads(self.paths["report_json"].read_text(encoding="utf-8"))

        self._stage_log("generate").info("生成报告 JSON")
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
        return report_json

    def _run_check_and_generate_parallel(
        self, patent_data: Dict[str, Any], parts_db: Dict[str, Any], image_parts: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        check_result: Optional[Dict[str, Any]] = None
        report_json: Optional[Dict[str, Any]] = None
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="pipeline") as executor:
            future_check = executor.submit(self._run_check_step, parts_db, image_parts)
            future_generate = executor.submit(
                self._run_generate_step, patent_data, parts_db, image_parts
            )
            futures = {future_check: "check", future_generate: "generate"}
            try:
                for future in as_completed(futures):
                    stage = futures[future]
                    result = future.result()
                    if stage == "check":
                        check_result = result
                        self._update_step_status("check", "completed")
                    else:
                        report_json = result
                        self._update_step_status("generate", "completed")
            except Exception:
                for pending in futures:
                    pending.cancel()
                raise

        if check_result is None or report_json is None:
            raise RuntimeError("并行阶段未产出完整结果")
        return check_result, report_json

    def run(self) -> dict:
        """
        执行完整的处理流程
        :return: 包含处理状态和结果路径的字典
        """
        self._stage_log("pipeline").info("开始执行专利分析流程")

        try:
            self._check_cancelled()
            self._update_step_status("download", "processing")
            self._step_download()
            self._update_step_status("download", "completed")
            self._check_cancelled()

            self._update_step_status("parse", "processing")
            if not self.paths["raw_md"].exists():
                self._stage_log("parse").info("开始解析 PDF")
                PDFParser.parse(self.raw_pdf_path, self.paths["mineru_dir"])
            else:
                self._stage_log("parse").info("已存在解析结果，跳过")

            md_content = self.paths["raw_md"].read_text(encoding="utf-8")
            self._update_step_status("parse", "completed")
            self._check_cancelled()

            self._update_step_status("transform", "processing")
            patent_data = {}
            if self.paths["patent_json"].exists():
                self._stage_log("transform").info("加载已有结构化专利数据")
                patent_data = json.loads(self.paths["patent_json"].read_text(encoding="utf-8"))
            else:
                self._stage_log("transform").info("将 Markdown 转换为结构化 JSON")
                patent_data = extract_structured_data(md_content, method="hybrid")
                self.paths["patent_json"].write_text(
                    json.dumps(patent_data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            self._refresh_output_artifact_paths(patent_data)
            self._update_step_status("transform", "completed")
            self._check_cancelled()

            self._update_step_status("extract", "processing")
            parts_db = {}
            if self.paths["parts_json"].exists():
                self._stage_log("extract").info("加载已有部件知识库")
                parts_db = json.loads(self.paths["parts_json"].read_text(encoding="utf-8"))
            else:
                self._stage_log("extract").info("提取知识要素")
                extractor = KnowledgeExtractor()
                parts_db = extractor.extract_entities(patent_data)
                self.paths["parts_json"].write_text(
                    json.dumps(parts_db, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            self._update_step_status("extract", "completed")
            self._check_cancelled()

            self._update_step_status("vision", "processing")
            image_parts = {}
            if self.paths["image_parts_json"].exists():
                self._stage_log("vision").info("加载已有图像部件映射")
                image_parts = json.loads(self.paths["image_parts_json"].read_text(encoding="utf-8"))
            else:
                self._stage_log("vision").info("执行图像处理与 OCR")
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

            self._update_step_status("check", "processing")
            self._update_step_status("generate", "processing")
            check_result, report_json = self._run_check_and_generate_parallel(
                patent_data=patent_data,
                parts_db=parts_db,
                image_parts=image_parts,
            )
            self._check_cancelled()

            self._update_step_status("search", "processing")
            self._stage_log("search").info("生成检索策略与语义查询")
            search_json = {}
            if self.paths["search_strategy_json"].exists():
                self._stage_log("search").info("加载已有检索策略")
                search_json = json.loads(
                    self.paths["search_strategy_json"].read_text(encoding="utf-8")
                )
            else:
                self._stage_log("search").info("生成检索策略 JSON")

                cache_file = self.paths["root"].joinpath("search_strategy_intermediate.json")
                search_gen = SearchStrategyGenerator(patent_data, report_json, cache_file)

                search_json = search_gen.generate_strategy()

                self.paths["search_strategy_json"].write_text(
                    json.dumps(search_json, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            self._update_step_status("search", "completed")
            self._check_cancelled()

            self._update_step_status("render", "processing")
            self._stage_log("render").info("渲染 Markdown/PDF 报告")
            renderer = ReportRenderer(patent_data)
            renderer.render(
                report_data=report_json,
                check_result=check_result,
                search_data=search_json,
                md_path=self.paths["final_md"],
                pdf_path=self.paths["final_pdf"],
            )
            self._update_step_status("render", "completed")

            self._stage_log("render").success(f"流程执行完成，输出文件: {self.paths['final_pdf']}")
            return {"status": "success", "pn": self.pn, "output": str(self.paths["final_pdf"])}

        except PipelineCancelled as exc:
            self._stage_log("pipeline").warning(f"流程已取消: {str(exc)}")
            return {"status": "cancelled", "pn": self.pn, "error": str(exc)}
        except Exception as e:
            self._stage_log("pipeline").exception(f"流程执行失败: {str(e)}")
            return {"status": "failed", "pn": self.pn, "error": str(e)}

    def _step_download(self):
        """处理文件下载与归档（支持上传文件或下载专利）"""
        self._check_cancelled()
        if self.raw_pdf_path.exists():
            self._stage_log("download").info("已存在 raw.pdf，跳过下载")
            return

        if self.upload_file_path and Path(self.upload_file_path).exists():
            self._stage_log("download").info(f"使用上传文件: {self.upload_file_path}")
            import shutil
            shutil.copy2(self.upload_file_path, self.raw_pdf_path)
            return

        self._stage_log("download").info("下载专利原文")

        client = SearchClientFactory.get_client("zhihuiya")

        success = client.download_patent_document(self.pn, str(self.raw_pdf_path))

        if not success:
            raise Exception(f"下载失败: {self.pn}（API 返回异常或文件为空）")


def worker_wrapper(pn: str) -> dict:
    """线程池的包装函数，确保每个线程有独立的 Pipeline 实例"""
    pipeline = PatentPipeline(pn)
    return pipeline.run()


def main():
    from backend.logging_setup import setup_logging_utc8
    setup_logging_utc8(level="INFO")

    parser = argparse.ArgumentParser(description="专利分析流水线")
    parser.add_argument("--pn", type=str, help="单个专利号，或逗号分隔的多个专利号（例：CN116745575A,CN123）")
    parser.add_argument("--file", type=str, help="专利号文本文件路径（每行一个）")

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
            logger.error(f"未找到输入文件: {args.file}")

    # 去重
    pns = list(set(pns))

    if not pns:
        logger.warning("未提供可处理的专利号，用法示例：")
        logger.warning("  python -m agents.patent_analysis.main --pn CN116745575A")
        logger.warning("  python -m agents.patent_analysis.main --file list.txt")
        return

    logger.info(f"待处理专利数量: {len(pns)}")

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
                    logger.success(f"{pn} 处理完成")
                else:
                    logger.error(f"{pn} 处理失败: {res.get('error')}")
            except Exception as exc:
                logger.error(f"{pn} 处理出现异常: {exc}")
                results.append({"status": "failed", "pn": pn, "error": str(exc)})

    # 3. 统计结果
    duration = time.time() - start_time
    success_count = sum(1 for r in results if r["status"] == "success")

    logger.info("=" * 40)
    logger.info(f"批量处理完成，耗时: {duration:.2f}s")
    logger.info(f"总计: {len(pns)}，成功: {success_count}，失败: {len(pns) - success_count}")
    logger.info("=" * 40)


if __name__ == "__main__":
    main()
