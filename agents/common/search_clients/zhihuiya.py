import requests
import re
import os
import threading
import base64
import json
import random
import time
from typing import List, Dict, Optional, Any
from loguru import logger
from config import settings
from agents.ai_reply.src.utils import is_patent_application_number
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5


def _to_public_key_pem(public_key_text: str) -> str:
    """将智慧芽返回的公钥文本规范化为 PEM 格式。"""
    text = (public_key_text or "").strip()
    if not text:
        raise ValueError("public key is empty")

    # 某些网关会返回 JSON 包裹的 key，先尝试解包
    if text.startswith("{") or text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                for field in ("publicKey", "public_key", "key"):
                    value = parsed.get(field)
                    if isinstance(value, str) and value.strip():
                        text = value.strip()
                        break
                else:
                    data_value = parsed.get("data")
                    if isinstance(data_value, str) and data_value.strip():
                        text = data_value.strip()
        except Exception:
            # JSON 解析失败时按原始文本继续兜底处理
            pass

    # 将字符串中的转义换行恢复成真实换行
    text = text.replace("\\r", "").replace("\\n", "\n").strip()

    if "BEGIN PUBLIC KEY" in text and "END PUBLIC KEY" in text:
        match = re.search(
            r"-----BEGIN PUBLIC KEY-----.*?-----END PUBLIC KEY-----",
            text,
            flags=re.S,
        )
        if match:
            return match.group(0).strip()
        return text

    # 兜底：按 base64 key body 封装为 PEM
    body = re.sub(r"\s+", "", text)
    if not body:
        raise ValueError("public key body is empty")
    wrapped = "\n".join(body[i : i + 64] for i in range(0, len(body), 64))
    return f"-----BEGIN PUBLIC KEY-----\n{wrapped}\n-----END PUBLIC KEY-----"


def rsa_encrypt(message: str, public_key_text: str) -> str:
    """
    使用 RSA 公钥加密字符串 (PKCS1_v1_5 填充)
    对应前端 JSEncrypt 逻辑
    """
    try:
        public_key_pem = _to_public_key_pem(public_key_text)
        key = RSA.import_key(public_key_pem)
        cipher = PKCS1_v1_5.new(key)
        ciphertext = cipher.encrypt(message.encode('utf-8'))
        return base64.b64encode(ciphertext).decode('utf-8')
    except Exception as e:
        raise ValueError(f"Encryption failed: {e}")
from agents.common.search_clients.base import BaseSearchClient


