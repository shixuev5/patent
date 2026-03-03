"""
专利检索节点
负责调用智慧芽API下载专利文件并解析为结构化数据
"""

from pathlib import Path
from loguru import logger
from agents.common.search_clients.factory import SearchClientFactory
from agents.common.parsers.pdf_parser import PDFParser
from agents.common.patent_structuring import extract_structured_data
from agents.office_action_reply.src.state import WorkflowState
from agents.office_action_reply.src.utils import is_patent_document, get_node_cache


class PatentRetrievalNode:
    """专利检索节点"""

    def __init__(self, config=None):
        """初始化专利检索节点"""
        self.config = config
        self.search_client = SearchClientFactory.get_client("zhihuiya")
        self.pdf_parser = PDFParser()

    def download_patent(self, patent_number: str, output_dir: str) -> str:
        """
        下载专利文件

        Args:
            patent_number: 专利号
            output_dir: 输出目录

        Returns:
            专利文件路径
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"开始下载专利: {patent_number}")

        # 构造完整的专利文件路径
        patent_path = output_dir / f"{patent_number}.pdf"
        success = self.search_client.download_patent_document(patent_number, str(patent_path))

        if success and patent_path.exists():
            logger.success(f"专利下载成功: {patent_path}")
            return str(patent_path)
        else:
            raise Exception(f"专利下载失败: {patent_number}")

    def parse_patent_document(self, patent_path: str, output_dir: str) -> str:
        """
        解析专利文档

        Args:
            patent_path: 专利文件路径
            output_dir: 输出目录

        Returns:
            解析后的markdown文件路径
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"开始解析专利文档: {patent_path}")
        markdown_path = str(self.pdf_parser.parse(Path(patent_path), output_dir))
        logger.success(f"专利文档解析成功: {markdown_path}")

        return markdown_path

    def extract_patent_structured_data(self, markdown_path: str) -> dict:
        """
        提取专利结构化数据

        Args:
            markdown_path: markdown文件路径

        Returns:
            专利结构化数据
        """
        with open(markdown_path, 'r', encoding='utf-8') as f:
            markdown_content = f.read()

        logger.info(f"开始提取专利结构化数据")
        structured_data = extract_structured_data(markdown_content)
        logger.success(f"专利结构化数据提取成功")

        return structured_data

    def __call__(self, state: WorkflowState):
        """
        执行专利检索节点

        Args:
            state: 当前工作流状态

        Returns:
            包含要更新字段的字典
        """
        logger.info("开始专利检索节点")
        updates = {
            "current_node": "patent_retrieval",
            "status": "running",
            "progress": 30.0
        }

        try:
            # 获取节点缓存
            cache = get_node_cache(self.config, "patent_retrieval")

            # 使用缓存运行专利检索
            search_results = cache.run_step(
                "retrieve_patents",
                self._retrieve_patents,
                state.office_action,
                state.output_dir
            )

            if search_results:
                updates["search_results"] = search_results

            logger.success("专利检索节点执行成功")
            updates["progress"] = 40.0

        except Exception as e:
            logger.error(f"专利检索节点执行失败: {e}")
            updates["errors"] = state.errors + [{
                "node_name": "patent_retrieval",
                "error_message": str(e),
                "error_type": "patent_retrieval"
            }]
            updates["status"] = "failed"

        return updates

    def _retrieve_patents(self, office_action, output_dir):
        """
        实际执行专利检索的内部方法（可缓存）

        Args:
            office_action: 审查意见数据
            output_dir: 输出目录

        Returns:
            专利检索结果列表，格式为：
            [{"<专利号/公开号>": structured_data}, ...]
        """
        search_results = []
        processed_patents = set()

        if office_action:
            application_number = (office_action.get("application_number") or "").strip()
            if application_number:
                logger.info(f"处理原申请专利: {application_number}")
                self._process_patent(
                    document_number=application_number,
                    output_dir=output_dir,
                    search_results=search_results,
                    processed_patents=processed_patents
                )

            comparison_documents = office_action.get("comparison_documents", [])
            logger.info(f"找到 {len(comparison_documents)} 个对比文件")

            # 处理专利对比文件
            for doc in comparison_documents:
                document_number = doc.get("document_number", "")
                is_patent = doc.get("is_patent")
                if is_patent is None:
                    is_patent = is_patent_document(document_number)

                # 只处理专利文献（包含CN、US、WO等国家代码）
                if is_patent:
                    logger.info(f"处理专利对比文件: {document_number}")
                    self._process_patent(
                        document_number=document_number,
                        output_dir=output_dir,
                        search_results=search_results,
                        processed_patents=processed_patents
                    )

        return search_results

    def _process_patent(self, document_number, output_dir, search_results, processed_patents):
        """下载、解析并结构化单个专利。"""
        if not document_number:
            return

        if document_number in processed_patents:
            logger.info(f"专利已处理，跳过重复: {document_number}")
            return

        processed_patents.add(document_number)

        patent_dir = Path(output_dir) / f"patent_{document_number}"
        patent_dir.mkdir(parents=True, exist_ok=True)

        patent_path = self.download_patent(document_number, patent_dir)
        markdown_path = self.parse_patent_document(patent_path, patent_dir)

        structured_data = self.extract_patent_structured_data(markdown_path)

        search_results.append({
            document_number: structured_data
        })
