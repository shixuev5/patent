import requests
import re
from typing import List, Dict
from loguru import logger
from config import settings
from src.utils.crypto import rsa_encrypt
from src.search_clients.base import BaseSearchClient

class ZhihuiyaClient(BaseSearchClient):
    def __init__(self):
        self.session = requests.Session()
        self.token = None
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "x-api-version": "2.0"
        }

    def _login(self):
        """执行登录流程获取 Token"""
        logger.info("[Zhihuiya] Logging in...")
        
        # 1. 获取公钥
        try:
            pk_resp = self.session.get("https://passport.zhihuiya.com/public/request_public_key")
            pk_resp.raise_for_status()
            public_key = pk_resp.text
        except Exception as e:
            logger.error(f"[Zhihuiya] Failed to get public key: {e}")
            raise

        # 2. 加密密码
        encrypted_password = rsa_encrypt(settings.ZHIHUIYA_PASSWORD, public_key)

        # 3. 登录获取 Token
        payload = {
            "username": settings.ZHIHUIYA_USERNAME,
            "password": encrypted_password,
            "remember_me": "on",
            "client_id": settings.ZHIHUIYA_CLIENT_ID,
            "from": "account",
            "response_type": "TOKEN"
        }
        
        try:
            login_resp = self.session.post(f"{settings.ZHIHUIYA_PASSPORT_URL}/doLogin", json=payload)
            login_resp.raise_for_status()
            data = login_resp.json()
            self.token = data.get("token")
            self.headers["Authorization"] = f"Bearer {self.token}"
            logger.success("[Zhihuiya] Login successful.")
        except Exception as e:
            logger.error(f"[Zhihuiya] Login failed: {e}")
            raise

    def _clean_html(self, text: str) -> str:
        """清洗智慧芽返回的高亮标签 <span class=...>"""
        if not text:
            return ""
        return re.sub(r'<[^>]+>', '', text).strip()

    def _normalize_result(self, raw_item: Dict) -> Dict:
        """将智慧芽原始数据标准化为系统通用格式"""
        # 提取分类号 (ADC 结构复杂，这里简化提取)
        cpcs = []
        if "ADC" in raw_item:
            for group in raw_item["ADC"]:
                if "code" in group:
                    cpcs.append(str(group["code"]))
                if "node_list" in group:
                    for node in group["node_list"]:
                        if "code" in node:
                            cpcs.append(str(node["code"]))
        
        # 提取申请人
        assignees = []
        if "ANCS" in raw_item:
            assignees = raw_item["ANCS"]
        elif "ANC" in raw_item and "OFFICIAL" in raw_item["ANC"]:
            assignees = raw_item["ANC"]["OFFICIAL"]

        return {
            "id": raw_item.get("PATENT_ID") or raw_item.get("PN"),
            "pn": raw_item.get("PN"),
            "title": self._clean_html(raw_item.get("TITLE", "")),
            "abstract": self._clean_html(raw_item.get("ABST", "")),
            "cpc": list(set(cpcs)), # 去重
            "assignees": assignees,
            "publication_date": raw_item.get("PBD"),
            "score": raw_item.get("score", 0), # 搜索引擎的相关性分数
            "source_db": "Zhihuiya"
        }

    def search(self, query: str, db: str = "publication", limit: int = 50) -> List[Dict]:
        if not self.token:
            self._login()

        url = "https://search-service.zhihuiya.com/core-search-api/search/srp/patents"
        
        # 智慧芽特定的请求体
        payload = {
            "with_count": True,
            "q": query,
            "sort": "sdesc", # 相关性降序
            "page": 1,
            "limit": limit,
            "search_mode": db # publication
        }

        # 尝试请求，处理 Token 过期
        for attempt in range(2):
            try:
                resp = self.session.post(url, headers=self.headers, json=payload)
                
                # 检查 Token 是否过期
                if resp.status_code == 401 or (resp.headers.get("content-type") == "application/json" and "token expired" in resp.text):
                    logger.warning("[Zhihuiya] Token expired, refreshing...")
                    self._login()
                    continue
                
                resp.raise_for_status()
                data = resp.json()
                
                if data.get("status") is False:
                    logger.error(f"[Zhihuiya] API Error: {data}")
                    return []

                raw_list = data.get("data", {}).get("patent_data", [])
                return [self._normalize_result(item) for item in raw_list]

            except Exception as e:
                logger.error(f"[Zhihuiya] Search request failed: {e}")
                return []
        
        return []

    def get_citations(self, patent_ids: List[str], direction: str = 'both') -> List[Dict]:
        """
        智慧芽的引文接口 (示例桩代码，需根据实际引文API补充)
        """
        logger.info(f"[Zhihuiya] Fetching citations for {len(patent_ids)} patents (Mocked)")
        # TODO: Implement actual /citations endpoint if available in docs
        return []