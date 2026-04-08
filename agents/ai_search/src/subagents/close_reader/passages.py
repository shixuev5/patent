"""精读场景的关键词提取与兜底段落选择。"""

from __future__ import annotations

import re
from typing import Dict, List

from agents.ai_search.src.stage_limits import DEFAULT_KEY_PASSAGES_LIMIT, DEFAULT_PASSAGE_PREVIEW_CHARS


def collect_key_terms(search_elements: Dict[str, Any]) -> List[str]:
    terms: List[str] = []
    for element in search_elements.get("search_elements") or []:
        if not isinstance(element, dict):
            continue
        for key in ("keywords_zh", "keywords_en"):
            for value in element.get(key) or []:
                text = str(value or "").strip()
                if text and text not in terms:
                    terms.append(text)
    return terms[:24]


def fallback_passages(text: str, terms: List[str]) -> List[Dict[str, Any]]:
    if not text:
        return []
    paragraphs = [item.strip() for item in re.split(r"\n{2,}", text) if item.strip()]
    scored: List[tuple[float, int, Dict[str, Any]]] = []
    for index, paragraph in enumerate(paragraphs):
        lowered = paragraph.lower()
        hit_terms = [term for term in terms if term and term.lower() in lowered]
        if not hit_terms:
            continue
        paragraph_type = "description"
        if re.search(r"权利要求|claim", paragraph[:80], re.IGNORECASE):
            paragraph_type = "claim"
        elif re.search(r"摘要|abstract", paragraph[:80], re.IGNORECASE):
            paragraph_type = "abstract"
        unique_hits = list(dict.fromkeys(hit_terms))
        density = round(len(unique_hits) / max(len(paragraph.split()), 1), 4)
        type_bonus = {"claim": 0.35, "abstract": 0.2, "description": 0.1}.get(paragraph_type, 0.0)
        score = len(unique_hits) + density + type_bonus
        scored.append(
            (
                score,
                index,
                {
                    "document_id": "",
                    "passage": paragraph[:DEFAULT_PASSAGE_PREVIEW_CHARS],
                    "reason": f"{paragraph_type} 命中 {len(unique_hits)} 个关键词",
                    "location": f"paragraph_{index + 1}",
                    "paragraph_type": paragraph_type,
                    "hit_terms": unique_hits[:8],
                    "hit_density": density,
                },
            )
        )
    scored.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    return [item[2] for item in scored[:DEFAULT_KEY_PASSAGES_LIMIT]]
