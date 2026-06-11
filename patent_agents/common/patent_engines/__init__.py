from importlib import import_module
from typing import Any

__all__ = ["KnowledgeExtractor", "VisualProcessor"]


def __getattr__(name: str) -> Any:
    if name == "KnowledgeExtractor":
        return import_module(".knowledge", __name__).KnowledgeExtractor
    if name == "VisualProcessor":
        return import_module(".vision", __name__).VisualProcessor
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
