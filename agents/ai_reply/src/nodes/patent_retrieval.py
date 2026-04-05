"""
专利检索节点
负责调用智慧芽API下载专利文件并解析为结构化数据
"""

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from loguru import logger
from agents.common.utils.concurrency import submit_with_current_context
from agents.common.search_clients.factory import SearchClientFactory
from agents.common.parsers.pdf_parser import PDFParser
from agents.common.patent_structuring import extract_structured_data
from agents.ai_reply.src.state import WorkflowState
from agents.ai_reply.src.utils import PipelineCancelled, ensure_not_cancelled, get_node_cache, is_patent_document
from backend.utils import _build_r2_storage
from config import settings


class PatentRetrievalNode:
    """专利检索节点"""

    def __init__(self, config=None):
        """初始化专利检索节点"""
        self.config = config
        self.search_client = SearchClientFactory.get_client("zhihuiya")

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
        markdown_path = str(PDFParser.parse(Path(patent_path), output_dir))
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
        structured_data = extract_structured_data(markdown_content, method="hybrid")
        logger.success(f"专利结构化数据提取成功")

        return structured_data

    def _resolve_patent_json_candidates(self, document_number: str) -> list[str]:
        """解析 patent.json 对应的候选专利号，优先原值，其次申请号映射出的公开号。"""
        candidates: list[str] = []
        normalized_document_number = str(document_number or "").strip().upper()
        if normalized_document_number:
            candidates.append(normalized_document_number)

        if document_number and not is_patent_document(document_number):
            try:
                publication_number = self.search_client.get_publication_number_by_application_number(document_number)
            except Exception as exc:
                logger.warning(f"原专利申请号转公开号失败，将回退到下载解析: {document_number}, error={exc}")
            else:
                normalized_publication_number = str(publication_number or "").strip().upper()
                if normalized_publication_number and normalized_publication_number not in candidates:
                    candidates.append(normalized_publication_number)
        return candidates

    def _write_local_patent_json(self, patent_dir: Path, patent_data: dict) -> Path:
        patent_json_path = patent_dir / "patent.json"
        patent_json_path.write_text(
            json.dumps(patent_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return patent_json_path

    def _load_patent_json_from_r2(self, document_number: str, patent_dir: Path):
        """尝试从 R2 直接加载已分析专利的 patent.json。"""
        r2_storage = _build_r2_storage()
        if not r2_storage.enabled:
            return None

        for patent_number in self._resolve_patent_json_candidates(document_number):
            patent_key = r2_storage.build_patent_json_key(patent_number)
            patent_bytes = r2_storage.get_bytes(patent_key)
            if not patent_bytes:
                continue

            try:
                patent_data = json.loads(patent_bytes.decode("utf-8"))
            except Exception as exc:
                logger.warning(f"R2 patent.json 解析失败，将回退到下载解析: key={patent_key}, error={exc}")
                continue

            if not isinstance(patent_data, dict):
                logger.warning(f"R2 patent.json 格式非法，将回退到下载解析: key={patent_key}")
                continue

            self._write_local_patent_json(patent_dir, patent_data)
            logger.info(
                f"命中原专利 R2 patent.json，跳过下载解析: document_number={document_number}, r2_key={patent_key}"
            )
            return patent_data

        return None

    def _persist_patent_json_to_r2(self, document_number: str, patent_data: dict) -> bool:
        """将 AI 答复阶段首次解析出的原专利 patent.json 回写到 R2。"""
        r2_storage = _build_r2_storage()
        if not r2_storage.enabled:
            return False

        candidates = self._resolve_patent_json_candidates(document_number)
        if not candidates:
            logger.warning(f"原专利 patent.json 缺少可用 key，跳过 R2 回写: document_number={document_number}")
            return False

        patent_number = candidates[-1]
        patent_key = r2_storage.build_patent_json_key(patent_number)
        stored = r2_storage.put_bytes(
            patent_key,
            json.dumps(patent_data, ensure_ascii=False, indent=2).encode("utf-8"),
            "application/json",
        )
        if stored:
            logger.info(
                f"原专利 patent.json 已回写到 R2: document_number={document_number}, r2_key={patent_key}"
            )
            return True

        logger.warning(f"原专利 patent.json 回写到 R2 失败: document_number={document_number}")
        return False

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
            ensure_not_cancelled(self.config)
            # 获取节点缓存
            cache = get_node_cache(self.config, "patent_retrieval")

            # 使用缓存运行专利检索
            search_results = cache.run_step(
                "retrieve_patents_v2",
                self._retrieve_patents,
                state.office_action,
                state.output_dir
            )

            if search_results:
                updates["search_results"] = search_results

            logger.success("专利检索节点执行成功")
            updates["progress"] = 40.0

        except PipelineCancelled as e:
            logger.warning(f"专利检索节点已取消: {e}")
            updates["errors"] = state.errors + [{
                "node_name": "patent_retrieval",
                "error_message": str(e),
                "error_type": "cancelled"
            }]
            updates["status"] = "cancelled"
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
        patent_numbers: list[str] = []
        search_results: list[dict] = []
        original_patent_number = ""

        if office_action:
            original_patent_number = (office_action.get("application_number") or "").strip()
            if original_patent_number:
                patent_numbers.append(original_patent_number)

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
                    patent_numbers.append(document_number)

        dedup_patents: list[str] = []
        for patent_number in patent_numbers:
            if patent_number and patent_number not in dedup_patents:
                dedup_patents.append(patent_number)

        if not dedup_patents:
            return search_results

        max_workers = max(1, min(settings.OAR_MAX_CONCURRENCY, len(dedup_patents)))
        results_by_number: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                submit_with_current_context(
                    executor,
                    self._retrieve_single_patent,
                    patent_number,
                    output_dir,
                    patent_number == original_patent_number,
                ): patent_number
                for patent_number in dedup_patents
            }
            for future in as_completed(futures):
                patent_number = futures[future]
                result = future.result()
                if result:
                    results_by_number[patent_number] = result

        for patent_number in dedup_patents:
            result = results_by_number.get(patent_number)
            if result:
                search_results.append(result)
        return search_results

    def _retrieve_single_patent(
        self,
        document_number: str,
        output_dir: str,
        prefer_cached_patent_json: bool = False,
    ):
        """下载、解析并结构化单个专利。"""
        if not document_number:
            return None

        patent_dir = Path(output_dir) / f"patent_{document_number}"
        patent_dir.mkdir(parents=True, exist_ok=True)

        if prefer_cached_patent_json:
            cached_patent_data = self._load_patent_json_from_r2(document_number, patent_dir)
            if cached_patent_data is not None:
                return {
                    document_number: cached_patent_data
                }

        patent_path = self.download_patent(document_number, patent_dir)
        markdown_path = self.parse_patent_document(patent_path, patent_dir)

        structured_data = self.extract_patent_structured_data(markdown_path)
        self._write_local_patent_json(patent_dir, structured_data)
        if prefer_cached_patent_json:
            self._persist_patent_json_to_r2(document_number, structured_data)

        return {
            document_number: structured_data
        }
