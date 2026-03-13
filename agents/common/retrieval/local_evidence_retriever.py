"""
任务级本地全文检索器（SQLite FTS5）。

能力：
1. build_index: 建立任务级索引
2. search: 检索候选片段
3. read: 按 chunk 读取邻近上下文
4. build_evidence_cards: 将候选压缩为短证据卡，控制 token 占用
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from loguru import logger


class LocalEvidenceRetriever:
    """Reusable local retriever for task-level corpora."""

    INDEX_VERSION = "v1"

    def __init__(
        self,
        db_path: str,
        chunk_chars: int = 600,
        chunk_overlap: int = 120,
    ):
        self.db_path = Path(db_path)
        self.chunk_chars = max(200, int(chunk_chars))
        self.chunk_overlap = max(0, int(chunk_overlap))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._fts_enabled = False
        self._ensure_db()

    def build_index(self, documents: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        """
        重建任务级索引。

        Args:
            documents: 每项至少包含 doc_id/content，支持 title/source_type
        """
        rows: List[Tuple[str, str, str, str, int, str]] = []
        doc_count = 0
        for doc in documents or []:
            doc_id = str(doc.get("doc_id", "")).strip()
            content = self._normalize_text(doc.get("content", ""))
            if not doc_id or not content:
                continue
            doc_count += 1
            source_type = str(doc.get("source_type", "")).strip() or "local_document"
            for chunk_index, chunk_text in self._split_chunks(content):
                section_type = self._detect_section_type(chunk_text)
                chunk_id = self._build_chunk_id(doc_id, chunk_index, chunk_text)
                rows.append((chunk_id, doc_id, source_type, section_type, chunk_index, chunk_text))

        with self._connect() as conn:
            conn.execute("DELETE FROM chunks")
            if self._fts_enabled:
                conn.execute("DELETE FROM chunks_fts")

            conn.executemany(
                """
                INSERT INTO chunks(chunk_id, doc_id, source_type, section_type, chunk_index, text)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            if self._fts_enabled:
                conn.executemany(
                    "INSERT INTO chunks_fts(chunk_id, text) VALUES(?, ?)",
                    [(item[0], item[5]) for item in rows],
                )
            conn.commit()

        return {
            "enabled": True,
            "backend": "sqlite_fts5" if self._fts_enabled else "sqlite_like",
            "index_version": self.INDEX_VERSION,
            "index_path": str(self.db_path),
            "chunk_chars": self.chunk_chars,
            "chunk_overlap": self.chunk_overlap,
            "document_count": doc_count,
            "chunk_count": len(rows),
        }

    def search(
        self,
        query: str,
        intent: str,
        doc_filters: Optional[Sequence[str]] = None,
        top_k: int = 20,
    ) -> List[Dict[str, Any]]:
        """检索候选片段并按启发式重排。"""
        normalized_query = self._normalize_text(query)
        if not normalized_query:
            return []

        terms = self._extract_terms(normalized_query)
        if not terms:
            return []

        filters = [str(item).strip() for item in (doc_filters or []) if str(item).strip()]
        raw_rows = self._search_rows(terms=terms, filters=filters, limit=max(10, top_k * 4))

        scored: List[Dict[str, Any]] = []
        for row in raw_rows:
            text = self._normalize_text(row.get("text", ""))
            if not text:
                continue
            term_hits = [term for term in terms if term and term in text]
            coverage = float(len(term_hits)) / float(max(1, len(terms)))
            section_type = str(row.get("section_type", "")).strip() or "other"
            section_boost = self._section_boost(intent=intent, section_type=section_type)
            filter_boost = 0.15 if filters and str(row.get("doc_id", "")) in filters else 0.0
            bm25_raw = row.get("bm25_score")
            bm25_score = 0.0
            if bm25_raw is not None:
                try:
                    bm25_score = 1.0 / (1.0 + max(0.0, float(bm25_raw)))
                except Exception:
                    bm25_score = 0.0

            score = round(coverage * 0.65 + bm25_score * 0.35 + section_boost + filter_boost, 6)
            scored.append(
                {
                    "candidate_id": row.get("chunk_id"),
                    "chunk_id": row.get("chunk_id"),
                    "doc_id": row.get("doc_id"),
                    "source_type": row.get("source_type", "local_document"),
                    "section_type": section_type,
                    "chunk_index": row.get("chunk_index", 0),
                    "location": f"chunk:{row.get('chunk_index', 0)}",
                    "text": text,
                    "score": score,
                    "match_terms": term_hits[:6],
                }
            )

        deduped: List[Dict[str, Any]] = []
        seen_ids: Set[str] = set()
        for item in sorted(scored, key=lambda x: x.get("score", 0.0), reverse=True):
            chunk_id = str(item.get("chunk_id", "")).strip()
            if not chunk_id or chunk_id in seen_ids:
                continue
            seen_ids.add(chunk_id)
            deduped.append(item)
            if len(deduped) >= top_k:
                break
        return deduped

    def read(self, chunk_ids: Sequence[str], window: int = 1) -> List[Dict[str, Any]]:
        """读取命中 chunk 附近上下文。"""
        cleaned_ids = [str(item).strip() for item in (chunk_ids or []) if str(item).strip()]
        if not cleaned_ids:
            return []

        window = max(0, int(window))
        outputs: List[Dict[str, Any]] = []
        with self._connect() as conn:
            for chunk_id in cleaned_ids:
                row = conn.execute(
                    """
                    SELECT chunk_id, doc_id, source_type, section_type, chunk_index, text
                    FROM chunks
                    WHERE chunk_id = ?
                    """,
                    (chunk_id,),
                ).fetchone()
                if not row:
                    continue

                base_doc_id = str(row["doc_id"])
                base_index = int(row["chunk_index"])
                left = max(0, base_index - window)
                right = base_index + window
                around_rows = conn.execute(
                    """
                    SELECT chunk_id, doc_id, source_type, section_type, chunk_index, text
                    FROM chunks
                    WHERE doc_id = ? AND chunk_index >= ? AND chunk_index <= ?
                    ORDER BY chunk_index ASC
                    """,
                    (base_doc_id, left, right),
                ).fetchall()
                context_text = "\n".join(self._normalize_text(item["text"]) for item in around_rows if item["text"])
                outputs.append(
                    {
                        "chunk_id": chunk_id,
                        "doc_id": base_doc_id,
                        "source_type": str(row["source_type"] or "local_document"),
                        "section_type": str(row["section_type"] or "other"),
                        "location": f"chunk:{left}-{right}",
                        "text": context_text,
                    }
                )
        return outputs

    def build_evidence_cards(
        self,
        candidates: Sequence[Dict[str, Any]],
        context_k: int,
        max_context_chars: int,
        max_quote_chars: int,
        read_window: int = 1,
    ) -> Dict[str, Any]:
        """压缩候选为短证据卡，限制总文本预算。"""
        context_k = max(1, int(context_k))
        max_context_chars = max(100, int(max_context_chars))
        max_quote_chars = max(20, int(max_quote_chars))
        read_window = max(0, int(read_window))

        merged = [self._to_candidate(item) for item in (candidates or [])]
        merged = [item for item in merged if item]
        if not merged:
            return {"cards": [], "trace": self._empty_card_trace()}

        local_chunk_ids = [
            str(item.get("chunk_id", "")).strip()
            for item in merged
            if str(item.get("chunk_id", "")).strip()
        ]
        read_map = {
            str(item.get("chunk_id", "")).strip(): item
            for item in self.read(local_chunk_ids, window=read_window)
            if str(item.get("chunk_id", "")).strip()
        }

        ranked = sorted(merged, key=lambda x: float(x.get("score", 0.0)), reverse=True)
        cards: List[Dict[str, Any]] = []
        selected_ids: List[str] = []
        dropped_ids: List[str] = []
        total_chars = 0

        for item in ranked:
            candidate_id = str(item.get("candidate_id", "")).strip() or str(item.get("chunk_id", "")).strip()
            chunk_id = str(item.get("chunk_id", "")).strip()
            read_item = read_map.get(chunk_id, {})
            raw_text = self._normalize_text(read_item.get("text") or item.get("text") or "")
            quote = self._trim_text(raw_text, max_quote_chars)
            if not quote:
                continue

            if cards and total_chars + len(quote) > max_context_chars:
                dropped_ids.append(candidate_id)
                continue

            doc_id = str(item.get("doc_id", "")).strip()
            source_type = str(item.get("source_type", "")).strip() or "local_document"
            section_type = str(item.get("section_type", "")).strip() or "other"
            location = str(item.get("location", "")).strip() or str(read_item.get("location", "")).strip()
            analysis = str(item.get("analysis", "")).strip() or self._default_analysis(
                source_type=source_type,
                section_type=section_type,
                match_terms=item.get("match_terms", []),
            )
            cards.append(
                {
                    "candidate_id": candidate_id,
                    "chunk_id": chunk_id or None,
                    "doc_id": doc_id,
                    "quote": quote,
                    "location": location,
                    "analysis": analysis,
                    "source_url": str(item.get("source_url", "")).strip() or None,
                    "source_title": str(item.get("source_title", "")).strip() or None,
                    "source_type": source_type,
                    "score": float(item.get("score", 0.0)),
                }
            )
            selected_ids.append(candidate_id)
            total_chars += len(quote)
            if len(cards) >= context_k:
                break

        return {
            "cards": cards,
            "trace": {
                "selected_candidates": selected_ids,
                "dropped_candidates": dropped_ids,
                "context_chars": total_chars,
                "context_k": context_k,
                "max_context_chars": max_context_chars,
                "max_quote_chars": max_quote_chars,
            },
        }

    def _ensure_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chunk_id TEXT UNIQUE NOT NULL,
                    doc_id TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    section_type TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    text TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc_chunk ON chunks(doc_id, chunk_index)")
            try:
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
                    USING fts5(chunk_id UNINDEXED, text, tokenize='unicode61')
                    """
                )
                self._fts_enabled = True
            except sqlite3.OperationalError as ex:
                self._fts_enabled = False
                logger.warning(f"SQLite FTS5 不可用，将降级 LIKE 检索: {ex}")
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _split_chunks(self, text: str) -> List[Tuple[int, str]]:
        step = max(1, self.chunk_chars - self.chunk_overlap)
        chunks: List[Tuple[int, str]] = []
        start = 0
        index = 0
        length = len(text)
        while start < length:
            end = min(length, start + self.chunk_chars)
            chunk_text = self._normalize_text(text[start:end])
            if chunk_text:
                chunks.append((index, chunk_text))
            if end >= length:
                break
            start += step
            index += 1
        return chunks

    def _search_rows(self, terms: List[str], filters: List[str], limit: int) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        with self._connect() as conn:
            if self._fts_enabled:
                match_expr = self._build_match_expression(terms)
                if match_expr:
                    sql = (
                        "SELECT c.chunk_id, c.doc_id, c.source_type, c.section_type, c.chunk_index, c.text, "
                        "bm25(chunks_fts) AS bm25_score "
                        "FROM chunks_fts JOIN chunks c ON c.chunk_id = chunks_fts.chunk_id "
                        "WHERE chunks_fts MATCH ? "
                    )
                    params: List[Any] = [match_expr]
                    if filters:
                        placeholders = ",".join("?" for _ in filters)
                        sql += f"AND c.doc_id IN ({placeholders}) "
                        params.extend(filters)
                    sql += "ORDER BY bm25(chunks_fts) ASC LIMIT ?"
                    params.append(limit)
                    rows = conn.execute(sql, params).fetchall()
                    for row in rows:
                        results.append(dict(row))

            if results:
                return results

            where_clauses: List[str] = []
            params = []
            if filters:
                placeholders = ",".join("?" for _ in filters)
                where_clauses.append(f"doc_id IN ({placeholders})")
                params.extend(filters)

            like_clauses = []
            for term in terms[:6]:
                like_clauses.append("text LIKE ?")
                params.append(f"%{term}%")
            if like_clauses:
                where_clauses.append("(" + " OR ".join(like_clauses) + ")")

            where_sql = ""
            if where_clauses:
                where_sql = "WHERE " + " AND ".join(where_clauses)

            sql = (
                "SELECT chunk_id, doc_id, source_type, section_type, chunk_index, text "
                f"FROM chunks {where_sql} ORDER BY chunk_index ASC LIMIT ?"
            )
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]

    def _build_match_expression(self, terms: List[str]) -> str:
        clauses = []
        for term in terms[:8]:
            sanitized = term.replace('"', " ").strip()
            if not sanitized:
                continue
            clauses.append(f'"{sanitized}"')
        return " OR ".join(clauses)

    def _extract_terms(self, query: str) -> List[str]:
        raw_terms = re.split(r"[\s,，。；;:：、（）()\[\]{}|]+", query)
        stop_words = {"一种", "方法", "系统", "装置", "包括", "用于", "实现", "所述", "技术", "特征"}
        terms: List[str] = []
        for token in raw_terms:
            value = token.strip()
            if len(value) < 2 or value in stop_words:
                continue
            if value not in terms:
                terms.append(value)
        return terms[:20]

    def _section_boost(self, intent: str, section_type: str) -> float:
        normalized_intent = str(intent or "").strip().lower()
        normalized_section = str(section_type or "").strip().lower()
        if normalized_intent == "embodiment" and normalized_section == "embodiment":
            return 0.35
        if normalized_intent == "fact_verification" and normalized_section in {"claim", "embodiment"}:
            return 0.2
        if normalized_intent == "common_knowledge" and normalized_section == "other":
            return 0.05
        return 0.0

    def _detect_section_type(self, text: str) -> str:
        value = text or ""
        if re.search(r"(具体实施方式|实施例|优选实施例|实施方式)", value):
            return "embodiment"
        if re.search(r"(权利要求|claim\s*\d+)", value, re.I):
            return "claim"
        if re.search(r"(摘要|abstract)", value, re.I):
            return "abstract"
        return "other"

    def _build_chunk_id(self, doc_id: str, chunk_index: int, chunk_text: str) -> str:
        digest = hashlib.sha1(f"{doc_id}:{chunk_index}:{chunk_text[:200]}".encode("utf-8")).hexdigest()[:10]
        return f"{doc_id}_{chunk_index}_{digest}"

    def _default_analysis(self, source_type: str, section_type: str, match_terms: Sequence[str]) -> str:
        terms = [str(item).strip() for item in (match_terms or []) if str(item).strip()]
        term_hint = f"关键词命中：{', '.join(terms[:3])}" if terms else "命中相关技术描述"
        return f"来自 {source_type}/{section_type} 片段，{term_hint}。"

    def _to_candidate(self, item: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(item, dict):
            return {}
        candidate = {
            "candidate_id": str(item.get("candidate_id", "")).strip() or str(item.get("chunk_id", "")).strip(),
            "chunk_id": str(item.get("chunk_id", "")).strip(),
            "doc_id": str(item.get("doc_id", "")).strip(),
            "source_type": str(item.get("source_type", "")).strip() or "local_document",
            "section_type": str(item.get("section_type", "")).strip() or "other",
            "location": str(item.get("location", "")).strip(),
            "text": self._normalize_text(item.get("text", "")),
            "analysis": str(item.get("analysis", "")).strip(),
            "source_url": str(item.get("source_url", "")).strip(),
            "source_title": str(item.get("source_title", "")).strip(),
            "score": self._safe_float(item.get("score"), 0.0),
            "match_terms": item.get("match_terms", []),
        }
        if not candidate["doc_id"]:
            return {}
        return candidate

    def _trim_text(self, text: str, limit: int) -> str:
        normalized = self._normalize_text(text)
        if len(normalized) <= limit:
            return normalized
        return normalized[: max(1, limit - 3)].rstrip() + "..."

    def _normalize_text(self, value: Any) -> str:
        text = str(value or "")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _safe_float(self, value: Any, default: float) -> float:
        try:
            return float(value)
        except Exception:
            return float(default)

    def _empty_card_trace(self) -> Dict[str, Any]:
        return {
            "selected_candidates": [],
            "dropped_candidates": [],
            "context_chars": 0,
            "context_k": 0,
            "max_context_chars": 0,
            "max_quote_chars": 0,
        }