class ZhihuiyaClient(BaseSearchClient):
    _account_cooldown_seconds = 30 * 60
    _account_cooldowns: Dict[str, float] = {}
    _account_cooldown_lock = threading.Lock()

    def __init__(self):
        self.session = requests.Session()
        self.accounts = [dict(item) for item in settings.ZHIHUIYA_ACCOUNTS]
        self.token = None
        self.current_account: Optional[Dict[str, str]] = None
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "x-api-version": "2.0",
        }
        self.request_timeout = settings.RETRIEVAL_REQUEST_TIMEOUT_SECONDS
        self.download_timeout = settings.DOWNLOAD_REQUEST_TIMEOUT_SECONDS
        # 依然建议保留登录锁，防止 Token 过期时多个线程同时触发重新登录
        self._login_lock = threading.RLock()

    @classmethod
    def _cleanup_expired_cooldowns(cls, now: Optional[float] = None):
        now_ts = now if now is not None else time.monotonic()
        expired_usernames = [
            username
            for username, cooldown_until in cls._account_cooldowns.items()
            if cooldown_until <= now_ts
        ]
        for username in expired_usernames:
            cls._account_cooldowns.pop(username, None)

    @classmethod
    def _mark_account_cooldown(cls, username: str, reason: str):
        if not username:
            return
        cooldown_until = time.monotonic() + cls._account_cooldown_seconds
        with cls._account_cooldown_lock:
            cls._cleanup_expired_cooldowns()
            cls._account_cooldowns[username] = cooldown_until
        remaining_text = cls._format_cooldown_remaining(cooldown_until - time.monotonic())
        logger.warning(
            f"[智慧芽] 账号进入冷却：{username}，剩余：{remaining_text}，原因：{reason}"
        )

    @classmethod
    def _format_cooldown_remaining(cls, remaining_seconds: float) -> str:
        seconds = max(0, int(round(remaining_seconds)))
        minutes, seconds = divmod(seconds, 60)
        if minutes <= 0:
            return f"{seconds}s"
        if seconds == 0:
            return f"{minutes}m"
        return f"{minutes}m{seconds}s"

    @classmethod
    def _describe_cooldowns(cls, now: Optional[float] = None) -> str:
        now_ts = now if now is not None else time.monotonic()
        with cls._account_cooldown_lock:
            cls._cleanup_expired_cooldowns(now_ts)
            snapshots = [
                (
                    username,
                    cls._format_cooldown_remaining(cooldown_until - now_ts),
                )
                for username, cooldown_until in sorted(cls._account_cooldowns.items())
                if cooldown_until > now_ts
            ]
        if not snapshots:
            return "无"
        return ", ".join(f"{username}({remaining})" for username, remaining in snapshots)

    def _clear_auth_state(self):
        self.token = None
        self.current_account = None
        self.headers.pop("Authorization", None)

    def _pick_login_candidates(self) -> List[Dict[str, str]]:
        if not self.accounts:
            return []

        now_ts = time.monotonic()
        with self._account_cooldown_lock:
            self._cleanup_expired_cooldowns(now_ts)
            available_accounts = [
                account
                for account in self.accounts
                if self._account_cooldowns.get(account["username"], 0.0) <= now_ts
            ]

        candidates = available_accounts or [dict(account) for account in self.accounts]
        random.shuffle(candidates)
        if not available_accounts:
            logger.warning(
                "[智慧芽] 所有账号都在冷却中，本次将忽略冷却状态重试全部账号。"
                f" 当前冷却池：{self._describe_cooldowns(now_ts)}"
            )
        return candidates

    def _fetch_login_public_key(self) -> str:
        last_error: Optional[Exception] = None
        for attempt in range(2):
            try:
                pk_resp = self.session.get(
                    "https://passport.zhihuiya.com/public/request_public_key",
                    timeout=self.request_timeout,
                )
                pk_resp.raise_for_status()
                return pk_resp.text
            except Exception as e:
                last_error = e
                logger.warning(
                    f"[智慧芽] 公钥获取失败（第 {attempt + 1} 次）：{e}"
                )
        raise RuntimeError(f"获取公钥失败（已重试）：{last_error}")

    def _login_with_account(self, account: Dict[str, str], public_key_text: str):
        encrypted_password = rsa_encrypt(account["password"], public_key_text)
        payload = {
            "username": account["username"],
            "password": encrypted_password,
            "remember_me": "on",
            "client_id": settings.ZHIHUIYA_CLIENT_ID,
            "from": "account",
            "response_type": "TOKEN",
        }
        login_resp = self.session.post(
            "https://passport.zhihuiya.com/doLogin",
            json=payload,
            timeout=self.request_timeout,
        )
        try:
            data = login_resp.json()
        except Exception:
            data = {}

        if login_resp.status_code >= 400:
            error_message = (
                data.get("error_message")
                or data.get("message")
                or data.get("msg")
                or login_resp.text
                or f"http {login_resp.status_code}"
            )
            raise RuntimeError(str(error_message).strip())

        token = str(data.get("token") or "").strip()
        if not token:
            raise RuntimeError("登录成功响应缺少 token")

        self.current_account = dict(account)
        self.token = token
        self.headers["Authorization"] = f"Bearer {token}"
        logger.success(f"[智慧芽] 登录成功，账号：{account['username']}")

        # 登录成功后初始化查询字段配置，失败只记录日志，不阻断主流程
        self._configure_search_settings()

    def _handle_auth_failure(self, reason: str):
        username = ""
        if self.current_account:
            username = str(self.current_account.get("username") or "").strip()
        if username:
            self._mark_account_cooldown(username, reason)
        self._clear_auth_state()

    def _is_auth_failure_response(self, resp: requests.Response) -> bool:
        if resp.status_code in {401, 403}:
            return True

        body_text = str(getattr(resp, "text", "") or "").lower()
        if "token expired" in body_text or "invalid token" in body_text:
            return True

        try:
            payload = resp.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            payload_text = json.dumps(payload, ensure_ascii=False).lower()
            if "token expired" in payload_text or "invalid token" in payload_text:
                return True
        return False

    def _request_with_auth(self, method: str, url: str, **kwargs) -> requests.Response:
        base_headers = dict(kwargs.pop("headers", {}) or {})
        request_timeout = kwargs.pop("timeout", self.request_timeout)

        last_response: Optional[requests.Response] = None
        for _ in range(2):
            if not self.token:
                self._login()

            request_headers = dict(self.headers)
            request_headers.update(base_headers)
            request_fn = getattr(self.session, method.lower())
            response = request_fn(
                url,
                headers=request_headers,
                timeout=request_timeout,
                **kwargs,
            )
            last_response = response
            if self._is_auth_failure_response(response):
                username = ""
                if self.current_account:
                    username = str(self.current_account.get("username") or "").strip()
                logger.warning(
                    "[智慧芽] 检测到鉴权失败，正在切换账号重试。"
                    f" 账号：{username or 'unknown'}"
                )
                self._handle_auth_failure("鉴权失败")
                continue
            return response

        if last_response is not None and self._is_auth_failure_response(last_response):
            raise RuntimeError("智慧芽鉴权失败，所有账号重试后仍不可用")
        raise RuntimeError("智慧芽请求失败，未获得有效响应")

    def _login(self):
        """执行登录流程获取 Token"""
        logger.info("[智慧芽] 正在登录...")

        if self.token:
            return

        with self._login_lock:
            if self.token:
                return
            if not self.accounts:
                raise RuntimeError("未配置可用的智慧芽账号")

            public_key_text = self._fetch_login_public_key()
            candidate_accounts = self._pick_login_candidates()
            login_errors: List[str] = []

            for account in candidate_accounts:
                username = account["username"]
                try:
                    self._login_with_account(account, public_key_text)
                    return
                except Exception as e:
                    self._clear_auth_state()
                    self._mark_account_cooldown(username, f"登录失败: {e}")
                    login_errors.append(f"{username}: {e}")
                    logger.warning(f"[智慧芽] 账号登录失败，尝试切换下一个账号：{username}")

            raise RuntimeError(
                "智慧芽全部账号登录失败: " + " | ".join(login_errors)
            )

    def _fetch_basic_info(self, patent_id: str) -> Dict[str, Any]:
        """
        [Private] 获取专利基础信息 (Basic)
        URL: /patent/id/{id}/basic
        """
        url = f"https://search-service.zhihuiya.com/core-search-api/search/patent/id/{patent_id}/basic"
        # 构造最小可用 Payload，避免依赖具体的 signature
        payload = {
            "_type": "query",
            "source_type": "search_result",
            "q": f"PATENT_ID:{patent_id}",
            "rows": "1",
            "page": 1
        }

        try:
            resp = self._request_with_auth("post", url, json=payload)
            resp.raise_for_status()
            data = resp.json()

            if not data.get("status"):
                return {}

            info = data.get("data", {})

            # 数据清洗
            # 1. 标题 (优先中文)
            title = info.get("TITLE", {}).get("CN", "") or info.get("TITLE", {}).get("EN", "")
            if not title and "TITLE_LANG" in info:
                title = info.get("TITLE_LANG", "")

            # 2. 摘要 (优先中文)
            abst = info.get("ABST", {}).get("CN", "") or info.get("ABST", {}).get("EN", "")

            # 3. 申请人 (归一化)
            assignees = info.get("AN", {}).get("OFFICIAL", [])

            # 4. 发明人
            inventors = [p.get("name") for p in info.get("IN", {}).get("CN", [])]

            return {
                "pn": info.get("PN"),
                "patent_id": info.get("PATENT_ID"),
                "title": self._clean_html(title),
                "abstract": self._clean_html(abst),
                "assignees": assignees,
                "inventors": inventors,
                "publication_date": info.get("PBD"),
                "application_date": info.get("APD"),
                "ipc": info.get("IPC", []),
                "cpc": info.get("CPC", []),
                "technical_benefit": info.get("AI_TECHNICAL_BENEFIT", ""), # AI 生成的技术效果
                "technical_problem": info.get("AI_TECHNICAL_PROBLEM", ""), # AI 生成的技术问题
            }
        except Exception as e:
            logger.error(f"[智慧芽] 获取基础信息失败：{e}")
            return {}

    def _fetch_claims(self, patent_id: str) -> str:
        """
        [Private] 获取权利要求 (Claims)
        URL: /patent/id/{id}/clms
        """
        url = f"https://search-service.zhihuiya.com/core-search-api/search/patent/id/{patent_id}/clms"
        payload = {
            "_type": "query",
            "source_type": "search_result",
            "q": f"PATENT_ID:{patent_id}"
        }

        try:
            resp = self._request_with_auth("post", url, json=payload)
            resp.raise_for_status()
            data = resp.json()

            # 提取 HTML 内容 (优先 CN)
            clms_html = data.get("data", {}).get("CLMS", {}).get("CN", "")
            return self._clean_html(clms_html)
        except Exception as e:
            logger.error(f"[智慧芽] 获取权利要求失败：{e}")
            return ""

    def _fetch_description(self, patent_id: str) -> str:
        """
        [Private] 获取说明书全文 (Description)
        URL: /patent/id/{id}/desc
        """
        url = f"https://search-service.zhihuiya.com/core-search-api/search/patent/id/{patent_id}/desc"
        payload = {
            "_type": "query",
            "source_type": "search_result",
            "q": f"PATENT_ID:{patent_id}"
        }

        try:
            resp = self._request_with_auth("post", url, json=payload)
            resp.raise_for_status()
            data = resp.json()

            # 提取 HTML 内容 (优先 CN)
            desc_html = data.get("data", {}).get("DESC", {}).get("CN", "")
            return self._clean_html(desc_html)
        except Exception as e:
            logger.error(f"[智慧芽] 获取说明书失败：{e}")
            return ""

    def _fetch_official_images(self, patent_id: str) -> List[str]:
        """
        [Private] 获取官方附图列表
        URL: /patent/id/{id}/official-image (GET)
        """
        url = f"https://search-service.zhihuiya.com/core-search-api/search/patent/id/{patent_id}/official-image"

        try:
            resp = self._request_with_auth("get", url)
            resp.raise_for_status()
            data = resp.json()

            # 数据结构: data -> data -> {patent_id} -> OFFICIAL_IMAGE -> {ImageID: {url...}}
            images_map = data.get("data", {}).get(patent_id, {}).get("OFFICIAL_IMAGE", {})

            image_urls = []
            # 按 Image ID 排序 (通常 HDA...1, HDA...2) 保证顺序
            sorted_keys = sorted(images_map.keys())

            for key in sorted_keys:
                img_obj = images_map[key]
                if isinstance(img_obj, dict) and "url" in img_obj:
                    image_urls.append(img_obj["url"])

            return image_urls
        except Exception as e:
            logger.error(f"[智慧芽] 获取官方附图失败：{e}")
            return []

    def get_patent_detail(self, pn_or_id: str) -> Dict[str, Any]:
        """
        [Public] 获取专利完整详情 (聚合 Basic, Claims, Desc, Images)
        :param pn_or_id: 专利公开号(PN) 或 内部ID(PATENT_ID)
        """
        if not self.token:
            self._login()

        # 1. 确保持有 ID
        patent_id = pn_or_id
        # 如果看起来像 PN (包含字母且长度较短)，尝试转换。ID通常是UUID格式 (36位)
        if len(pn_or_id) < 30:
            resolved_id = self._get_patent_id_by_pn(pn_or_id)
            if resolved_id:
                patent_id = resolved_id
            else:
                logger.warning(f"[智慧芽] 无法解析 {pn_or_id} 的专利 ID，将尝试直接按 ID 使用。")

        logger.info(f"[智慧芽] 正在获取专利详情：{patent_id}...")

        # 2. 并行或串行获取各部分数据 (这里使用串行，简单可靠)
        # 如果追求性能，可以使用 ThreadPoolExecutor 并发这4个请求

        basic_info = self._fetch_basic_info(patent_id)
        if not basic_info:
            logger.error(f"[智慧芽] 获取基础信息失败：{patent_id}")
            return {}

        claims_text = self._fetch_claims(patent_id)
        desc_text = self._fetch_description(patent_id)
        images = self._fetch_official_images(patent_id)

        # 3. 组装结果
        detail = {
            **basic_info, # 展开基础信息
            "claims_text": claims_text,
            "description_text": desc_text,
            "images": images,
            "full_text_combined": (
                f"【标题】\n{basic_info.get('title', '')}\n\n"
                f"【摘要】\n{basic_info.get('abstract', '')}\n\n"
                f"【权利要求】\n{claims_text}\n\n"
                f"【说明书】\n{desc_text}"
            )
        }

        logger.success(f"[智慧芽] 专利详情获取完成：{basic_info.get('pn', patent_id)}")
        return detail

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
            logger.info("[智慧芽] 正在配置检索结果字段...")
            resp = self._request_with_auth("put", url, json=payload)
            resp.raise_for_status()
            logger.success("[智慧芽] 检索结果字段配置成功。")
        except Exception as e:
            # 记录错误但不阻断流程，可能使用默认字段
            logger.error(f"[智慧芽] 配置检索结果字段失败：{e}")

    def _clean_html(self, text: str) -> str:
        """清洗智慧芽返回的高亮标签和div标签"""
        if not text:
            return ""
        # 移除 <div ...> 和 </div>
        text = re.sub(r"<[^>]+>", "", text)
        # 移除 XML 实体
        text = text.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        # 移除连续空行
        text = re.sub(r"\n\s*\n", "\n\n", text)
        return text.strip()

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
            "ipc": raw_item.get("IPC", []) or [],
            "cpc": cpcs, # 去重
            "assignees": assignees,
            "publication_date": raw_item.get("PBD", ""),
            "application_date": raw_item.get("APD", ""),
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
    ) -> Dict[str, Any]:
        """
        执行语义检索 (两步走)
        :param text: 自然语言文本
        :param to_date: 截止日期 (YYYYMMDD)，用于查新逻辑
        :param limit: 返回数量
        """
        logger.info(f"[智慧芽] 开始语义检索（截止日期：{to_date}）...")

        # --- Step 1: 获取 Semantic ID ---
        step1_url = "https://search-service.zhihuiya.com/core-search-api/search/input/search/semantic"
        step1_payload = {
            "from_pbd": "",
            "to_pbd": to_date,  # 关键：限制公开日早于此日期
            "ipc_logic_type": "AND",
            "query": text,
        }

        try:
            resp1 = self._request_with_auth("post", step1_url, json=step1_payload)
            resp1.raise_for_status()
            data1 = resp1.json()

            # 解析 URL 参数获取 semantic_id
            # 响应示例: "...url": "_type=semantic&semantic_id=b1daedae...&sort=sdesc"
            result_url = data1.get("data", {}).get("url", "")
            match = re.search(r"semantic_id=([a-f0-9\-]+)", result_url)

            if not match:
                logger.error(
                    f"[智慧芽] 从返回结果提取 semantic_id 失败：{result_url}"
                )
                return []

            semantic_id = match.group(1)
            logger.debug(f"[智慧芽] 已获取语义检索 ID：{semantic_id}")

        except Exception as e:
            logger.error(f"[智慧芽] 语义检索步骤1失败：{e}")
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

    def get_similar_patents(
        self,
        pn: str,
        limit: int = 50,
        min_similarity_score: float = 0.0,
    ) -> Dict[str, Any]:
        """
        [Similar] 查询本专利的相似专利 (基于 PN 语义跳转)
        1. POST /jump 获取 semantic_id
        2. 执行语义检索
        :param pn: 目标专利号 (e.g., CN116745575A)
        :param limit: 返回数量
        """
        # --- Step 1: 通过 PN 获取 Semantic ID ---
        jump_url = "https://search-service.zhihuiya.com/core-search-api/search/input/search/semantic/jump"
        jump_payload = {
            "from_apd": "",
            "to_apd": "",
            "from_pbd": "",
            "to_pbd": "",
            "query": pn
        }

        try:
            logger.info(f"[智慧芽] 正在为 {pn} 生成相似专利语义 ID...")
            resp = self._request_with_auth("post", jump_url, json=jump_payload)
            resp.raise_for_status()
            data = resp.json()

            if not data.get("status"):
                logger.warning(f"[智慧芽] 跳转接口返回失败状态：{data}")
                return {"total": 0, "results": []}

            # 解析 URL 参数获取 semantic_id
            # 响应示例: "...url": "_type=semantic&semantic_id=05e4dd15...&sort=sdesc"
            result_url = data.get("data", {}).get("url", "")
            match = re.search(r"semantic_id=([a-f0-9\-]+)", result_url)

            if not match:
                logger.error(f"[智慧芽] 从跳转链接提取 semantic_id 失败：{result_url}")
                return {"total": 0, "results": []}

            semantic_id = match.group(1)
            logger.debug(f"[智慧芽] 已获取相似检索语义 ID：{semantic_id}")

        except Exception as e:
            logger.error(f"[智慧芽] 相似检索步骤1（Jump）失败：{e}")
            return {"total": 0, "results": []}

        # --- Step 2: 执行语义检索 ---
        search_url = "https://search-service.zhihuiya.com/core-search-api/search/srp/patents"
        search_payload = {
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

        # 复用统一的请求处理方法 + 相似度过滤
        response = self._do_post_request(search_url, search_payload)
        if not isinstance(response, dict):
            return {"total": 0, "results": []}

        raw_results = response.get("results", []) or []
        if not isinstance(raw_results, list):
            return {"total": 0, "results": []}

        filtered_results: List[Dict[str, Any]] = []
        for item in raw_results:
            item_dict = item if isinstance(item, dict) else {}
            score_raw = item_dict.get("score", 0.0)
            try:
                similarity_score = float(str(score_raw).replace("%", "").strip())
            except Exception:
                similarity_score = 0.0
            if similarity_score < float(min_similarity_score):
                continue
            item_dict["similarity_score"] = similarity_score
            filtered_results.append(item_dict)

        return {
            "total": len(filtered_results),
            "results": filtered_results,
            "min_similarity_score": float(min_similarity_score),
        }

    def get_family(self, pn: str, limit: int = 50) -> List[Dict]:
        """
        [Spider] 获取目标专利的同族专利 (Family members)
        语法: EFAM:(PN)
        """
        if not pn:
            return []

        # 构建同族检索式
        query = f"EFAM:({pn})"
        logger.debug(f"[智慧芽] 正在查询 {pn} 的同族专利，检索式：{query}")

        # 调用基础检索
        res = self.search(query=query, limit=limit)

        # Spider 逻辑只需要结果列表，过滤掉自身
        results = [doc for doc in res.get("results", []) if doc.get("pn") != pn]
        return results

    def get_citations(self, pn: str, limit: int = 50) -> List[Dict]:
        """
        [Spider] 获取目标专利的引用与被引用专利 (Citations)
        语法: BF_CITES:(PN) -> 包含前向(F)和后向(B)引证
        """
        if not pn:
            return []

        # 构建引证检索式
        query = f"BF_CITES:({pn})"
        logger.debug(f"[智慧芽] 正在查询 {pn} 的引证信息，检索式：{query}")

        # 调用基础检索
        res = self.search(query=query, limit=limit)

        return res.get("results", [])

    def _do_post_request(self, url: str, payload: Dict) -> List[Dict]:
        """统一的 POST 请求处理与重试逻辑"""
        try:
            resp = self._request_with_auth("post", url, json=payload)
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") is False:
                logger.error(f"[智慧芽] API 错误：{data}")
                return {"total": 0, "results": []}

            count_info = data.get("data", {}).get("patent_count", {})
            total_hits = count_info.get("total_count", 0)
            if total_hits == 0:
                total_hits = count_info.get("group_count", 0)

            raw_list = data.get("data", {}).get("patent_data", [])
            normalized_results = [self._normalize_result(item) for item in raw_list]

            return {"total": total_hits, "results": normalized_results}
        except Exception as e:
            logger.error(f"[智慧芽] 请求失败：{e}")
            return {"total": 0, "results": []}

    # =========================================================================
    # PDF 下载相关功能
    # =========================================================================

    def _query_patent_info_by_count(self, query: str) -> Optional[Dict[str, Any]]:
        """通过 count 接口查询 patent_info。"""
        url = "https://search-service.zhihuiya.com/core-search-api/search/patent/query/count"
        payload = {
            "search_mode": "publication",
            "q": str(query or "").strip(),
            "simple": True,
            "check_complexity": True,
        }
        if not payload["q"]:
            return None

        try:
            resp = self._request_with_auth("post", url, json=payload)
            resp.raise_for_status()
            data = resp.json()

            if not data.get("status"):
                logger.error(f"[智慧芽] count 查询失败：{data.get('message')}")
                return None

            patent_info = data.get("data", {}).get("patent_info", {})
            if isinstance(patent_info, dict):
                return patent_info
            return None
        except Exception as e:
            logger.error(f"[智慧芽] count 查询失败：{e}")
            return None

    def _get_patent_id_by_pn(self, pn: str) -> Optional[str]:
        """
        Step 1: 查询专利ID
        :param pn: 专利公开号 (如 CN116745575A) 或 专利申请号 (如 202211411308.6)
        :return: PATENT_ID 或 None
        """
        # 判断输入是专利公开号还是申请号
        if is_patent_application_number(pn):
            # 申请号格式，使用 APNO 字段查询
            logger.info(f"[智慧芽] 识别为申请号：{pn}")
            query = f"APNO:({pn})"
        else:
            # 公开号格式，使用 PN 字段查询
            logger.info(f"[智慧芽] 识别为公开号：{pn}")
            query = f"PN:({pn})"

        patent_info = self._query_patent_info_by_count(query)
        if not patent_info:
            return None

        patent_id = patent_info.get("PATENT_ID")
        if not patent_id:
            logger.warning(f"[智慧芽] 未查询到 {pn} 对应的专利 ID")
            return None
        return str(patent_id).strip() or None

    def get_publication_number_by_application_number(self, application_number: str) -> Optional[str]:
        """
        通过申请号查询公开号（count 接口返回 data.patent_info.PN）。
        """
        apno = str(application_number or "").strip()
        if not apno:
            return None

        patent_info = self._query_patent_info_by_count(f"APNO:({apno})")
        if not patent_info:
            logger.warning(f"[智慧芽] 未查询到申请号 {apno} 对应的 patent_info")
            return None

        publication_number = str(patent_info.get("PN") or "").strip().upper()
        if not publication_number:
            logger.warning(f"[智慧芽] 申请号 {apno} 的 count 返回中缺少 PN")
            return None
        return publication_number

    def _get_pdf_download_url(self, patent_id: str) -> Optional[str]:
        """
        Step 2: 获取下载地址
        :param patent_id: 内部专利ID
        :return: PDF下载链接 或 None
        """
        url = "https://search-service.zhihuiya.com/core-search-api/search/srp/pdf"
        params = {
            "patentId": patent_id,
            "ttlLang": "CN" # 默认使用CN，如果需要可扩展为参数
        }

        try:
            resp = self._request_with_auth("get", url, params=params)
            resp.raise_for_status()
            data = resp.json()

            if not data.get("status"):
                logger.error(f"[智慧芽] 获取 PDF 链接失败：{data.get('message')}")
                return None

            pdf_url = data.get("data", {}).get("PDF_D")
            return pdf_url
        except Exception as e:
            logger.error(f"[智慧芽] 获取 PDF 链接异常：{e}")
            return None

    def download_patent_document(self, pn: str, save_path: str) -> bool:
        """
        下载 PDF 到指定路径
        :param pn: 专利公开号
        :param save_path: 完整的保存路径 (含文件名)
        :return: Boolean 是否成功
        """
        if not self.token:
            self._login()

        logger.info(f"[智慧芽] 开始下载专利文件：{pn}")

        # 1. 获取 Patent ID
        patent_id = self._get_patent_id_by_pn(pn)
        if not patent_id:
            logger.error(f"[智慧芽] 终止下载：无法找到 {pn} 的专利 ID")
            return None

        # 2. 获取下载链接
        pdf_url = self._get_pdf_download_url(patent_id)
        if not pdf_url:
            logger.error(f"[智慧芽] 终止下载：无法获取 {pn} 的 PDF 链接")
            return None

        # 3. 下载文件
        try:
            # 确保目标文件夹存在
            os.makedirs(os.path.dirname(save_path), exist_ok=True)

            with self.session.get(pdf_url, stream=True, timeout=self.download_timeout) as r:
                r.raise_for_status()
                with open(save_path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)

            logger.success(f"[智慧芽] 下载完成：{save_path}")
            return True
        except Exception as e:
            logger.error(f"[智慧芽] 下载失败：{e}")
            if os.path.exists(save_path):
                os.remove(save_path)
            return False
