from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RetrievalChunk:
    """Normalized chunk unit for vector indexing."""

    chunk_id: str
    text: str
    source_type: str
    modality: str = "text"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalHit:
    """Unified retrieval output."""

    score: float
    excerpt: str
    doc_id: str
    source_type: str
    modality: str
    chunk_id: str
    heading_path: str = ""
    para_id: str = ""
    page: Optional[int] = None
    url: str = ""
    title: str = ""
    published_date: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalRequest:
    """Input parameters for retrieval service."""

    query_text: str = ""
    query_image: str = ""
    inputs: List[Dict[str, Any]] = field(default_factory=list)
    mode: str = "ephemeral"
    session_id: str = ""
    top_n: int = 20
    top_k: int = 5
    filters: Dict[str, Any] = field(default_factory=dict)


SOURCE_MARKDOWN = {"patent", "non_patent"}
SOURCE_WEB = {"web", "tavily", "tavily_web"}
SOURCE_SHORT_TEXT = {"zhihuiya", "zhihuiya_patent", "openalex"}
