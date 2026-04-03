import threading
from typing import Dict
from agents.common.search_clients.base import BaseSearchClient
from agents.common.search_clients.zhihuiya import ZhihuiyaClient

class SearchClientFactory:
    _instances: Dict[str, BaseSearchClient] = {}
    _lock = threading.Lock()  # 新增：类级别的锁

    @staticmethod
    def get_client(provider: str = "zhihuiya") -> BaseSearchClient:
        provider = provider.lower().strip()

        if provider == "zhihuiya" or provider == "patsnap":
            return ZhihuiyaClient()

        # 双重检查锁定 (Double-Checked Locking) 确保线程安全且高效
        if provider not in SearchClientFactory._instances:
            with SearchClientFactory._lock:
                if provider not in SearchClientFactory._instances:
                    raise ValueError(f"Unknown search provider: {provider}")

        return SearchClientFactory._instances[provider]
