from src.search_clients.base import BaseSearchClient
from src.search_clients.zhihuiya import ZhihuiyaClient
from src.search_clients.google_patents import GooglePatentsClient # 新增导入

class SearchClientFactory:
    _instances = {}

    @staticmethod
    def get_client(provider: str = "zhihuiya") -> BaseSearchClient:
        # 标准化 provider 字符串
        provider = provider.lower().strip()
        
        if provider not in SearchClientFactory._instances:
            if provider == "zhihuiya" or provider == "patsnap":
                SearchClientFactory._instances[provider] = ZhihuiyaClient()
            elif provider == "google" or provider == "google_patents":
                # 新增 Google 客户端实例化
                SearchClientFactory._instances[provider] = GooglePatentsClient()
            else:
                # 默认或错误处理，可回退到 Mock 或报错
                raise ValueError(f"Unknown search provider: {provider}")
        
        return SearchClientFactory._instances[provider]