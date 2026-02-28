import json
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from loguru import logger

class StepCache:
    """
    通用步骤缓存管理器。
    支持：
    1. 自动加载/保存 JSON 文件。
    2. 线程安全的写入 (适配并行任务)。
    3. 'Load or Compute' 模式，简化调用代码。
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
                # 写入原子性通过锁保证，但文件系统层面建议 write+rename，这里简化为直接覆盖
                self.file_path.write_text(
                    json.dumps(self.data, ensure_ascii=False, indent=2), 
                    encoding='utf-8'
                )
                logger.debug(f"缓存已更新: [{key}]")
            except Exception as e:
                logger.error(f"写入缓存文件失败: {e}")

    def get(self, key: str) -> Optional[Any]:
        return self.data.get(key)

    def run_step(self, key: str, func: Callable, *args, **kwargs) -> Any:
        """
        核心方法：
        如果 key 存在，直接返回缓存值；
        否则，执行 func(*args, **kwargs)，保存并返回结果。
        """
        if key in self.data:
            logger.info(f"Step [{key}]: 跳过 (命中缓存)")
            return self.data[key]
        
        # 执行计算
        logger.info(f"Step [{key}]: 开始执行...")
        result = func(*args, **kwargs)
        
        # 保存结果
        if result is not None:
            self.save(key, result)
        
        return result