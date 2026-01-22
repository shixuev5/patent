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
            "x-api-version": "2.0",
        }

    def _login(self):
        """执行登录流程获取 Token"""
        logger.info("[Zhihuiya] Logging in...")

        # 1. 获取公钥
        try:
            pk_resp = self.session.get(
                "https://passport.zhihuiya.com/public/request_public_key"
            )
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
            "response_type": "TOKEN",
        }

        try:
            login_resp = self.session.post(
                "https://passport.zhihuiya.com/doLogin", json=payload
            )
            login_resp.raise_for_status()
            data = login_resp.json()
            self.token = data.get("token")
            self.headers["Authorization"] = f"Bearer {self.token}"
            logger.success("[Zhihuiya] Login successful.")
            
            # 4. 登录成功后，初始化查询字段配置
            self._configure_search_settings()
        except Exception as e:
            logger.error(f"[Zhihuiya] Login failed: {e}")
            raise
        
    def _configure_search_settings(self):
        """
        配置搜索结果返回字段 (PUT 请求)
        URL: https://search-service.zhihuiya.com/core-search-api/search/srp/setting
        """
        url = "https://search-service.zhihuiya.com/core-search-api/search/srp/setting"
        payload = {
            "view": "standard",
            "fields": "PN,TITLE,ABST,ANC,PBD,ICLMS,ADC",
            "search_mode": "publication"
        }
        
        try:
            logger.info("[Zhihuiya] Configuring search result settings...")
            resp = self.session.put(url, headers=self.headers, json=payload)
            resp.raise_for_status()
            logger.success("[Zhihuiya] Search settings configured successfully.")
        except Exception as e:
            # 记录错误但不阻断流程，可能使用默认字段
            logger.error(f"[Zhihuiya] Failed to configure search settings: {e}")

    def _clean_html(self, text: str) -> str:
        """清洗智慧芽返回的高亮标签和div标签"""
        if not text:
            return ""
        # 移除 <div ...> 和 </div>
        text = re.sub(r"<[^>]+>", "", text)
        # 移除可能存在的 &nbsp; 等实体
        text = text.replace("&nbsp;", " ").strip()
        return text

    def _normalize_result(self, raw_item: Dict) -> Dict:
        """将智慧芽原始数据标准化为系统通用格式 (适配语义检索返回字段)"""
        
        # 1. 处理分数: 将 "88%" 转换为 88.0
        raw_score = raw_item.get("RELEVANCY", 0)
        score = 0.0
        try:
            if isinstance(raw_score, str) and "%" in raw_score:
                score = float(raw_score.replace("%", "").strip())
            elif isinstance(raw_score, (int, float)):
                score = float(raw_score)
        except (ValueError, TypeError):
            score = 0.0

        # 2. 处理 CPC
        cpcs = []
        if "ADC" in raw_item and isinstance(raw_item["ADC"], list):
            for group in raw_item["ADC"]:
                if "code" in group:
                    cpcs.append(str(group["code"]))

        # 3. 处理申请人
        assignees = []
        if "ANC" in raw_item and isinstance(raw_item["ANC"], dict):
            assignees = raw_item["ANC"].get("OFFICIAL", [])
        
        # 4. 安全提取图片 URL
        image_info = raw_item.get("PATSNAP_IMAGE")
        image_url = None
        if isinstance(image_info, dict):
            image_url = image_info.get("url")
        elif isinstance(image_info, str):
            image_url = image_info

        return {
            "id": raw_item.get("PATENT_ID") or raw_item.get("PN"),
            "pn": raw_item.get("PN"),
            "title": self._clean_html(raw_item.get("TITLE", "")),
            "abstract": self._clean_html(raw_item.get("ABST", "")),
            "claims": self._clean_html(raw_item.get("ICLMS", "")),
            "cpc": cpcs, # 去重
            "assignees": assignees,
            "publication_date": raw_item.get("PBD", ""),
            "score": score, # 格式化后的数字 (e.g. 88.0)
            "source_db": "Zhihuiya",
            "image_url": image_url
        }

    def search(self, query: str, limit: int = 50) -> List[Dict]:
        """普通命令行检索"""
        if not self.token:
            self._login()

        url = "https://search-service.zhihuiya.com/core-search-api/search/srp/patents"
        payload = {
            "special_query": False,
            "view_type": "standard",
            "with_count": True,
            "_type": "query",
            "q": query,
            "sort": "sdesc",
            "page": 1,
            "limit": limit,
            "search_mode": "publication",
        }
        return self._do_post_request(url, payload)

    def search_semantic(
        self, text: str, to_date: str = "", limit: int = 50
    ) -> List[Dict]:
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
            "to_pbd": to_date,  # 关键：限制公开日早于此日期
            "ipc_logic_type": "AND",
            "query": text,
        }

        try:
            resp1 = self.session.post(
                step1_url, headers=self.headers, json=step1_payload
            )
            resp1.raise_for_status()
            data1 = resp1.json()

            # 解析 URL 参数获取 semantic_id
            # 响应示例: "...url": "_type=semantic&semantic_id=b1daedae...&sort=sdesc"
            result_url = data1.get("data", {}).get("url", "")
            match = re.search(r"semantic_id=([a-f0-9\-]+)", result_url)

            if not match:
                logger.error(
                    f"[Zhihuiya] Failed to extract semantic_id from: {result_url}"
                )
                return []

            semantic_id = match.group(1)
            logger.debug(f"[Zhihuiya] Got Semantic ID: {semantic_id}")

        except Exception as e:
            logger.error(f"[Zhihuiya] Semantic Step 1 failed: {e}")
            return []

        # --- Step 2: 获取检索结果 ---
        step2_url = (
            "https://search-service.zhihuiya.com/core-search-api/search/srp/patents"
        )
        step2_payload = {
            "special_query": False,
            "view_type": "standard",
            "with_count": True,
            "_type": "semantic",
            "q": f"[SEMANTIC]{semantic_id}",
            "semantic_id": semantic_id,
            "search_mode": "unset",
            "sort": "sdesc",  # 相关度降序
            "page": 1,
            "limit": limit,
        }

        return self._do_post_request(step2_url, step2_payload)

    def _do_post_request(self, url: str, payload: Dict) -> List[Dict]:
        """统一的 POST 请求处理与重试逻辑"""
        for attempt in range(2):
            try:
                resp = self.session.post(url, headers=self.headers, json=payload)

                # Token 过期重试
                if resp.status_code == 401 or (
                    resp.headers.get("content-type") == "application/json"
                    and "token expired" in resp.text
                ):
                    logger.warning("[Zhihuiya] Token expired, refreshing...")
                    self._login()
                    continue

                resp.raise_for_status()
                data = resp.json()

                if data.get("status") is False:
                    logger.error(f"[Zhihuiya] API Error: {data}")
                    return []

                count_info = data.get("data", {}).get("patent_count", {})
                total_hits = count_info.get("total_count", 0)
                if total_hits == 0:
                    total_hits = count_info.get("group_count", 0)

                raw_list = data.get("data", {}).get("patent_data", [])
                normalized_results = [self._normalize_result(item) for item in raw_list]

                return {"total": total_hits, "results": normalized_results}

            except Exception as e:
                logger.error(f"[Zhihuiya] Request failed: {e}")
                return {"total": 0, "results": []}
        return {"total": 0, "results": []}
