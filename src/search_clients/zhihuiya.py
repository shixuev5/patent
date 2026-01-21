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
            login_resp = self.session.post("https://passport.zhihuiya.com/doLogin", json=payload)
            login_resp.raise_for_status()
            data = login_resp.json()
            self.token = data.get("token")
            self.headers["Authorization"] = f"Bearer {self.token}"
            logger.success("[Zhihuiya] Login successful.")
        except Exception as e:
            logger.error(f"[Zhihuiya] Login failed: {e}")
            raise

    def _clean_html(self, text: str) -> str:
        """清洗智慧芽返回的高亮标签和div标签"""
        if not text:
            return ""
        # 移除 <div ...> 和 </div>
        text = re.sub(r'<[^>]+>', '', text)
        # 移除可能存在的 &nbsp; 等实体
        text = text.replace("&nbsp;", " ").strip()
        return text

    def _normalize_result(self, raw_item: Dict) -> Dict:
        """将智慧芽原始数据标准化为系统通用格式 (适配语义检索返回字段)"""
        # 1. 提取分类号 (兼容 ADC 结构)
        cpcs = []
        if "ADC" in raw_item:
            for group in raw_item["ADC"]:
                if "code" in group:
                    cpcs.append(str(group["code"]))
                if "node_list" in group:
                    for node in group["node_list"]:
                        if "code" in node:
                            cpcs.append(str(node["code"]))
        
        # 2. 提取申请人 (语义检索返回 ANCS, 普通检索返回 ANC.OFFICIAL)
        assignees = []
        if "ANCS" in raw_item:
            assignees = raw_item["ANCS"]
        elif "ANC" in raw_item and "OFFICIAL" in raw_item["ANC"]:
            assignees = raw_item["ANC"]["OFFICIAL"]

        # 3. 提取摘要和权利要求
        # 语义检索倾向于返回 MCLMS (Main Claims)
        claims_text = raw_item.get("MCLMS", "") or raw_item.get("CLMS", "")
        
        return {
            "id": raw_item.get("PATENT_ID") or raw_item.get("PN"),
            "pn": raw_item.get("PN"),
            "title": self._clean_html(raw_item.get("TITLE", "")),
            "abstract": self._clean_html(raw_item.get("ABST", "")),
            "claims": self._clean_html(claims_text), # 新增权利要求字段
            "cpc": list(set(cpcs)),
            "assignees": assignees,
            "publication_date": raw_item.get("PBD"),
            # 语义检索返回 "88%" 字符串，普通检索返回数字，统一处理
            "score": raw_item.get("RELEVANCY", raw_item.get("score", 0)), 
            "source_db": "Zhihuiya",
            "image_url": raw_item.get("PATSNAP_IMAGE", {}).get("url") # 如果有附图
        }

    def search(self, query: str, db: str = "publication", limit: int = 50) -> List[Dict]:
        """普通命令行检索"""
        if not self.token:
            self._login()
            
        url = "https://search-service.zhihuiya.com/core-search-api/search/srp/patents"
        payload = {
            "with_count": True,
            "q": query,
            "sort": "sdesc",
            "page": 1,
            "limit": limit,
            "search_mode": db 
        }
        return self._do_post_request(url, payload)

    def search_semantic(self, text: str, to_date: str = "", limit: int = 50) -> List[Dict]:
        """
        执行语义检索 (两步走)
        :param text: 自然语言文本
        :param to_date: 截止日期 (YYYYMMDD)，用于查新逻辑
        :param limit: 返回数量
        """
        if not self.token:
            self._login()

        logger.info(f"[Zhihuiya] Starting Semantic Search (Limit date: {to_date})...")

        # --- Step 1: 获取 Semantic ID ---
        step1_url = "https://search-service.zhihuiya.com/core-search-api/search/input/search/semantic"
        step1_payload = {
            "from_pbd": "",
            "to_pbd": to_date, # 关键：限制公开日早于此日期
            "ipc_logic_type": "AND",
            "query": text
        }

        try:
            resp1 = self.session.post(step1_url, headers=self.headers, json=step1_payload)
            resp1.raise_for_status()
            data1 = resp1.json()
            
            # 解析 URL 参数获取 semantic_id
            # 响应示例: "...url": "_type=semantic&semantic_id=b1daedae...&sort=sdesc"
            result_url = data1.get("data", {}).get("url", "")
            match = re.search(r"semantic_id=([a-f0-9\-]+)", result_url)
            
            if not match:
                logger.error(f"[Zhihuiya] Failed to extract semantic_id from: {result_url}")
                return []
            
            semantic_id = match.group(1)
            logger.debug(f"[Zhihuiya] Got Semantic ID: {semantic_id}")

        except Exception as e:
            logger.error(f"[Zhihuiya] Semantic Step 1 failed: {e}")
            return []

        # --- Step 2: 获取检索结果 ---
        step2_url = "https://search-service.zhihuiya.com/core-search-api/search/srp/patents"
        step2_payload = {
            "special_query": False,
            "view_type": "standard",
            "with_count": True,
            "_type": "semantic",
            "q": f"[SEMANTIC]{semantic_id}",
            "semantic_id": semantic_id,
            "search_mode": "unset",
            "sort": "sdesc", # 相关度降序
            "page": 1,
            "limit": limit
        }

        return self._do_post_request(step2_url, step2_payload)

    def _do_post_request(self, url: str, payload: Dict) -> List[Dict]:
        """统一的 POST 请求处理与重试逻辑"""
        for attempt in range(2):
            try:
                resp = self.session.post(url, headers=self.headers, json=payload)
                
                # Token 过期重试
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
                logger.error(f"[Zhihuiya] Request failed: {e}")
                return []
        return []

    def get_citations(self, patent_ids: List[str], direction: str = 'both') -> List[Dict]:
        """
        智慧芽的引文接口 (示例桩代码，需根据实际引文API补充)
        """
        logger.info(f"[Zhihuiya] Fetching citations for {len(patent_ids)} patents (Mocked)")
        # TODO: Implement actual /citations endpoint if available in docs
        return []