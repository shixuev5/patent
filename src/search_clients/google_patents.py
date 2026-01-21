import requests
from typing import List, Dict
from loguru import logger
from config import settings
from src.search_clients.base import BaseSearchClient

class GooglePatentsClient(BaseSearchClient):
    def __init__(self):
        self.api_key = settings.SERPAPI_KEY
        self.base_url = "https://serpapi.com/search.json"
        if not self.api_key:
            logger.warning("[GooglePatents] SERPAPI_KEY is missing.")

    def _normalize_result(self, raw_item: Dict) -> Dict:
        """
        标准化返回结果 (对齐 Zhihuiya 格式)
        """
        publication_number = raw_item.get("publication_number", "")
        
        # 处理人员
        assignees = [raw_item.get("assignee")] if raw_item.get("assignee") else []
        inventors = [raw_item.get("inventor")] if raw_item.get("inventor") else []

        # 处理分类号
        cpcs = raw_item.get("classifications", [])

        # 处理图片 (SerpApi 有时返回 thumbnail)
        image_url = raw_item.get("thumbnail", "")

        return {
            "id": publication_number,
            "pn": publication_number,
            "title": raw_item.get("title", ""),
            "abstract": raw_item.get("snippet", ""), # Google snippet
            
            # --- 补齐字段以保持一致性 ---
            "claims": "", # Google 列表页通常不返回权利要求，留空
            "image_url": image_url, # 对应 Patsnap 的 image_url
            "cpc": cpcs,
            "assignees": assignees,
            "inventors": inventors, # Patsnap 那边也可以补上这个
            "publication_date": raw_item.get("publication_date", ""),
            
            # Google 不返回数值分数，给默认值或尝试解析
            "score": 0, 
            "source_db": "GooglePatents",
            
            # Google 特有字段保留，但渲染器可能不通用
            "link": raw_item.get("link", ""),
            "pdf_link": raw_item.get("pdf", "")
        }

    def search(self, query: str, db: str = "ALL", limit: int = 50) -> List[Dict]:
        """普通检索"""
        if not self.api_key:
            return []

        # status 参数映射
        status_param = "GRANT" if db.upper() == "GRANT" else "APPLICATION" if db.upper() == "APPLICATION" else ""

        params = {
            "engine": "google_patents",
            "q": query,
            "api_key": self.api_key,
            "num": min(limit, 100), 
            "status": status_param,
            "sort": "relevance"
        }

        return self._do_request(params)

    def search_semantic(self, text: str, to_date: str = "", limit: int = 50) -> List[Dict]:
        """
        Google 的语义检索实现
        逻辑：直接使用 q 参数，并转换日期格式
        """
        query = text
        
        # 处理查新截止日期
        # 输入格式 YYYYMMDD -> Google 格式 YYYY-MM-DD
        # Google 语法: before:publication:2023-05-19
        if to_date and len(to_date) == 8:
            formatted_date = f"{to_date[:4]}-{to_date[4:6]}-{to_date[6:]}"
            query = f"{query} before:publication:{formatted_date}"
            logger.info(f"[GooglePatents] Applied date filter: {formatted_date}")

        # 复用 search 逻辑，Google 对长文本会自动进行语义匹配
        return self.search(query, db="ALL", limit=limit)

    def _do_request(self, params: Dict) -> List[Dict]:
        """统一请求处理"""
        try:
            # logger.info(f"[GooglePatents] Searching: {params.get('q')[:50]}...")
            resp = requests.get(self.base_url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            if "error" in data:
                logger.error(f"[GooglePatents] API Error: {data['error']}")
                return []

            raw_results = data.get("organic_results", [])
            return [self._normalize_result(item) for item in raw_results]

        except Exception as e:
            logger.error(f"[GooglePatents] Request failed: {e}")
            return []

    def get_citations(self, patent_ids: List[str], direction: str = 'both') -> List[Dict]:
        return []