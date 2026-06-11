"""通用序列化工具。"""

from pathlib import Path
from typing import Any, Dict

from pydantic import BaseModel, TypeAdapter

_ANY_ADAPTER = TypeAdapter(Any)


def to_jsonable(value: Any) -> Any:
    """
    将对象转换为可 JSON 序列化的基础类型。
    优先使用 Pydantic TypeAdapter，失败时递归降级处理。
    """
    try:
        return _ANY_ADAPTER.dump_python(value, mode="json")
    except Exception:
        return _to_jsonable_fallback(value)


def _to_jsonable_fallback(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [_to_jsonable_fallback(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _to_jsonable_fallback(v) for k, v in value.items()}
    if isinstance(value, BaseModel):
        return _to_jsonable_fallback(value.model_dump(mode="json"))
    if hasattr(value, "model_dump"):
        return _to_jsonable_fallback(value.model_dump())
    if hasattr(value, "dict"):
        return _to_jsonable_fallback(value.dict())
    return str(value)


def item_get(item: Any, key: str, default=None):
    """兼容 dict / 对象字段读取。"""
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def to_dict(item: Any) -> Dict[str, Any]:
    """尽可能将对象转换为 dict。"""
    if isinstance(item, dict):
        return item
    plain = to_jsonable(item)
    return plain if isinstance(plain, dict) else {}
