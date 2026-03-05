from __future__ import annotations

import re
from typing import Any, Dict, List

from .types import RetrievalChunk, SOURCE_MARKDOWN, SOURCE_SHORT_TEXT, SOURCE_WEB


def chunk_documents(items: List[Dict[str, Any]]) -> List[RetrievalChunk]:
    chunks: List[RetrievalChunk] = []
    for index, item in enumerate(items or []):
        source_type = _normalize_source_type(item)
        if source_type in SOURCE_MARKDOWN:
            chunks.extend(chunk_markdown_document(item, doc_index=index))
        elif source_type in SOURCE_WEB:
            chunks.extend(chunk_tavily_document(item, doc_index=index))
        elif source_type in SOURCE_SHORT_TEXT:
            chunks.extend(chunk_short_text_document(item, doc_index=index))
        else:
            chunks.extend(chunk_short_text_document(item, doc_index=index))
    return chunks


def chunk_markdown_document(item: Dict[str, Any], doc_index: int) -> List[RetrievalChunk]:
    markdown = str(item.get("markdown") or item.get("content") or "").strip()
    if not markdown:
        return []

    doc_id = str(item.get("doc_id") or f"DOC_{doc_index + 1}")
    source_type = _normalize_source_type(item)
    page = _safe_int(item.get("page"))

    chunks: List[RetrievalChunk] = []
    heading_levels: Dict[int, str] = {}
    para_buffer: List[str] = []
    para_index = 0

    def flush_paragraph() -> None:
        nonlocal para_buffer, para_index
        if not para_buffer:
            return
        paragraph = "\n".join(para_buffer).strip()
        para_buffer = []
        if not paragraph:
            return

        para_index += 1
        claim_id = _extract_claim_id(paragraph)
        heading_path = _heading_path(heading_levels)
        max_chars = 320
        overlap = 40
        parts = _sentence_windows(paragraph, max_chars=max_chars, overlap_chars=overlap)

        for part_index, part in enumerate(parts, start=1):
            chunk_id = f"{doc_id}::p{para_index}::c{part_index}"
            chunks.append(
                RetrievalChunk(
                    chunk_id=chunk_id,
                    text=part,
                    source_type=source_type,
                    modality="text",
                    metadata={
                        "doc_id": doc_id,
                        "page": page,
                        "para_id": f"p{para_index}",
                        "heading_path": heading_path,
                        "claim_id": claim_id,
                    },
                )
            )

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            flush_paragraph()
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading_match:
            flush_paragraph()
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()
            heading_levels[level] = heading_text
            for stale_level in list(heading_levels.keys()):
                if stale_level > level:
                    heading_levels.pop(stale_level, None)
            continue

        para_buffer.append(line)

    flush_paragraph()
    return chunks


def chunk_tavily_document(item: Dict[str, Any], doc_index: int) -> List[RetrievalChunk]:
    content = _clean_text(str(item.get("content") or item.get("snippet") or ""))
    if not content:
        return []

    doc_id = str(item.get("doc_id") or f"WEB_{doc_index + 1}")
    source_type = _normalize_source_type(item)
    title = str(item.get("title") or "").strip()
    url = str(item.get("url") or "").strip()
    published = str(item.get("published_date") or item.get("published") or "").strip()

    parts = _sentence_windows(content, max_chars=220, overlap_chars=30)
    chunks: List[RetrievalChunk] = []
    for part_index, part in enumerate(parts, start=1):
        chunk_id = f"{doc_id}::w{part_index}"
        chunks.append(
            RetrievalChunk(
                chunk_id=chunk_id,
                text=part,
                source_type=source_type,
                modality="text",
                metadata={
                    "doc_id": doc_id,
                    "url": url,
                    "title": title,
                    "published_date": published,
                    "chunk_index": part_index,
                },
            )
        )
    return chunks


