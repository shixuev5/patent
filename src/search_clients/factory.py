import threading
from typing import Dict
from src.search_clients.base import BaseSearchClient
from src.search_clients.zhihuiya import ZhihuiyaClient

class SearchClientFactory:
    _instances: Dict[str, BaseSearchClient] = {}
    _lock = threading.Lock()  # 新增：类级别的锁

    @staticmethod
    def get_client(provider: str = "zhihuiya") -> BaseSearchClient:
        provider = provider.lower().strip()
        
        # 双重检查锁定 (Double-Checked Locking) 确保线程安全且高效
        if provider not in SearchClientFactory._instances:
            with SearchClientFactory._lock:
                if provider not in SearchClientFactory._instances:
                    if provider == "zhihuiya" or provider == "patsnap":
                        # 这里创建实例
                        SearchClientFactory._instances[provider] = ZhihuiyaClient()
                    else:
                        raise ValueError(f"Unknown search provider: {provider}")
        
        return SearchClientFactory._instances[provider]