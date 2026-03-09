import json
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from loguru import logger
from agents.common.utils.serialization import to_jsonable

class StepCache:
    """
    通用缓存管理器。
    支持自动加载/保存、线程安全写入和惰性计算。
    """
    def __init__(self, file_path: Path):
        self.file_path = file_path
        self._lock = threading.Lock() # 文件写入锁
        self.data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.file_path and self.file_path.exists():
            try:
                content = self.file_path.read_text(encoding='utf-8')
                logger.info(f"已加载缓存文件: {self.file_path}")
                return json.loads(content)
            except Exception as e:
                logger.warning(f"缓存文件加载失败，将重置: {e}")
        return {}

    def save(self, key: str, value: Any):
        """线程安全地保存单个键值对"""
        with self._lock:
            self.data[key] = value
            try:
                self.file_path.parent.mkdir(parents=True, exist_ok=True)
                self.file_path.write_text(
                    json.dumps(self.data, ensure_ascii=False, indent=2, default=self._json_default),
                    encoding='utf-8'
                )
                logger.debug(f"缓存已更新: [{key}]")
            except Exception as e:
                logger.error(f"写入缓存文件失败: {e}")

    def get(self, key: str) -> Optional[Any]:
        return self.data.get(key)

    def run_step(self, key: str, func: Callable, *args, **kwargs) -> Any:
        """
        如果 key 存在则直接返回；否则执行函数并缓存结果。
        """
        if key in self.data:
            logger.info(f"缓存命中: [{key}]")
            return self.data[key]

        logger.info(f"开始执行: [{key}]")
        result = func(*args, **kwargs)

        if result is not None:
            self.save(key, result)

        return result

    def _json_default(self, obj: Any):
        """将常见不可序列化对象转换为 JSON 兼容结构。"""
        return to_jsonable(obj)
