"""
数据准备节点
负责校验非专文件数量并将数据整理为下游可消费的新结构
"""

import re
import json
import os
from pathlib import Path
from typing import Any, Dict, List
from loguru import logger
from agents.common.retrieval import LocalEvidenceRetriever
from agents.ai_reply.src.state import WorkflowState
from agents.ai_reply.src.utils import PipelineCancelled, ensure_not_cancelled, get_node_cache
from config import settings


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
            ensure_not_cancelled(self.config)
            cache = get_node_cache(self.config, "data_preparation")

            prepared_materials = cache.run_step(
                "prepare_materials_v3",
                self._prepare_materials,
                self._state_get(state, "office_action"),
                self._state_get(state, "parsed_files", []),
                self._state_get(state, "search_results", [])
            )
            updates["prepared_materials"] = prepared_materials
            updates["progress"] = 50.0
            updates["status"] = "completed"
            logger.success("数据准备节点执行成功")

        except PipelineCancelled as e:
            logger.warning(f"数据准备节点已取消: {e}")
            updates["errors"] = [{
                "node_name": "data_preparation",
                "error_message": str(e),
                "error_type": "cancelled"
            }]
            updates["status"] = "cancelled"
        except Exception as e:
            logger.error(f"数据准备节点执行失败: {e}")
            error_message = str(e)
            error_type = "non_patent_mismatch" if ("非专文献数量不匹配" in error_message or "缺少对比文件" in error_message) else "data_preparation"
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

        patent_data_map = self._build_patent_data_map(search_results)
        application_number = str(office_action.get("application_number", "")).strip()
        non_patent_content_map = self._build_non_patent_content_map(non_patent_docs, uploaded_non_patent_files)

        normalized_comparison_documents = []
        for doc in comparison_documents:
            document_id = doc.get("document_id", "")
            document_number = doc.get("document_number", "")
            is_patent = bool(doc.get("is_patent", False))
            publication_date = doc.get("publication_date")

            if is_patent:
                data = patent_data_map.get(document_number, {})
            else:
                data = non_patent_content_map.get(document_id, "")

            normalized_comparison_documents.append({
                "document_id": document_id,
                "document_number": document_number,
                "is_patent": is_patent,
                "publication_date": publication_date,
                "data": data
            })

        response_content = self._get_parsed_content(parsed_files, "response")

        prepared_materials = {
            "original_patent": {
                "application_number": application_number,
                "data": patent_data_map.get(application_number, {})
            },
            "comparison_documents": normalized_comparison_documents,
            "office_action": {
                "application_number": application_number,
                "current_notice_round": int(office_action.get("current_notice_round", 0) or 0),
                "paragraphs": office_action.get("paragraphs", [])
            },
            "response": {
                "content": response_content
            },
            "claims_previous": {
                "content": self._get_parsed_content(parsed_files, "claims_previous")
            },
            "claims_current": {
                "content": self._get_parsed_content(parsed_files, "claims_current")
            },
            "local_retrieval": {},
        }

        prepared_materials["local_retrieval"] = self._build_local_retrieval_meta(prepared_materials)
        return prepared_materials

    def _build_local_retrieval_meta(self, prepared_materials: Dict[str, Any]) -> Dict[str, Any]:
        if not settings.LOCAL_RETRIEVAL_ENABLED:
            return {
                "enabled": False,
                "reason": "LOCAL_RETRIEVAL_ENABLED=false",
            }

        documents = self._build_local_retrieval_documents(prepared_materials)
        if not documents:
            return {
                "enabled": False,
                "reason": "no retrievable documents",
            }

        cache_dir = Path(getattr(self.config, "cache_dir", ".cache"))
        index_path = cache_dir / "local_retrieval.db"
        retriever = LocalEvidenceRetriever(
            db_path=str(index_path),
            chunk_chars=settings.LOCAL_RETRIEVAL_CHUNK_CHARS,
            chunk_overlap=settings.LOCAL_RETRIEVAL_CHUNK_OVERLAP,
        )
        return retriever.build_index(documents)

    def _build_local_retrieval_documents(self, prepared_materials: Dict[str, Any]) -> List[Dict[str, str]]:
        documents: List[Dict[str, str]] = []

        for doc in prepared_materials.get("comparison_documents", []) or []:
            item = self._to_dict(doc)
            doc_id = str(item.get("document_id", "")).strip()
            title = str(item.get("document_number", "")).strip()
            if not doc_id:
                continue
            content = self._extract_doc_content(item)
            if not content:
                continue
            documents.append(
                {
                    "doc_id": doc_id,
                    "title": title,
                    "source_type": "comparison_document",
                    "content": content,
                }
            )

        original_patent = self._to_dict(prepared_materials.get("original_patent", {}))
        original_data = self._to_dict(original_patent.get("data", {}))
        original_desc = self._extract_patent_description_text(original_data)
        original_number = str(original_patent.get("application_number", "")).strip()
        if original_desc and original_number:
            documents.append(
                {
                    "doc_id": "ORIGINAL_PATENT",
                    "title": original_number,
                    "source_type": "original_patent",
                    "content": original_desc,
                }
            )
        return documents

    def _build_patent_data_map(self, search_results) -> Dict[str, Dict[str, Any]]:
        """将 patent_retrieval 的新格式列表拍平成 map。"""
        patent_data_map: Dict[str, Dict[str, Any]] = {}
        for item in search_results or []:
            item_dict = self._to_dict(item)
            for patent_number, structured_data in item_dict.items():
                patent_data_map[str(patent_number)] = structured_data
        return patent_data_map

    def _build_non_patent_content_map(
        self,
        non_patent_docs: List[Dict[str, Any]],
        uploaded_non_patent_files: List[Dict[str, Any]],
    ) -> Dict[str, str]:
        """基于标题/作者/期刊匹配上传非专文件，必要时按顺序兜底映射。"""
        if not non_patent_docs:
            return {}

        content_map: Dict[str, str] = {}
        remaining_indices = set(range(len(uploaded_non_patent_files)))
        missing_doc_labels: List[str] = []
        doc_candidates: Dict[str, List[int]] = {}
        all_missing = True

        for doc in non_patent_docs:
            document_id = str(doc.get("document_id", "")).strip()
            document_number = str(doc.get("document_number", "")).strip()
            metadata = self._extract_non_patent_metadata(document_number)
            title = metadata["title"]

            if not document_id:
                raise ValueError(f"非专文献映射失败: document_number={document_number} 缺少 document_id")
            if not title:
                raise ValueError(f"非专文献映射失败: {document_id} 的 document_number 为空，无法提取标题")

            candidate_indices = self._collect_doc_candidate_indices(metadata, uploaded_non_patent_files, remaining_indices)
            doc_candidates[document_id] = candidate_indices
            if candidate_indices:
                all_missing = False

            if len(candidate_indices) == 1:
                matched_idx = candidate_indices[0]
                remaining_indices.remove(matched_idx)
                content_map[document_id] = str(uploaded_non_patent_files[matched_idx].get("content", ""))
                continue

            if len(candidate_indices) > 1:
                narrowed_indices = self._disambiguate_doc_candidates(
                    metadata,
                    uploaded_non_patent_files,
                    candidate_indices,
                )
                doc_candidates[document_id] = narrowed_indices
                if len(narrowed_indices) == 1:
                    matched_idx = narrowed_indices[0]
                    remaining_indices.remove(matched_idx)
                    content_map[document_id] = str(uploaded_non_patent_files[matched_idx].get("content", ""))
                    continue

        unresolved_docs = [
            doc for doc in non_patent_docs
            if str(doc.get("document_id", "")).strip() not in content_map
        ]

        if unresolved_docs and all_missing and len(unresolved_docs) == len(remaining_indices):
            for doc, idx in zip(unresolved_docs, sorted(remaining_indices)):
                document_id = str(doc.get("document_id", "")).strip()
                content_map[document_id] = str(uploaded_non_patent_files[idx].get("content", ""))
            return content_map

        for doc in unresolved_docs:
            document_id = str(doc.get("document_id", "")).strip()
            candidate_indices = doc_candidates.get(document_id, [])
            if candidate_indices:
                matched_files = [
                    self._non_patent_file_label(uploaded_non_patent_files[idx])
                    for idx in candidate_indices
                ]
                raise ValueError(
                    f"非专文献映射冲突: {document_id} 匹配到多个上传文件: {', '.join(matched_files)}"
                )
            missing_doc_labels.append(self._comparison_document_label(doc))

        if missing_doc_labels:
            uploaded_count = len(uploaded_non_patent_files)
            message = f"缺少对比文件，请上传：{'、'.join(missing_doc_labels)}。"
            if uploaded_count:
                message += f" 当前已上传 {uploaded_count} 份对比文件，但仍未匹配到上述文献。"
            raise ValueError(message)

        return content_map

    def _comparison_document_label(self, doc: Dict[str, Any]) -> str:
        document_id = str(doc.get("document_id", "")).strip()
        document_number = str(doc.get("document_number", "")).strip()
        if document_id and document_number:
            return f"{document_id}（{document_number}）"
        if document_id:
            return document_id
        if document_number:
            return document_number
        return "<unknown>"

    def _extract_non_patent_title(self, document_number: str) -> str:
        """提取 document_number 中第一个逗号（中英文）之前的内容作为标题。"""
        normalized_number = str(document_number or "").strip()
        if not normalized_number:
            return ""
        title = re.split(r"[,，]", normalized_number, maxsplit=1)[0].strip()
        return title.strip("\"'“”‘’「」『』《》〈〉")

    def _extract_non_patent_metadata(self, document_number: str) -> Dict[str, Any]:
        normalized_number = str(document_number or "").strip()
        title = self._extract_non_patent_title(normalized_number)
        remainder = self._strip_leading_title_segment(normalized_number, title)
        authors_text, journal_text = self._split_authors_and_journal(remainder)
        authors = self._split_authors(authors_text)
        return {
            "title": title,
            "authors": authors,
            "journal": journal_text,
        }

    def _strip_leading_title_segment(self, document_number: str, title: str) -> str:
        normalized_number = str(document_number or "").strip()
        normalized_title = str(title or "").strip()
        if not normalized_number or not normalized_title:
            return normalized_number
        title_pattern = re.escape(normalized_title)
        patterns = [
            rf'^[\"\'“”‘’「」『』《》〈〉]*{title_pattern}[\"\'“”‘’「」『』《》〈〉]*',
        ]
        remainder = normalized_number
        for pattern in patterns:
            new_remainder = re.sub(pattern, "", normalized_number, count=1).lstrip("，,;；:： ")
            if new_remainder != normalized_number:
                remainder = new_remainder
                break
        return remainder

    def _split_authors_and_journal(self, text: str) -> tuple[str, str]:
        normalized = str(text or "").strip()
        if not normalized:
            return "", ""
        first_book = re.search(r"[《〈<]", normalized)
        if first_book:
            idx = first_book.start()
            journal_segment = normalized[idx:].strip()
            return normalized[:idx].strip("，,;；:： "), self._extract_journal_name(journal_segment)
        parts = re.split(r"[,，]", normalized, maxsplit=1)
        if len(parts) == 2:
            return parts[0].strip(), self._extract_journal_name(parts[1].strip())
        return normalized, ""

    def _extract_journal_name(self, text: str) -> str:
        normalized = str(text or "").strip()
        if not normalized:
            return ""
        bracket_match = re.search(r"[《〈<]([^》〉>]+)[》〉>]", normalized)
        if bracket_match:
            return bracket_match.group(1).strip()
        cutoff = re.search(r"(第\s*\d+\s*卷|第\s*\d+\s*期|\d+\s*-\s*\d+\s*页|\d{4}\s*年)", normalized)
        if cutoff:
            normalized = normalized[:cutoff.start()].strip("，,;；:： ")
        return normalized.strip("\"'“”‘’「」『』《》〈〉")

    def _split_authors(self, authors_text: str) -> List[str]:
        normalized = str(authors_text or "").strip()
        if not normalized:
            return []
        parts = re.split(r"[;；,/，,\s]+", normalized)
        authors: List[str] = []
        for part in parts:
            author = self._normalize_non_patent_lookup_text(part)
            if len(author) >= 2:
                authors.append(author)
        return list(dict.fromkeys(authors))

    def _title_in_content(self, title: str, content: str) -> bool:
        if not title or not content:
            return False
        if title in content:
            return True
        normalized_title = self._normalize_non_patent_lookup_text(title)
        normalized_content = self._normalize_non_patent_lookup_text(content)
        return bool(normalized_title) and normalized_title in normalized_content

    def _normalize_non_patent_lookup_text(self, text: str) -> str:
        normalized = re.sub(r"\s+", "", str(text or ""))
        normalized = re.sub(r"[\"'“”‘’「」『』《》〈〉]", "", normalized)
        normalized = re.sub(r"[，,；;：:。.!！?？·•／/\\\\|()\[\]{}<>-]+", "", normalized)
        return normalized

    def _collect_doc_candidate_indices(
        self,
        metadata: Dict[str, Any],
        uploaded_non_patent_files: List[Dict[str, Any]],
        remaining_indices: set[int],
    ) -> List[int]:
        candidates: List[int] = []
        for idx in sorted(remaining_indices):
            item = uploaded_non_patent_files[idx]
            matches = self._build_match_flags(metadata, item)
            if any(matches.values()):
                candidates.append(idx)
        return candidates

    def _disambiguate_doc_candidates(
        self,
        metadata: Dict[str, Any],
        uploaded_non_patent_files: List[Dict[str, Any]],
        candidate_indices: List[int],
    ) -> List[int]:
        narrowed = list(candidate_indices)
        for key in ("title", "authors", "journal"):
            matched = []
            for idx in narrowed:
                flags = self._build_match_flags(metadata, uploaded_non_patent_files[idx])
                if flags.get(key):
                    matched.append(idx)
            if len(matched) == 1:
                return matched
            if matched:
                narrowed = matched
        return narrowed

    def _build_match_flags(self, metadata: Dict[str, Any], uploaded_file: Dict[str, Any]) -> Dict[str, bool]:
        searchable_text = self._build_uploaded_file_search_text(uploaded_file)
        title = str(metadata.get("title", "")).strip()
        authors = metadata.get("authors", []) or []
        journal = str(metadata.get("journal", "")).strip()
        return {
            "title": self._field_in_text(title, searchable_text),
            "authors": any(self._field_in_text(author, searchable_text) for author in authors),
            "journal": self._field_in_text(journal, searchable_text),
        }

    def _build_uploaded_file_search_text(self, item: Dict[str, Any]) -> str:
        content = str(item.get("content", "")).strip()
        file_name = str(item.get("file_name", "")).strip()
        file_path = str(item.get("file_path", "")).strip()
        path_name = os.path.basename(file_path) if file_path else ""
        return "\n".join(part for part in [content, file_name, path_name] if part)

    def _field_in_text(self, field: str, text: str) -> bool:
        normalized_field = self._normalize_non_patent_lookup_text(field)
        normalized_text = self._normalize_non_patent_lookup_text(text)
        if not normalized_field or not normalized_text:
            return False
        return normalized_field in normalized_text

    def _non_patent_file_label(self, item: Dict[str, Any]) -> str:
        file_path = str(item.get("file_path", "")).strip()
        if file_path:
            return file_path
        markdown_path = str(item.get("markdown_path", "")).strip()
        if markdown_path:
            return markdown_path
        return "<unknown>"

    def _get_parsed_content(self, parsed_files, file_type: str) -> str:
        for item in parsed_files or []:
            parsed = self._to_dict(item)
            if parsed.get("file_type") == file_type:
                return parsed.get("content", "")
        return ""

    def _extract_doc_content(self, doc: Dict[str, Any]) -> str:
        is_patent = bool(doc.get("is_patent", False))
        data = doc.get("data")
        if is_patent:
            return self._extract_patent_description_text(self._to_dict(data))
        if isinstance(data, str):
            return data.strip()
        if isinstance(data, dict):
            return json.dumps(data, ensure_ascii=False)
        return ""

    def _extract_patent_description_text(self, data: Dict[str, Any]) -> str:
        data_dict = self._to_dict(data)
        description = self._to_dict(data_dict.get("description", {}))
        detailed = str(description.get("detailed_description", "")).strip()
        if detailed:
            return detailed
        abstract = str(data_dict.get("abstract", "")).strip()
        claims = data_dict.get("claims", [])
        claim_texts: List[str] = []
        if isinstance(claims, list):
            for item in claims[:20]:
                claim = self._to_dict(item)
                text = str(claim.get("claim_text", "")).strip()
                if text:
                    claim_texts.append(text)
        merged = "\n".join([part for part in [abstract, "\n".join(claim_texts)] if part])
        return merged.strip()

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
