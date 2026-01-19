import requests
from typing import List, Dict
from loguru import logger
from config import settings
from src.search_clients.base import BaseSearchClient

class GooglePatentsClient(BaseSearchClient):
    """
    基于 SerpApi 的 Google Patents 检索客户端
    文档参考: https://serpapi.com/google-patents-api
    """
    def __init__(self):
        self.api_key = settings.SERPAPI_KEY
        self.base_url = "https://serpapi.com/search.json"
        if not self.api_key:
            logger.warning("[GooglePatents] SERPAPI_KEY is missing. Search calls will fail.")

    def _normalize_result(self, raw_item: Dict) -> Dict:
        """
        将 SerpApi 返回的 organic_results 标准化为通用格式
        """
        # 1. 基础字段映射
        publication_number = raw_item.get("publication_number", "")
        
        # 2. 人员信息处理 (Google 返回的是字符串，可能包含逗号)
        assignees = [raw_item.get("assignee")] if raw_item.get("assignee") else []
        inventors = [raw_item.get("inventor")] if raw_item.get("inventor") else []

        # 3. CPC/IPC 分类号
        # 注意: SerpApi 搜索列表页通常不返回详细的 classifications 列表。
        # 这里留空，或者如果 raw_item 中有 'classifications' 字段(某些特定查询)则解析
        cpcs = raw_item.get("classifications", []) 

        return {
            "id": publication_number,
            "pn": publication_number,
            "title": raw_item.get("title", ""),
            "abstract": raw_item.get("snippet", ""), # Google 使用 snippet 作为摘要/片段
            "cpc": cpcs,
            "assignees": assignees,
            "inventors": inventors,
            "publication_date": raw_item.get("publication_date", ""),
            "filing_date": raw_item.get("filing_date", ""),
            "score": 0, # Google 不返回数值型相关性分数
            "source_db": "GooglePatents",
            "link": raw_item.get("link", "") # 也就是 pdf 链接或 google 详情页
        }

    def search(self, query: str, db: str = "ALL", limit: int = 50) -> List[Dict]:
        """
        执行检索
        :param query: 检索关键词
        :param db: 映射为 status 参数 (GRANT, APPLICATION)
        :param limit: 数量 (SerpApi 单页最大 100)
        """
        if not self.api_key:
            logger.error("[GooglePatents] Cannot search: No API Key provided.")
            return []

        # 1. 构建参数
        # status: GRANT (授权), APPLICATION (申请)
        status_param = ""
        if db.upper() == "GRANT":
            status_param = "GRANT"
        elif db.upper() == "APPLICATION":
            status_param = "APPLICATION"

        params = {
            "engine": "google_patents",
            "q": query,
            "api_key": self.api_key,
            "num": min(limit, 100), 
            "status": status_param,
            "sort": "relevance"
        }

        # 2. 发起请求
        try:
            logger.info(f"[GooglePatents] Searching: {query[:50]}...")
            resp = requests.get(self.base_url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            # 3. 错误处理
            if "error" in data:
                logger.error(f"[GooglePatents] API Error: {data['error']}")
                return []

            # 4. 数据解析
            raw_results = data.get("organic_results", [])
            logger.success(f"[GooglePatents] Found {len(raw_results)} results.")
            
            return [self._normalize_result(item) for item in raw_results]

        except Exception as e:
            logger.error(f"[GooglePatents] Request failed: {e}")
            return []

    def get_citations(self, patent_ids: List[str], direction: str = 'both') -> List[Dict]:
        """
        获取引证文献
        注意: SerpApi 的 'google_patents' 引擎主要用于搜索，
        它不直接提供结构化的 'citation' 字段。
        若需获取引证，通常需要解析具体的专利详情页或使用 Google Scholar 引擎。
        此处为了接口一致性返回空列表，并记录日志。
        """
        logger.warning(f"[GooglePatents] 'get_citations' is not fully supported by SerpApi Search API. Returning empty.")
        return []