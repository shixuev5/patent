"""
文档处理节点
负责解析输入文件（包括审查意见通知书、意见陈述书、权利要求书）为markdown格式，并对审查意见通知书进行结构化提取
"""

from pathlib import Path
from loguru import logger
from agents.common.parsers.pdf_parser import PDFParser
from agents.common.parsers.word_parser import WordParser
from agents.common.office_action_structuring.rule_based_extractor import OfficeActionExtractor
from agents.common.patent_structuring import extract_structured_claims
from agents.office_action_reply.src.state import WorkflowState, ParsedFile, StructuredClaim
from agents.office_action_reply.src.utils import get_node_cache


class DocumentProcessingNode:
    """文档处理节点"""

    def __init__(self, config=None):
        """初始化文档处理节点"""
        self.config = config
        self.pdf_parser = PDFParser()
        self.word_parser = WordParser()
        self.office_action_extractor = OfficeActionExtractor()

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
            return str(self.pdf_parser.parse(file_path, output_dir))
        elif file_path.suffix.lower() == '.docx':
            logger.info(f"解析Word文件: {file_path}")
            return str(self.word_parser.parse(file_path, output_dir))
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
            # 获取节点缓存
            cache = get_node_cache(self.config, "document_processing")

            # 使用缓存运行文档处理
            processed_result = cache.run_step(
                "process_documents_v3",
                self._process_documents,
                state.input_files,
                state.output_dir
            )

            # 更新状态
            updates["parsed_files"] = processed_result["parsed_files"]
            updates["office_action"] = processed_result["office_action"]
            updates["claims_new_structured"] = [
                item if isinstance(item, StructuredClaim) else StructuredClaim(**item)
                for item in processed_result.get("claims_new_structured", [])
            ]
            updates["progress"] = 30.0
            updates["status"] = "completed"
            logger.success("文档处理节点执行成功")

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
        claims_new_structured = []

        # 为每个输入文件创建子目录
        for input_file in input_files:
            file_dir = Path(output_dir) / f"parsed_{input_file.file_type}"
            file_dir.mkdir(parents=True, exist_ok=True)

            logger.info(f"处理文件: {input_file.file_name} ({input_file.file_type})")

            # 解析文件为markdown
            markdown_path = self.parse_document(input_file.file_path, file_dir)

            # 读取markdown内容
            with open(markdown_path, 'r', encoding='utf-8') as f:
                markdown_content = f.read()

            # 创建ParsedFile对象
            parsed_file = ParsedFile(
                file_path=input_file.file_path,
                file_type=input_file.file_type,
                markdown_path=markdown_path,
                content=markdown_content
            )
            parsed_files.append(parsed_file)

            # 如果是审查意见通知书，进行结构化提取
            if input_file.file_type == "office_action":
                logger.info("开始提取审查意见结构化数据")
                office_action_data = self.extract_office_action_structured_data(markdown_path)
                self._validate_office_action_data(office_action_data)
                logger.info(f"成功提取审查意见结构化数据")
            elif input_file.file_type == "claims":
                logger.info("开始结构化提取新权利要求")
                claims_new_structured = extract_structured_claims(markdown_content)
                logger.info(f"提取到 {len(claims_new_structured)} 条新权利要求")

        return {
            "parsed_files": parsed_files,
            "office_action": office_action_data,
            "claims_new_structured": claims_new_structured,
        }

    def _validate_office_action_data(self, office_action_data: dict) -> None:
        if not office_action_data:
            raise ValueError("审查意见结构化数据为空，无法继续")

        application_number = str(office_action_data.get("application_number", "")).strip()
        if not application_number:
            raise ValueError("审查意见未解析出原专利号(application_number)")