def chunk_short_text_document(item: Dict[str, Any], doc_index: int) -> List[RetrievalChunk]:
    title = str(item.get("title") or "").strip()
    abstract = str(item.get("abstract") or item.get("snippet") or item.get("content") or "").strip()
    text = _clean_text("\n".join([x for x in [title, abstract] if x]))
    if not text:
        return []

    source_type = _normalize_source_type(item)
    doc_id = str(item.get("doc_id") or item.get("pn") or f"TXT_{doc_index + 1}")
    url = str(item.get("url") or item.get("doi") or "").strip()
    published = str(item.get("published_date") or item.get("published") or "").strip()

    text_len = len(text)
    if text_len <= 500:
        parts = [text]
    elif text_len <= 1200:
        parts = _sentence_windows(text, max_chars=max(250, text_len // 2 + 40), overlap_chars=40)
        if len(parts) > 2:
            parts = [parts[0], " ".join(parts[1:])]
    else:
        parts = _sentence_windows(text, max_chars=260, overlap_chars=40)

    chunks: List[RetrievalChunk] = []
    for part_index, part in enumerate(parts, start=1):
        chunk_id = f"{doc_id}::s{part_index}"
        chunks.append(
            RetrievalChunk(
                chunk_id=chunk_id,
                text=part,
                source_type=source_type,
                modality="text",
                metadata={
                    "doc_id": doc_id,
                    "url": url,
                    "title": title,
                    "published_date": published,
                    "chunk_index": part_index,
                },
            )
        )
    return chunks


def _normalize_source_type(item: Dict[str, Any]) -> str:
    value = str(item.get("source_type") or "").strip().lower()
    aliases = {
        "tavily_web": "web",
        "tavily": "web",
        "openalex": "openalex",
        "zhihuiya_patent": "zhihuiya",
    }
    return aliases.get(value, value or "unknown")


def _clean_text(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\.\.\.\.+", "...", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _sentence_windows(text: str, max_chars: int, overlap_chars: int) -> List[str]:
    text = _clean_text(text)
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    sentences = _split_sentences(text)
    if not sentences:
        return _fixed_windows(text, max_chars=max_chars, overlap_chars=overlap_chars)

    chunks: List[str] = []
    current = ""

    for sentence in sentences:
        if len(sentence) > max_chars:
            if current:
                chunks.append(current.strip())
                current = ""
            chunks.extend(_fixed_windows(sentence, max_chars=max_chars, overlap_chars=overlap_chars))
            continue

        if not current:
            current = sentence
            continue

        if len(current) + len(sentence) <= max_chars:
            current += sentence
            continue

        chunks.append(current.strip())
        if overlap_chars > 0:
            tail = current[-overlap_chars:]
            current = (tail + sentence).strip()
            if len(current) > max_chars:
                chunks.extend(_fixed_windows(current, max_chars=max_chars, overlap_chars=overlap_chars))
                current = ""
        else:
            current = sentence

    if current:
        chunks.append(current.strip())

    return [chunk for chunk in chunks if chunk]


def _split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[。！？；.!?;])\s+", text)
    if len(parts) <= 1:
        parts = re.split(r"(?<=[。！？；.!?;])", text)
    return [part.strip() for part in parts if part.strip()]


def _fixed_windows(text: str, max_chars: int, overlap_chars: int) -> List[str]:
    windows: List[str] = []
    start = 0
    step = max(1, max_chars - max(0, overlap_chars))
    while start < len(text):
        windows.append(text[start:start + max_chars].strip())
        start += step
    return [window for window in windows if window]


def _heading_path(levels: Dict[int, str]) -> str:
    if not levels:
        return ""
    ordered = [levels[level] for level in sorted(levels) if levels.get(level)]
    return " / ".join(ordered)


def _extract_claim_id(text: str) -> str:
    match = re.search(r"权利要求\s*(\d+)", text)
    if match:
        return match.group(1)
    return ""


def _safe_int(value: Any):
    try:
        return int(value)
    except Exception:
        return None
