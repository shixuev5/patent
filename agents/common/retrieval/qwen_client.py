from __future__ import annotations

import base64
import hashlib
import math
import os
import random
from typing import Any, Dict, List, Optional

from loguru import logger


class DashScopeRetrievalClient:
    """Embedding and rerank client for Qwen retrieval models (SDK only)."""

    def __init__(
        self,
        api_key: Optional[str],
        embedding_model: str,
        text_rerank_model: str,
        vl_rerank_model: str,
        allow_fake_model: bool = True,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.embedding_model = embedding_model
        self.text_rerank_model = text_rerank_model
        self.vl_rerank_model = vl_rerank_model
        self.allow_fake_model = allow_fake_model

        self._dashscope = None
        try:
            import dashscope  # type: ignore

            self._dashscope = dashscope
        except Exception:
            self._dashscope = None

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        clean = [str(text or "").strip() for text in texts]
        if not any(clean):
            return [[0.0] * 384 for _ in clean]
        if not self.api_key and self.allow_fake_model:
            return [self._hash_embedding(text) for text in clean]

        try:
            vectors = self._embed_texts_by_sdk(clean)
            if vectors and len(vectors) == len(clean):
                return vectors
        except Exception as ex:
            logger.warning(f"Qwen embedding API failed, fallback policy triggered: {ex}")

        if self.allow_fake_model:
            return [self._hash_embedding(text) for text in clean]

        raise RuntimeError("Qwen embedding failed and fake model is disabled")

    def embed_images(self, image_paths: List[str]) -> List[List[float]]:
        clean = [str(path or "").strip() for path in image_paths]
        if not any(clean):
            return [[0.0] * 384 for _ in clean]
        if not self.api_key and self.allow_fake_model:
            return [self._hash_embedding(path) for path in clean]

        try:
            vectors = self._embed_images_by_sdk(clean)
            if vectors and len(vectors) == len(clean):
                return vectors
        except Exception as ex:
            logger.warning(f"Qwen image embedding API failed, fallback policy triggered: {ex}")

        if self.allow_fake_model:
            return [self._hash_embedding(path) for path in clean]

        raise RuntimeError("Qwen image embedding failed and fake model is disabled")

    def rerank_text(self, query: str, candidates: List[str], top_k: int) -> List[Dict[str, Any]]:
        if not candidates:
            return []
        top_k = max(1, min(top_k, len(candidates)))
        if not self.api_key and self.allow_fake_model:
            return self._fallback_rerank(query, candidates, top_k)

        try:
            ranked = self._rerank_text_by_sdk(query, candidates, top_k)
            if ranked:
                return ranked
        except Exception as ex:
            logger.warning(f"Qwen text rerank API failed, fallback policy triggered: {ex}")

        if self.allow_fake_model:
            return self._fallback_rerank(query, candidates, top_k)

        raise RuntimeError("Qwen text rerank failed and fake model is disabled")

    def rerank_vl(
        self,
        query_text: str,
        query_image: str,
        candidates: List[Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        if not candidates:
            return []
        top_k = max(1, min(top_k, len(candidates)))
        if not self.api_key and self.allow_fake_model:
            candidate_texts = [str(item.get("text", "")) for item in candidates]
            return self._fallback_rerank(query_text, candidate_texts, top_k)

        try:
            ranked = self._rerank_vl_by_sdk(query_text, query_image, candidates, top_k)
            if ranked:
                return ranked
        except Exception as ex:
            logger.warning(f"Qwen vl rerank API failed, fallback policy triggered: {ex}")

        if self.allow_fake_model:
            candidate_texts = [str(item.get("text", "")) for item in candidates]
            return self._fallback_rerank(query_text, candidate_texts, top_k)

        raise RuntimeError("Qwen VL rerank failed and fake model is disabled")

    def _embed_texts_by_sdk(self, texts: List[str]) -> List[List[float]]:
        if not self._dashscope:
            raise RuntimeError("dashscope SDK is required for Qwen retrieval")
        if not hasattr(self._dashscope, "MultiModalEmbedding"):
            raise RuntimeError("dashscope SDK missing MultiModalEmbedding")

        payload = [{"text": text} for text in texts]
        response = self._dashscope.MultiModalEmbedding.call(  # type: ignore[attr-defined]
            model=self.embedding_model,
            input=payload,
            api_key=self.api_key,
        )
        return self._extract_embeddings(response)

    def _embed_images_by_sdk(self, image_paths: List[str]) -> List[List[float]]:
        if not self._dashscope:
            raise RuntimeError("dashscope SDK is required for Qwen retrieval")
        if not hasattr(self._dashscope, "MultiModalEmbedding"):
            raise RuntimeError("dashscope SDK missing MultiModalEmbedding")

        payload = [{"image": self._image_to_data_uri(path)} for path in image_paths]
        response = self._dashscope.MultiModalEmbedding.call(  # type: ignore[attr-defined]
            model=self.embedding_model,
            input=payload,
            api_key=self.api_key,
        )
        return self._extract_embeddings(response)

    def _rerank_text_by_sdk(self, query: str, candidates: List[str], top_k: int) -> List[Dict[str, Any]]:
        if not self._dashscope:
            raise RuntimeError("dashscope SDK is required for Qwen retrieval")
        if not hasattr(self._dashscope, "TextReRank"):
            raise RuntimeError("dashscope SDK missing TextReRank")

        response = self._dashscope.TextReRank.call(  # type: ignore[attr-defined]
            model=self.text_rerank_model,
            query=query,
            documents=candidates,
            top_n=top_k,
            api_key=self.api_key,
        )
        return self._extract_rerank(response)

    def _rerank_vl_by_sdk(
        self,
        query_text: str,
        query_image: str,
        candidates: List[Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        if not self._dashscope:
            raise RuntimeError("dashscope SDK is required for Qwen retrieval")
        if not hasattr(self._dashscope, "MultiModalRerank"):
            raise RuntimeError("dashscope SDK missing MultiModalRerank")

        query: Dict[str, Any] = {}
        if query_text:
            query["text"] = query_text
        if query_image:
            query["image"] = self._image_to_data_uri(query_image)

        documents: List[Dict[str, Any]] = []
        for item in candidates:
            entry: Dict[str, Any] = {}
            if item.get("text"):
                entry["text"] = str(item.get("text"))
            if item.get("image"):
                entry["image"] = self._image_to_data_uri(str(item.get("image")))
            documents.append(entry)

        response = self._dashscope.MultiModalRerank.call(  # type: ignore[attr-defined]
            model=self.vl_rerank_model,
            query=query,
            documents=documents,
            top_n=top_k,
            api_key=self.api_key,
        )
        return self._extract_rerank(response)

    def _extract_embeddings(self, payload: Any) -> List[List[float]]:
        data = self._to_dict(payload)

        candidates = []
        output = data.get("output")
        if isinstance(output, dict):
            candidates.extend(output.get("embeddings", []) or [])
            candidates.extend(output.get("data", []) or [])
        candidates.extend(data.get("embeddings", []) or [])
        candidates.extend(data.get("data", []) or [])

        vectors: List[List[float]] = []
        for item in candidates:
            if isinstance(item, dict):
                vector = item.get("embedding") or item.get("vector")
            else:
                vector = item
            if isinstance(vector, list) and vector:
                vectors.append([float(v) for v in vector])

        if not vectors:
            raise RuntimeError("Embedding response contains no vectors")
        return vectors

    def _extract_rerank(self, payload: Any) -> List[Dict[str, Any]]:
        data = self._to_dict(payload)

        candidates = []
        output = data.get("output")
        if isinstance(output, dict):
            candidates.extend(output.get("results", []) or [])
            candidates.extend(output.get("list", []) or [])
        candidates.extend(data.get("results", []) or [])

        ranked: List[Dict[str, Any]] = []
        for index, item in enumerate(candidates):
            if not isinstance(item, dict):
                continue
            raw_idx = item.get("index")
            if raw_idx is None:
                raw_idx = item.get("id", index)
            try:
                candidate_index = int(raw_idx)
            except Exception:
                candidate_index = index

            score = item.get("relevance_score")
            if score is None:
                score = item.get("score", 0.0)

            ranked.append(
                {
                    "index": candidate_index,
                    "score": float(score),
                }
            )

        if not ranked:
            raise RuntimeError("Rerank response contains no results")
        ranked.sort(key=lambda x: x["score"], reverse=True)
        return ranked

    def _fallback_rerank(self, query: str, candidates: List[str], top_k: int) -> List[Dict[str, Any]]:
        query_terms = {token for token in _tokenize(query) if token}
        scores = []
        for idx, text in enumerate(candidates):
            terms = {token for token in _tokenize(text) if token}
            overlap = len(query_terms & terms)
            denom = max(1, len(query_terms) + len(terms))
            score = (2.0 * overlap) / denom
            scores.append({"index": idx, "score": float(score)})

        scores.sort(key=lambda item: item["score"], reverse=True)
        return scores[:top_k]

    def _hash_embedding(self, text: str, dim: int = 384) -> List[float]:
        seed = int(hashlib.md5(text.encode("utf-8")).hexdigest()[:8], 16)
        rng = random.Random(seed)
        vector = [rng.uniform(-1, 1) for _ in range(dim)]
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    @staticmethod
    def _to_dict(payload: Any) -> Dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        if hasattr(payload, "output") and hasattr(payload, "request_id"):
            result = {
                "output": getattr(payload, "output", {}),
                "request_id": getattr(payload, "request_id", ""),
            }
            return result
        if hasattr(payload, "model_dump"):
            try:
                return payload.model_dump()
            except Exception:
                pass
        return {}

    @staticmethod
    def _image_to_data_uri(path: str) -> str:
        if path.startswith("data:image/"):
            return path

        with open(path, "rb") as f:
            image_base64 = base64.b64encode(f.read()).decode("utf-8")

        ext = os.path.splitext(path)[1].lower()
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
            ".bmp": "image/bmp",
        }.get(ext, "image/jpeg")
        return f"data:{mime};base64,{image_base64}"


def _tokenize(text: str) -> List[str]:
    lowered = str(text or "").lower()
    tokens = []
    current = []
    for ch in lowered:
        if ch.isalnum() or ch in {"_", "-"}:
            current.append(ch)
            continue
        if current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tokens
