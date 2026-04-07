from __future__ import annotations

import importlib
import sys
import types
from typing import Any


def _get_or_create_module(name: str) -> types.ModuleType:
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name)
        sys.modules[name] = module
    return module


def install_langchain_compat() -> None:
    """Provide legacy LangChain import paths that PaddleX still imports."""
    try:
        importlib.import_module("langchain")
        from langchain_core.documents import Document
    except Exception:
        return

    try:
        importlib.import_module("langchain.docstore.document")
    except ModuleNotFoundError:
        langchain = sys.modules["langchain"]
        docstore_module = _get_or_create_module("langchain.docstore")
        document_module = _get_or_create_module("langchain.docstore.document")
        document_module.Document = Document
        docstore_module.document = document_module
        setattr(langchain, "docstore", docstore_module)

    try:
        importlib.import_module("langchain.text_splitter")
    except ModuleNotFoundError:
        langchain = sys.modules["langchain"]
        text_splitter_module = _get_or_create_module("langchain.text_splitter")

        class RecursiveCharacterTextSplitter:
            """Minimal compatibility shim for PaddleX import-time usage."""

            def __init__(
                self,
                chunk_size: int = 1000,
                chunk_overlap: int = 0,
                separators: list[str] | None = None,
                **_: Any,
            ) -> None:
                self.chunk_size = max(1, int(chunk_size))
                self.chunk_overlap = max(0, int(chunk_overlap))
                self.separators = separators or ["\n\n", "\n", " ", ""]

            def split_text(self, text: str) -> list[str]:
                content = str(text or "")
                if not content:
                    return []

                step = max(1, self.chunk_size - self.chunk_overlap)
                return [
                    content[start : start + self.chunk_size]
                    for start in range(0, len(content), step)
                ]

        text_splitter_module.RecursiveCharacterTextSplitter = (
            RecursiveCharacterTextSplitter
        )
        setattr(langchain, "text_splitter", text_splitter_module)
