"""
文档处理节点
负责解析输入文件（包括审查意见通知书、意见陈述书、不同版本权利要求书）为markdown格式，并对审查意见通知书进行结构化提取
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from loguru import logger
from agents.common.utils.concurrency import submit_with_current_context
from agents.common.parsers.pdf_parser import PDFParser
from agents.common.parsers.word_parser import WordParser
from agents.common.office_action_structuring.rule_based_extractor import OfficeActionExtractor
from agents.common.search_clients.factory import SearchClientFactory
from agents.common.patent_structuring import extract_structured_claims
from agents.ai_reply.src.state import WorkflowState, ParsedFile, StructuredClaim
from agents.ai_reply.src.utils import PipelineCancelled, ensure_not_cancelled, get_node_cache
from config import settings


class DocumentProcessingNode:
    """文档处理节点"""

    def __init__(self, config=None):
        """初始化文档处理节点"""
        self.config = config
        self._search_client = None
        self.office_action_extractor = OfficeActionExtractor(
            patent_resolver=self._resolve_is_patent_via_zhihuiya,
        )

    def parse_document(self, file_path: str, output_dir: str) -> str:
        """
        解析文档为markdown格式

        Args:
            file_path: 文档路径
            output_dir: 输出目录

        Returns:
            解析后的markdown文件路径
        """
        file_path = Path(file_path)
        output_dir = Path(output_dir)

        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        output_dir.mkdir(parents=True, exist_ok=True)

        # 根据文件扩展名选择解析器
        if file_path.suffix.lower() == '.pdf':
            logger.info(f"解析PDF文件: {file_path}")
            return str(PDFParser.parse(file_path, output_dir))
        elif file_path.suffix.lower() in {'.doc', '.docx'}:
            logger.info(f"解析Word文件: {file_path}")
            return str(WordParser.parse(file_path, output_dir))
        else:
            raise ValueError(f"不支持的文件格式: {file_path.suffix}")

    def extract_office_action_structured_data(self, markdown_path: str) -> dict:
        """
        从markdown格式的审查意见通知书中提取结构化数据

        Args:
            markdown_path: markdown文件路径

        Returns:
            结构化数据字典
        """
        with open(markdown_path, 'r', encoding='utf-8') as f:
            markdown_content = f.read()

        try:
            office_action = self.office_action_extractor.extract(markdown_content)
            return office_action.dict()
        except Exception as e:
            logger.error(f"提取审查意见结构化数据失败: {e}")
            raise

    def __call__(self, state: WorkflowState) -> dict:
        """
        执行文档处理节点

        Args:
            state: 当前工作流状态

        Returns:
            包含要更新字段的字典
        """
        logger.info("开始文档处理节点")

        updates = {
            "current_node": "document_processing",
            "status": "running",
            "progress": 15.0
        }

        try:
            ensure_not_cancelled(self.config)
            # 获取节点缓存
            cache = get_node_cache(self.config, "document_processing")

            # 使用缓存运行文档处理
            processed_result = cache.run_step(
                "process_documents_v4",
                self._process_documents,
                state.input_files,
                state.output_dir
            )

            # 更新状态
            updates["parsed_files"] = processed_result["parsed_files"]
            updates["office_action"] = processed_result["office_action"]
            updates["claims_previous_structured"] = [
                item if isinstance(item, StructuredClaim) else StructuredClaim(**item)
                for item in processed_result.get("claims_previous_structured", [])
            ]
            updates["claims_current_structured"] = [
                item if isinstance(item, StructuredClaim) else StructuredClaim(**item)
                for item in processed_result.get("claims_current_structured", [])
            ]
            updates["progress"] = 30.0
            updates["status"] = "completed"
            logger.success("文档处理节点执行成功")

        except PipelineCancelled as e:
            logger.warning(f"文档处理节点已取消: {e}")
            updates["errors"] = [{
                "node_name": "document_processing",
                "error_message": str(e),
                "error_type": "cancelled"
            }]
            updates["status"] = "cancelled"
        except Exception as e:
            logger.error(f"文档处理节点执行失败: {e}")
            error_message = str(e)
            error_type = "missing_application_number" if "未解析出原专利号" in error_message else "document_processing"
            updates["errors"] = [{
                "node_name": "document_processing",
                "error_message": error_message,
                "error_type": error_type
            }]
            updates["status"] = "failed"

        return updates

    def _process_documents(self, input_files, output_dir):
        """
        实际执行文档处理的内部方法（可缓存）

        Args:
            input_files: 输入文件列表
            output_dir: 输出目录

        Returns:
            包含解析文件和审查意见数据的字典
        """
        parsed_files = []
        office_action_data = None
        claims_previous_structured = []
        claims_current_structured = []

        max_workers = max(1, min(settings.OAR_MAX_CONCURRENCY, max(len(input_files), 1)))
        parse_results = [None] * len(input_files)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for index, input_file in enumerate(input_files):
                futures[
                    submit_with_current_context(
                        executor, self._parse_single_file, input_file, output_dir
                    )
                ] = index

            for future in as_completed(futures):
                index = futures[future]
                parse_results[index] = future.result()

        for parsed in parse_results:
            if not parsed:
                continue
            parsed_file = parsed["parsed_file"]
            parsed_files.append(parsed_file)

            if parsed_file.file_type == "office_action":
                logger.info("开始提取审查意见结构化数据")
                office_action_data = self.extract_office_action_structured_data(parsed_file.markdown_path)
                self._validate_office_action_data(office_action_data)
                logger.info("成功提取审查意见结构化数据")
            elif parsed_file.file_type == "claims_previous":
                logger.info("开始结构化提取上一版权利要求")
                claims_previous_structured = extract_structured_claims(parsed_file.content)
                logger.info(f"提取到 {len(claims_previous_structured)} 条上一版权利要求")
            elif parsed_file.file_type == "claims_current":
                logger.info("开始结构化提取当前最新权利要求")
                claims_current_structured = extract_structured_claims(parsed_file.content)
                logger.info(f"提取到 {len(claims_current_structured)} 条当前最新权利要求")

        return {
            "parsed_files": parsed_files,
            "office_action": office_action_data,
            "claims_previous_structured": claims_previous_structured,
            "claims_current_structured": claims_current_structured,
        }

    def _parse_single_file(self, input_file, output_dir: str):
        file_type = self._item_get(input_file, "file_type", "")
        file_name = self._item_get(input_file, "file_name", "")
        file_path = self._item_get(input_file, "file_path", "")

        file_dir = Path(output_dir) / f"parsed_{file_type}"
        file_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"处理文件: {file_name} ({file_type})")
        markdown_path = self.parse_document(file_path, file_dir)
        with open(markdown_path, 'r', encoding='utf-8') as f:
            markdown_content = f.read()

        parsed_file = ParsedFile(
            file_path=file_path,
            file_type=file_type,
            markdown_path=markdown_path,
            content=markdown_content,
        )
        return {"parsed_file": parsed_file}

    def _item_get(self, item: Any, key: str, default=None):
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)

    def _validate_office_action_data(self, office_action_data: dict) -> None:
        if not office_action_data:
            raise ValueError("审查意见结构化数据为空，无法继续")

        application_number = str(office_action_data.get("application_number", "")).strip()
        if not application_number:
            raise ValueError("审查意见未解析出原专利号(application_number)")
        current_notice_round = int(office_action_data.get("current_notice_round", 0) or 0)
        if current_notice_round <= 0:
            raise ValueError("审查意见未解析出有效轮次(current_notice_round)")

    def _get_search_client(self):
        if self._search_client is None:
            self._search_client = SearchClientFactory.get_client("zhihuiya")
        return self._search_client

    def _resolve_is_patent_via_zhihuiya(self, document_number: str) -> bool:
        normalized = str(document_number or "").strip()
        if not normalized:
            return False

        client = self._get_search_client()
        has_patent_record = getattr(client, "has_patent_record", None)
        if not callable(has_patent_record):
            raise RuntimeError("智慧芽客户端不支持专利存在性查询")

        result = bool(has_patent_record(normalized))
        logger.info(f"智慧芽兜底识别对比文件是否为专利: {normalized} -> {result}")
        return result
