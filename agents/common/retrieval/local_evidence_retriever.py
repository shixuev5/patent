"""
任务级本地混合检索器（FTS5 + sqlite-vec + BGE-M3）。

能力：
1. build_index: 建立任务级 hybrid 索引
2. search: lexical + dense 并行召回与融合
3. read: 按 chunk 读取邻近上下文
4. build_evidence_cards: 将候选压缩为短证据卡
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np
from loguru import logger
from openai import OpenAI

from config import settings


@dataclass
class EmbeddingConfig:
    model_name: str
    batch_size: int = 10


class LanguageRouter:
    _PATTERNS = {
        "zh": re.compile(r"[\u4e00-\u9fff]"),
        "ja": re.compile(r"[\u3040-\u30ff]"),
        "ko": re.compile(r"[\uac00-\ud7af]"),
        "en": re.compile(r"[A-Za-z]"),
    }

    def detect(self, text: str) -> str:
        counts = self.language_distribution(text)
        active = [lang for lang, count in counts.items() if count > 0]
        if not active:
            return "other"
        if len(active) >= 2:
            return "mixed"
        return active[0]

    def language_distribution(self, text: str) -> Dict[str, int]:
        value = str(text or "")
        counts = {lang: len(pattern.findall(value)) for lang, pattern in self._PATTERNS.items()}
        return counts


class ChunkBuilder:
    def __init__(self, chunk_chars: int, chunk_overlap: int):
        self.chunk_chars = max(200, int(chunk_chars))
        self.chunk_overlap = max(0, int(chunk_overlap))

    def split(self, text: str) -> List[Tuple[int, str]]:
        normalized = self._normalize_text(text)
        if not normalized:
            return []

        units = self._split_structured_units(normalized)
        chunks: List[Tuple[int, str]] = []
        chunk_index = 0
        for unit in units:
            unit = self._normalize_text(unit)
            if not unit:
                continue
            if len(unit) <= self.chunk_chars:
                chunks.append((chunk_index, unit))
                chunk_index += 1
                continue
            for item in self._split_with_sliding_window(unit):
                chunks.append((chunk_index, item))
                chunk_index += 1
        return chunks

    def _split_structured_units(self, text: str) -> List[str]:
        markers = [
            r"\n(?=(?:#+\s))",
            r"\n(?=(?:摘要|abstract|背景技术|发明内容|具体实施方式|实施例|claims?|权利要求|請求項|청구항))",
            r"\n(?=(?:\d+\.\s|\(\d+\)\s))",
        ]
        parts = [text]
        for pattern in markers:
            updated: List[str] = []
            for item in parts:
                updated.extend(re.split(pattern, item, flags=re.I))
            parts = updated
        return [item for item in parts if self._normalize_text(item)]

    def _split_with_sliding_window(self, text: str) -> List[str]:
        step = max(1, self.chunk_chars - self.chunk_overlap)
        chunks: List[str] = []
        start = 0
        length = len(text)
        while start < length:
            end = min(length, start + self.chunk_chars)
            item = self._normalize_text(text[start:end])
            if item:
                chunks.append(item)
            if end >= length:
                break
            start += step
        return chunks

    def _normalize_text(self, value: Any) -> str:
        text = str(value or "")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


class EmbeddingProvider:
    def __init__(self, config: EmbeddingConfig):
        self.config = config
        api_key = settings.LOCAL_RETRIEVAL_EMBEDDING_API_KEY or settings.LLM_API_KEY
        base_url = settings.LOCAL_RETRIEVAL_EMBEDDING_BASE_URL or settings.LLM_BASE_URL
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._api_key = api_key
        self._base_url = base_url
        self._dim: Optional[int] = None

    @property
    def embedding_dim(self) -> int:
        if self._dim is None:
            probe = self.encode_passages(["embedding dimension probe"])
            self._dim = len(probe[0]) if probe else 0
        return self._dim or 0

    def encode_queries(self, texts: Sequence[str]) -> List[List[float]]:
        return self._encode([str(text) for text in texts])

    def encode_passages(self, texts: Sequence[str]) -> List[List[float]]:
        return self._encode([str(text) for text in texts])

    def _encode(self, texts: Sequence[str]) -> List[List[float]]:
        values = [str(item or "").strip() for item in texts]
        if not values:
            return []
        outputs: List[List[float]] = []
        batch_size = max(1, min(self.config.batch_size, 10))
        for start in range(0, len(values), batch_size):
            batch = values[start : start + batch_size]
            response = self._client.embeddings.create(
                model=self.config.model_name,
                input=batch,
            )
            vectors = [list(item.embedding) for item in response.data]
            if vectors:
                matrix = np.asarray(vectors, dtype=np.float32)
                denom = np.linalg.norm(matrix, axis=1, keepdims=True)
                denom = np.where(denom == 0, 1.0, denom)
                matrix = matrix / denom
                outputs.extend(matrix.astype(np.float32).tolist())
        if outputs and self._dim is None:
            self._dim = len(outputs[0])
        return outputs


class SQLiteHybridIndex:
    SCHEMA_VERSION = "v2"

    def __init__(self, db_path: Path, embedding_config: EmbeddingConfig):
        self.db_path = db_path
        self.embedding_config = embedding_config
        self._fts_enabled = False
        self._vec_enabled = False
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_db()

    def build(
        self,
        documents: Sequence[Dict[str, Any]],
        chunk_builder: ChunkBuilder,
        language_router: LanguageRouter,
        embedding_provider: EmbeddingProvider,
    ) -> Dict[str, Any]:
        current_signature = self._build_model_signature()
        existing_signature = self._get_meta_value("embedding_signature")
        if (
            existing_signature
            and existing_signature != current_signature
        ):
            logger.info("本地检索 embedding 配置发生变化，重建索引")

        doc_rows: List[Tuple[str, str, str, str, str, str, str]] = []
        chunk_rows: List[Tuple[str, str, int, int, int, str, str, int, str]] = []
        fts_rows: List[Tuple[str, str]] = []
        embedding_rows: List[Tuple[str, str, int, str]] = []
        indexed_languages: Set[str] = set()

        for doc in documents or []:
            doc_id = str(doc.get("doc_id", "")).strip()
            content = self._normalize_text(doc.get("content", ""))
            if not doc_id or not content:
                continue
            title = str(doc.get("title", "")).strip()
            source_type = str(doc.get("source_type", "")).strip() or "local_document"
            lang_dist = language_router.language_distribution(content)
            doc_language = language_router.detect(content)
            indexed_languages.add(doc_language)
            doc_rows.append(
                (
                    doc_id,
                    title,
                    source_type,
                    doc_language,
                    json.dumps(lang_dist, ensure_ascii=False),
                    hashlib.sha1(content.encode("utf-8")).hexdigest(),
                    self.SCHEMA_VERSION,
                )
            )
            split_chunks = chunk_builder.split(content)
            chunk_texts: List[str] = []
            chunk_ids: List[str] = []
            chunk_languages: List[str] = []
            chunk_sections: List[str] = []
            for chunk_index, chunk_text in split_chunks:
                chunk_id = self._build_chunk_id(doc_id, chunk_index, chunk_text)
                section_type = self._detect_section_type(chunk_text)
                chunk_language = language_router.detect(chunk_text)
                indexed_languages.add(chunk_language)
                chunk_rows.append(
                    (
                        chunk_id,
                        doc_id,
                        chunk_index,
                        0,
                        0,
                        section_type,
                        chunk_language,
                        len(chunk_text),
                        chunk_text,
                    )
                )
                fts_rows.append((chunk_id, chunk_text))
                chunk_ids.append(chunk_id)
                chunk_texts.append(chunk_text)
                chunk_languages.append(chunk_language)
                chunk_sections.append(section_type)

            if chunk_texts:
                embeddings = embedding_provider.encode_passages(chunk_texts)
                for chunk_id, vector in zip(chunk_ids, embeddings):
                    embedding_rows.append(
                        (
                            chunk_id,
                            self.embedding_config.model_name,
                            len(vector),
                            json.dumps(vector, ensure_ascii=False),
                        )
                    )

        with self._connect() as conn:
            self._recreate_schema(conn)
            conn.executemany(
                """
                INSERT INTO documents(doc_id, title, source_type, doc_language, language_distribution_json, content_sha1, parser_version)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                doc_rows,
            )
            conn.executemany(
                """
                INSERT INTO chunks(chunk_id, doc_id, chunk_index, page_start, page_end, section_type, chunk_language, token_length, text)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                chunk_rows,
            )
            if self._fts_enabled:
                conn.executemany("INSERT INTO chunks_fts(chunk_id, text) VALUES(?, ?)", fts_rows)
            conn.executemany(
                """
                INSERT INTO chunk_embeddings(chunk_id, model_name, embedding_dim, embedding)
                VALUES(?, ?, ?, ?)
                """,
                embedding_rows,
            )
            meta_items = {
                "schema_version": self.SCHEMA_VERSION,
                "embedding_model": self.embedding_config.model_name,
                "embedding_signature": current_signature,
                "embedding_dim": str(embedding_provider.embedding_dim),
                "embedding_normalize": json.dumps(True),
                "vec_enabled": json.dumps(self._vec_enabled),
            }
            conn.executemany(
                "INSERT INTO retrieval_meta(key, value) VALUES(?, ?)",
                list(meta_items.items()),
            )
            conn.commit()

        return {
            "enabled": True,
            "index_path": str(self.db_path),
            "schema_version": self.SCHEMA_VERSION,
            "embedding_model": self.embedding_config.model_name,
            "embedding_dim": embedding_provider.embedding_dim,
            "chunk_chars": chunk_builder.chunk_chars,
            "chunk_overlap": chunk_builder.chunk_overlap,
            "document_count": len(doc_rows),
            "chunk_count": len(chunk_rows),
            "indexed_languages": sorted(indexed_languages),
            "vector_enabled": True,
            "documents": [{"doc_id": row[0], "source_type": row[2], "doc_language": row[3]} for row in doc_rows],
        }

    def search_lexical(self, terms: Sequence[str], filters: Sequence[str], limit: int) -> List[Dict[str, Any]]:
        if not terms:
            return []
        with self._connect() as conn:
            if self._fts_enabled:
                match_expr = " OR ".join(f'"{self._sanitize_match_term(term)}"' for term in terms[:12] if term)
                if match_expr:
                    sql = (
                        "SELECT c.chunk_id, c.doc_id, c.chunk_index, c.section_type, c.chunk_language, c.text, "
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
                    if rows:
                        return [dict(row) for row in rows]

            like_clauses = []
            params = []
            if filters:
                placeholders = ",".join("?" for _ in filters)
                like_clauses.append(f"doc_id IN ({placeholders})")
                params.extend(filters)
            term_sql = []
            for term in terms[:8]:
                term_sql.append("text LIKE ?")
                params.append(f"%{term}%")
            if term_sql:
                like_clauses.append("(" + " OR ".join(term_sql) + ")")
            sql = "SELECT chunk_id, doc_id, chunk_index, section_type, chunk_language, text FROM chunks"
            if like_clauses:
                sql += " WHERE " + " AND ".join(like_clauses)
            sql += " ORDER BY chunk_index ASC LIMIT ?"
            params.append(limit)
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def search_dense(
        self,
        query_vector: Sequence[float],
        filters: Sequence[str],
        limit: int,
    ) -> List[Dict[str, Any]]:
        if not query_vector:
            return []
        with self._connect() as conn:
            sql = (
                "SELECT e.chunk_id, c.doc_id, c.chunk_index, c.section_type, c.chunk_language, c.text, e.embedding "
                "FROM chunk_embeddings e JOIN chunks c ON c.chunk_id = e.chunk_id "
            )
            params: List[Any] = []
            if filters:
                placeholders = ",".join("?" for _ in filters)
                sql += f"WHERE c.doc_id IN ({placeholders}) "
                params.extend(filters)
            rows = conn.execute(sql, params).fetchall()

        query_arr = np.asarray(query_vector, dtype=np.float32)
        scored: List[Dict[str, Any]] = []
        for row in rows:
            vector = np.asarray(json.loads(str(row["embedding"])), dtype=np.float32)
            score = float(np.dot(query_arr, vector))
            item = dict(row)
            item["dense_score"] = score
            scored.append(item)
        scored.sort(key=lambda item: float(item.get("dense_score", 0.0)), reverse=True)
        return scored[:limit]

    def read(self, chunk_ids: Sequence[str], window: int) -> List[Dict[str, Any]]:
        cleaned_ids = [str(item).strip() for item in chunk_ids if str(item).strip()]
        if not cleaned_ids:
            return []
        outputs: List[Dict[str, Any]] = []
        with self._connect() as conn:
            for chunk_id in cleaned_ids:
                row = conn.execute(
                    """
                    SELECT chunk_id, doc_id, chunk_index, section_type, chunk_language, text
                    FROM chunks WHERE chunk_id = ?
                    """,
                    (chunk_id,),
                ).fetchone()
                if not row:
                    continue
                left = max(0, int(row["chunk_index"]) - window)
                right = int(row["chunk_index"]) + window
                around_rows = conn.execute(
                    """
                    SELECT chunk_id, doc_id, chunk_index, section_type, chunk_language, text
                    FROM chunks WHERE doc_id = ? AND chunk_index >= ? AND chunk_index <= ?
                    ORDER BY chunk_index ASC
                    """,
                    (row["doc_id"], left, right),
                ).fetchall()
                outputs.append(
                    {
                        "chunk_id": chunk_id,
                        "doc_id": str(row["doc_id"]),
                        "section_type": str(row["section_type"]),
                        "chunk_language": str(row["chunk_language"]),
                        "location": f"chunk:{left}-{right}",
                        "text": "\n".join(self._normalize_text(item["text"]) for item in around_rows if item["text"]),
                    }
                )
        return outputs

    def _ensure_db(self) -> None:
        with self._connect() as conn:
            self._create_schema_if_missing(conn)
            conn.commit()

    def _recreate_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute("DROP TABLE IF EXISTS retrieval_meta")
        conn.execute("DROP TABLE IF EXISTS chunk_embeddings")
        conn.execute("DROP TABLE IF EXISTS chunks")
        conn.execute("DROP TABLE IF EXISTS documents")
        try:
            conn.execute("DROP TABLE IF EXISTS chunks_fts")
        except sqlite3.OperationalError:
            pass

        conn.execute(
            """
            CREATE TABLE documents(
                doc_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_type TEXT NOT NULL,
                doc_language TEXT NOT NULL,
                language_distribution_json TEXT NOT NULL,
                content_sha1 TEXT NOT NULL,
                parser_version TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE chunks(
                chunk_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                page_start INTEGER NOT NULL,
                page_end INTEGER NOT NULL,
                section_type TEXT NOT NULL,
                chunk_language TEXT NOT NULL,
                token_length INTEGER NOT NULL,
                text TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX idx_chunks_doc ON chunks(doc_id)")
        conn.execute("CREATE INDEX idx_chunks_doc_chunk ON chunks(doc_id, chunk_index)")
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE chunks_fts USING fts5(
                    chunk_id UNINDEXED,
                    text,
                    tokenize='unicode61'
                )
                """
            )
            self._fts_enabled = True
        except sqlite3.OperationalError as ex:
            self._fts_enabled = False
            raise RuntimeError(f"SQLite FTS5 is required for local retrieval: {ex}") from ex

        conn.execute(
            """
            CREATE TABLE chunk_embeddings(
                chunk_id TEXT PRIMARY KEY,
                model_name TEXT NOT NULL,
                embedding_dim INTEGER NOT NULL,
                embedding TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX idx_chunk_embeddings_model ON chunk_embeddings(model_name)")
        conn.execute(
            """
            CREATE TABLE retrieval_meta(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._enable_sqlite_vec(conn)

    def _create_schema_if_missing(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents(
                doc_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_type TEXT NOT NULL,
                doc_language TEXT NOT NULL,
                language_distribution_json TEXT NOT NULL,
                content_sha1 TEXT NOT NULL,
                parser_version TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks(
                chunk_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                page_start INTEGER NOT NULL,
                page_end INTEGER NOT NULL,
                section_type TEXT NOT NULL,
                chunk_language TEXT NOT NULL,
                token_length INTEGER NOT NULL,
                text TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc_chunk ON chunks(doc_id, chunk_index)")
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                    chunk_id UNINDEXED,
                    text,
                    tokenize='unicode61'
                )
                """
            )
            self._fts_enabled = True
        except sqlite3.OperationalError as ex:
            self._fts_enabled = False
            raise RuntimeError(f"SQLite FTS5 is required for local retrieval: {ex}") from ex

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chunk_embeddings(
                chunk_id TEXT PRIMARY KEY,
                model_name TEXT NOT NULL,
                embedding_dim INTEGER NOT NULL,
                embedding TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_model ON chunk_embeddings(model_name)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS retrieval_meta(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._enable_sqlite_vec(conn)

    def _enable_sqlite_vec(self, conn: sqlite3.Connection) -> None:
        self._vec_enabled = False
        extension_path = settings.LOCAL_RETRIEVAL_SQLITE_VEC_EXTENSION_PATH
        if not extension_path:
            return
        try:
            conn.enable_load_extension(True)
            conn.load_extension(extension_path)
            self._vec_enabled = True
        except Exception as ex:  # pragma: no cover - 取决于宿主环境
            logger.warning(f"sqlite-vec 扩展加载失败，将继续使用 SQLite 存储向量并做精确计算: {ex}")
        finally:
            try:
                conn.enable_load_extension(False)
            except Exception:
                pass

    def _get_meta_value(self, key: str) -> str:
        if not self.db_path.exists():
            return ""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM retrieval_meta WHERE key = ?",
                (key,),
            ).fetchone()
            return str(row["value"]) if row else ""

    def _build_model_signature(self) -> str:
        payload = {
            "model_name": self.embedding_config.model_name,
            "base_url": str(settings.LOCAL_RETRIEVAL_EMBEDDING_BASE_URL or settings.LLM_BASE_URL or "").strip(),
            "normalize": True,
        }
        return hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _sanitize_match_term(self, term: str) -> str:
        return str(term or "").replace('"', " ").strip()

    def _build_chunk_id(self, doc_id: str, chunk_index: int, chunk_text: str) -> str:
        digest = hashlib.sha1(f"{doc_id}:{chunk_index}:{chunk_text[:200]}".encode("utf-8")).hexdigest()[:10]
        return f"{doc_id}_{chunk_index}_{digest}"

    def _detect_section_type(self, text: str) -> str:
        value = text or ""
        if re.search(r"(具体实施方式|实施例|优选实施例|实施方式|embodiment)", value, re.I):
            return "embodiment"
        if re.search(r"(权利要求|claim\s*\d+|請求項|청구항)", value, re.I):
            return "claim"
        if re.search(r"(摘要|abstract)", value, re.I):
            return "abstract"
        return "other"

    def _normalize_text(self, value: Any) -> str:
        text = str(value or "")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


class HybridRanker:
    def merge(
        self,
        lexical_hits: Sequence[Dict[str, Any]],
        dense_hits: Sequence[Dict[str, Any]],
        intent: str,
        top_k: int,
    ) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        lexical_rank = {str(item.get("chunk_id", "")): idx + 1 for idx, item in enumerate(lexical_hits)}
        dense_rank = {str(item.get("chunk_id", "")): idx + 1 for idx, item in enumerate(dense_hits)}

        for item in lexical_hits:
            chunk_id = str(item.get("chunk_id", "")).strip()
            if not chunk_id:
                continue
            merged.setdefault(chunk_id, {}).update(dict(item))
        for item in dense_hits:
            chunk_id = str(item.get("chunk_id", "")).strip()
            if not chunk_id:
                continue
            current = merged.setdefault(chunk_id, {})
            current.update({key: value for key, value in dict(item).items() if key not in {"text"} or not current.get("text")})

        ranked: List[Dict[str, Any]] = []
        for chunk_id, item in merged.items():
            lexical_pos = lexical_rank.get(chunk_id)
            dense_pos = dense_rank.get(chunk_id)
            lexical_score = self._lexical_score(item)
            dense_score = float(item.get("dense_score", 0.0) or 0.0)
            channels: List[str] = []
            rrf_score = 0.0
            if lexical_pos:
                channels.append("lexical")
                rrf_score += 0.6 / (60.0 + lexical_pos)
            if dense_pos:
                channels.append("dense")
                rrf_score += 0.7 / (60.0 + dense_pos)

            section_boost = self._section_boost(intent, str(item.get("section_type", "")))
            dual_hit_boost = 0.08 if len(channels) >= 2 else 0.0
            diversity_boost = 0.03 if str(item.get("chunk_language", "")) == "mixed" else 0.0
            fusion_score = round(rrf_score + lexical_score * 0.3 + dense_score * 0.3 + section_boost + dual_hit_boost + diversity_boost, 6)
            text = str(item.get("text", "")).strip()
            ranked.append(
                {
                    "candidate_id": chunk_id,
                    "chunk_id": chunk_id,
                    "doc_id": str(item.get("doc_id", "")).strip(),
                    "source_type": "comparison_document",
                    "section_type": str(item.get("section_type", "")).strip() or "other",
                    "chunk_index": int(item.get("chunk_index", 0) or 0),
                    "location": f"chunk:{int(item.get('chunk_index', 0) or 0)}",
                    "text": text,
                    "chunk_language": str(item.get("chunk_language", "")).strip() or "other",
                    "retrieval_channels": channels,
                    "lexical_score": round(lexical_score, 6),
                    "dense_score": round(dense_score, 6),
                    "fusion_score": fusion_score,
                    "score": fusion_score,
                    "match_terms": item.get("match_terms", []),
                }
            )
        ranked.sort(key=lambda row: float(row.get("fusion_score", 0.0)), reverse=True)
        return ranked[:top_k]

    def _lexical_score(self, item: Dict[str, Any]) -> float:
        raw = item.get("bm25_score")
        if raw is None:
            return 0.0
        try:
            return 1.0 / (1.0 + max(0.0, float(raw)))
        except Exception:
            return 0.0

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


class LocalEvidenceRetriever:
    """Reusable task-level hybrid retriever."""

    def __init__(
        self,
        db_path: str,
        chunk_chars: int = 600,
        chunk_overlap: int = 120,
    ):
        self.db_path = Path(db_path)
        self.chunk_builder = ChunkBuilder(chunk_chars=chunk_chars, chunk_overlap=chunk_overlap)
        self.language_router = LanguageRouter()
        self.embedding_config = EmbeddingConfig(
            model_name=settings.LOCAL_RETRIEVAL_EMBEDDING_MODEL,
        )
        self.embedding_provider = self._build_embedding_provider()
        self.index = SQLiteHybridIndex(db_path=self.db_path, embedding_config=self.embedding_config)
        self.ranker = HybridRanker()

    def build_index(self, documents: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        return self.index.build(
            documents=documents,
            chunk_builder=self.chunk_builder,
            language_router=self.language_router,
            embedding_provider=self.embedding_provider,
        )

    def search(
        self,
        query: str,
        intent: str,
        doc_filters: Optional[Sequence[str]] = None,
        top_k: int = 20,
    ) -> List[Dict[str, Any]]:
        normalized_query = self._normalize_text(query)
        if not normalized_query:
            return []

        query_bundle = self._build_query_bundle(normalized_query)
        filters = [str(item).strip() for item in (doc_filters or []) if str(item).strip()]

        lexical_terms = self._extract_terms(query_bundle["lexical"])
        lexical_hits = self.index.search_lexical(
            terms=lexical_terms,
            filters=filters,
            limit=max(settings.LOCAL_RETRIEVAL_CANDIDATE_K, top_k),
        )
        for row in lexical_hits:
            text = self._normalize_text(row.get("text", ""))
            row["text"] = text
            row["match_terms"] = [term for term in lexical_terms if term and term.lower() in text.lower()][:8]

        dense_hits: List[Dict[str, Any]] = []
        dense_queries = query_bundle["semantic"][:4]
        if dense_queries:
            embeddings = self.embedding_provider.encode_queries(dense_queries)
            per_query_hits: Dict[str, Dict[str, Any]] = {}
            for vector in embeddings:
                for row in self.index.search_dense(
                    query_vector=vector,
                    filters=filters,
                    limit=max(settings.LOCAL_RETRIEVAL_CANDIDATE_K, top_k),
                ):
                    chunk_id = str(row.get("chunk_id", "")).strip()
                    if not chunk_id:
                        continue
                    current = per_query_hits.get(chunk_id)
                    if not current or float(row.get("dense_score", 0.0)) > float(current.get("dense_score", 0.0)):
                        per_query_hits[chunk_id] = row
            dense_hits = sorted(
                per_query_hits.values(),
                key=lambda item: float(item.get("dense_score", 0.0)),
                reverse=True,
            )[: max(settings.LOCAL_RETRIEVAL_CANDIDATE_K, top_k)]

        return self.ranker.merge(
            lexical_hits=lexical_hits,
            dense_hits=dense_hits,
            intent=intent,
            top_k=max(top_k, 1),
        )

    def read(self, chunk_ids: Sequence[str], window: int = 1) -> List[Dict[str, Any]]:
        return self.index.read(chunk_ids=chunk_ids, window=max(0, int(window)))

    def build_evidence_cards(
        self,
        candidates: Sequence[Dict[str, Any]],
        context_k: int,
        max_context_chars: int,
        max_quote_chars: int,
        read_window: int = 1,
    ) -> Dict[str, Any]:
        context_k = max(1, int(context_k))
        max_context_chars = max(100, int(max_context_chars))
        max_quote_chars = max(20, int(max_quote_chars))
        read_window = max(0, int(read_window))

        merged = [self._to_candidate(item) for item in (candidates or [])]
        merged = [item for item in merged if item]
        if not merged:
            return {"cards": [], "trace": self._empty_card_trace()}

        local_chunk_ids = [str(item.get("chunk_id", "")).strip() for item in merged if str(item.get("chunk_id", "")).strip()]
        read_map = {
            str(item.get("chunk_id", "")).strip(): item
            for item in self.read(local_chunk_ids, window=read_window)
            if str(item.get("chunk_id", "")).strip()
        }

        ranked = sorted(merged, key=lambda x: float(x.get("fusion_score", x.get("score", 0.0))), reverse=True)
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
            cards.append(
                {
                    "candidate_id": candidate_id,
                    "chunk_id": chunk_id or None,
                    "doc_id": str(item.get("doc_id", "")).strip(),
                    "quote": quote,
                    "location": str(item.get("location", "")).strip() or str(read_item.get("location", "")).strip(),
                    "analysis": str(item.get("analysis", "")).strip() or self._default_analysis(item),
                    "source_url": str(item.get("source_url", "")).strip() or None,
                    "source_title": str(item.get("source_title", "")).strip() or None,
                    "source_type": str(item.get("source_type", "")).strip() or "comparison_document",
                    "score": float(item.get("fusion_score", item.get("score", 0.0))),
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

    def _build_query_bundle(self, query: str) -> Dict[str, List[str]]:
        lexical = [query]
        semantic = [query]
        query_language = self.language_router.detect(query)
        if query_language in {"zh", "mixed"}:
            ascii_terms = [term for term in self._extract_terms(query) if re.search(r"[A-Za-z]", term)]
            if ascii_terms:
                lexical.append(" ".join(ascii_terms[:8]))
                semantic.append(" ".join(ascii_terms[:8]))
        else:
            collapsed = re.sub(r"\s+", " ", query)
            lexical.append(collapsed.lower())
        deduped_lexical = self._dedupe_strings(lexical)
        deduped_semantic = self._dedupe_strings(semantic)
        return {"lexical": deduped_lexical, "semantic": deduped_semantic}

    def _extract_terms(self, queries: Sequence[str]) -> List[str]:
        stop_words = {
            "一种", "方法", "系统", "装置", "包括", "用于", "实现", "所述", "技术", "特征",
            "the", "and", "for", "with", "into", "from", "that", "this", "these", "those",
        }
        terms: List[str] = []
        for query in queries:
            raw_terms = re.split(r"[\s,，。；;:：、（）()\[\]{}|/]+", str(query))
            for token in raw_terms:
                value = token.strip()
                if len(value) < 2 or value.lower() in stop_words:
                    continue
                if value not in terms:
                    terms.append(value)
        return terms[:32]

    def _default_analysis(self, item: Dict[str, Any]) -> str:
        section_type = str(item.get("section_type", "")).strip() or "other"
        channels = "/".join(item.get("retrieval_channels", []) or []) or "hybrid"
        terms = [str(term).strip() for term in (item.get("match_terms") or []) if str(term).strip()]
        term_hint = f"关键词命中：{', '.join(terms[:3])}" if terms else "语义相近"
        return f"来自 {section_type} 片段，经 {channels} 召回，{term_hint}。"

    def _to_candidate(self, item: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(item, dict):
            return {}
        candidate = {
            "candidate_id": str(item.get("candidate_id", "")).strip() or str(item.get("chunk_id", "")).strip(),
            "chunk_id": str(item.get("chunk_id", "")).strip(),
            "doc_id": str(item.get("doc_id", "")).strip(),
            "source_type": str(item.get("source_type", "")).strip() or "comparison_document",
            "section_type": str(item.get("section_type", "")).strip() or "other",
            "location": str(item.get("location", "")).strip(),
            "text": self._normalize_text(item.get("text", "")),
            "analysis": str(item.get("analysis", "")).strip(),
            "source_url": str(item.get("source_url", "")).strip(),
            "source_title": str(item.get("source_title", "")).strip(),
            "score": self._safe_float(item.get("score"), 0.0),
            "fusion_score": self._safe_float(item.get("fusion_score", item.get("score")), 0.0),
            "retrieval_channels": item.get("retrieval_channels", []),
            "match_terms": item.get("match_terms", []),
        }
        if not candidate["doc_id"]:
            return {}
        return candidate

    def _build_embedding_provider(self) -> EmbeddingProvider:
        if not self.embedding_config.model_name:
            raise ValueError("LOCAL_RETRIEVAL_EMBEDDING_MODEL is required")
        api_key = settings.LOCAL_RETRIEVAL_EMBEDDING_API_KEY or settings.LLM_API_KEY
        base_url = settings.LOCAL_RETRIEVAL_EMBEDDING_BASE_URL or settings.LLM_BASE_URL
        if not api_key or not base_url:
            raise ValueError(
                "LOCAL_RETRIEVAL_EMBEDDING_API_KEY/BASE_URL or LLM_API_KEY/BASE_URL are required for online embeddings"
            )
        return EmbeddingProvider(self.embedding_config)

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

    def _dedupe_strings(self, values: Iterable[str]) -> List[str]:
        outputs: List[str] = []
        for item in values:
            value = self._normalize_text(item)
            if value and value not in outputs:
                outputs.append(value)
        return outputs

    def _empty_card_trace(self) -> Dict[str, Any]:
        return {
            "selected_candidates": [],
            "dropped_candidates": [],
            "context_chars": 0,
            "context_k": 0,
            "max_context_chars": 0,
            "max_quote_chars": 0,
        }
