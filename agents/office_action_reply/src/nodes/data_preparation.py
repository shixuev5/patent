"""
数据准备节点
负责校验非专文件数量并将数据整理为下游可消费的新结构
"""

from typing import Any, Dict, List
from loguru import logger
from agents.office_action_reply.src.state import WorkflowState
from agents.office_action_reply.src.utils import get_node_cache


class DataPreparationNode:
    """数据准备节点"""

    def __init__(self, config=None):
        self.config = config

    def __call__(self, state: WorkflowState) -> dict:
        logger.info("开始数据准备节点")

        updates = {
            "current_node": "data_preparation",
            "status": "running",
            "progress": 40.0
        }

        try:
            cache = get_node_cache(self.config, "data_preparation")

            prepared_materials = cache.run_step(
                "prepare_materials",
                self._prepare_materials,
                self._state_get(state, "office_action"),
                self._state_get(state, "parsed_files", []),
                self._state_get(state, "search_results", [])
            )
            updates["prepared_materials"] = prepared_materials
            updates["progress"] = 50.0
            updates["status"] = "completed"
            logger.success("数据准备节点执行成功")

        except Exception as e:
            logger.error(f"数据准备节点执行失败: {e}")
            error_message = str(e)
            error_type = "non_patent_mismatch" if "非专文献数量不匹配" in error_message else "data_preparation"
            updates["errors"] = [{
                "node_name": "data_preparation",
                "error_message": error_message,
                "error_type": error_type
            }]
            updates["status"] = "failed"

        return updates

    def _prepare_materials(self, office_action, parsed_files, search_results) -> Dict[str, Any]:
        if not office_action:
            raise ValueError("缺少 office_action，无法整理数据")

        comparison_documents = office_action.get("comparison_documents", [])
        non_patent_docs = [doc for doc in comparison_documents if not doc.get("is_patent", False)]
        uploaded_non_patent_files = [
            self._to_dict(item) for item in parsed_files
            if self._to_dict(item).get("file_type") == "comparison_doc"
        ]

        if len(non_patent_docs) != len(uploaded_non_patent_files):
            raise ValueError(
                f"非专文献数量不匹配: comparison_documents中非专={len(non_patent_docs)}，上传非专文件={len(uploaded_non_patent_files)}"
            )

        patent_data_map = self._build_patent_data_map(search_results)
        application_number = str(office_action.get("application_number", "")).strip()
        non_patent_contents = [item.get("content", "") for item in uploaded_non_patent_files]
        non_patent_index = 0

        normalized_comparison_documents = []
        for doc in comparison_documents:
            document_id = doc.get("document_id", "")
            document_number = doc.get("document_number", "")
            is_patent = bool(doc.get("is_patent", False))
            publication_date = doc.get("publication_date")

            if is_patent:
                data = patent_data_map.get(document_number, {})
            else:
                data = non_patent_contents[non_patent_index]
                non_patent_index += 1

            normalized_comparison_documents.append({
                "document_id": document_id,
                "document_number": document_number,
                "is_patent": is_patent,
                "publication_date": publication_date,
                "data": data
            })

        response_content = self._get_parsed_content(parsed_files, "response")
        claims_content = self._get_parsed_content(parsed_files, "claims")

        prepared_materials = {
            "original_patent": {
                "application_number": application_number,
                "data": patent_data_map.get(application_number, {})
            },
            "comparison_documents": normalized_comparison_documents,
            "office_action": {
                "application_number": application_number,
                "paragraphs": office_action.get("paragraphs", [])
            },
            "response": {
                "content": response_content
            },
            "claims": {
                "content": claims_content
            }
        }

        return prepared_materials

    def _build_patent_data_map(self, search_results) -> Dict[str, Dict[str, Any]]:
        """将 patent_retrieval 的新格式列表拍平成 map。"""
        patent_data_map: Dict[str, Dict[str, Any]] = {}
        for item in search_results or []:
            item_dict = self._to_dict(item)
            for patent_number, structured_data in item_dict.items():
                patent_data_map[str(patent_number)] = structured_data
        return patent_data_map

    def _get_parsed_content(self, parsed_files, file_type: str) -> str:
        for item in parsed_files or []:
            parsed = self._to_dict(item)
            if parsed.get("file_type") == file_type:
                return parsed.get("content", "")
        return ""

    def _state_get(self, state, key: str, default=None):
        if isinstance(state, dict):
            return state.get(key, default)
        return getattr(state, key, default)

    def _to_dict(self, item: Any) -> Dict[str, Any]:
        if isinstance(item, dict):
            return item
        if hasattr(item, "model_dump"):
            return item.model_dump()
        if hasattr(item, "dict"):
            return item.dict()
        return {}
