import os
import re
from pathlib import Path
from typing import Dict, List, Mapping
from dotenv import load_dotenv

# 项目版本号
VERSION = "1.3.0"

# 加载 .env 环境变量
load_dotenv()


def load_zhihuiya_accounts(environ: Mapping[str, str] | None = None) -> List[Dict[str, str]]:
    """从编号环境变量中加载智慧芽多账号配置。"""
    env = environ or os.environ
    pattern = re.compile(r"^ZHIHUIYA_ACCOUNTS__(\d+)__(USERNAME|PASSWORD)$")
    grouped: Dict[int, Dict[str, str]] = {}

    for key, raw_value in env.items():
        match = pattern.match(str(key))
        if not match:
            continue
        index = int(match.group(1))
        field = match.group(2).lower()
        grouped.setdefault(index, {})[field] = str(raw_value or "").strip()

    accounts: List[Dict[str, str]] = []
    for index in sorted(grouped):
        username = grouped[index].get("username", "").strip()
        password = grouped[index].get("password", "").strip()
        if username and password:
            accounts.append({"username": username, "password": password})
    return accounts

# 检测是否在 Hugging Face Spaces 上运行
def is_huggingface_spaces():
    """检测是否在 Hugging Face Spaces 环境中运行"""
    return "SPACE_ID" in os.environ or "SPACES_DOMAIN" in os.environ or "HF_TOKEN" in os.environ

# Hugging Face Spaces 特定配置
if is_huggingface_spaces():
    print("检测到 Hugging Face Spaces 环境，使用相应配置")


class Settings:
    # --- 基础路径配置 ---
    BASE_DIR = Path(__file__).resolve().parent
    STORAGE_ROOT = Path(os.getenv("APP_STORAGE_ROOT", BASE_DIR))
    OUTPUT_DIR = Path(os.getenv("APP_OUTPUT_DIR", STORAGE_ROOT / "output"))
    DATA_DIR = Path(os.getenv("APP_DATA_DIR", STORAGE_ROOT / "data"))
    UPLOAD_DIR = Path(os.getenv("APP_UPLOAD_DIR", STORAGE_ROOT / "uploads"))
    ASSETS_DIR = BASE_DIR / "assets"

    # Hugging Face Spaces 特定配置
    if is_huggingface_spaces():
        # 在 Spaces 上，使用 /app 作为存储根目录
        STORAGE_ROOT = Path(os.getenv("APP_STORAGE_ROOT", "/app"))
        OUTPUT_DIR = Path(os.getenv("APP_OUTPUT_DIR", STORAGE_ROOT / "output"))
        DATA_DIR = Path(os.getenv("APP_DATA_DIR", STORAGE_ROOT / "data"))
        UPLOAD_DIR = Path(os.getenv("APP_UPLOAD_DIR", STORAGE_ROOT / "uploads"))
        print(f"Spaces 配置: 存储根目录 = {STORAGE_ROOT}")

    # 确保字体文件路径 (请手动放入 simhei.ttf 到 assets 目录)
    FONT_PATH = ASSETS_DIR / "simhei.ttf"

    # --- 核心 LLM 配置（两档） ---
    LLM_API_KEY = os.getenv("LLM_API_KEY")
    LLM_BASE_URL = os.getenv("LLM_BASE_URL")
    LLM_MODEL_DEFAULT = os.getenv("LLM_MODEL_DEFAULT")
    LLM_MODEL_LARGE = os.getenv("LLM_MODEL_LARGE")
    LLM_REQUEST_TIMEOUT_SECONDS = int(os.getenv("LLM_REQUEST_TIMEOUT_SECONDS", "600"))

    # --- 视觉模型配置（两档） ---
    VLM_API_KEY = os.getenv("VLM_API_KEY")
    VLM_BASE_URL = os.getenv("VLM_BASE_URL")
    VLM_MODEL_DEFAULT = os.getenv("VLM_MODEL_DEFAULT")
    VLM_MODEL_LARGE = os.getenv("VLM_MODEL_LARGE")
    VLM_MAX_WORKERS = max(1, int(os.getenv("VLM_MAX_WORKERS", "4")))

    # --- 智慧芽 ---
    ZHIHUIYA_CLIENT_ID = os.getenv(
        "ZHIHUIYA_CLIENT_ID", "f58bbdfdd63549dbb64fed4b816c8bfc"
    )

    # --- Mineru ---
    MINERU_API_KEY = os.getenv("MINERU_API_KEY", "")  
    MINERU_BASE_URL = os.getenv("MINERU_BASE_URL", "https://mineru.net/api/v4")
    MINERU_TEMP_FOLDER = "mineru_raw"
    MINERU_REQUEST_TIMEOUT_SECONDS = int(os.getenv("MINERU_REQUEST_TIMEOUT_SECONDS", "60"))

    # --- Office Action Reply 并行配置 ---
    OAR_MAX_CONCURRENCY = max(1, int(os.getenv("OAR_MAX_CONCURRENCY", "4")))
    OAR_WORKFLOW_TIMEOUT_SECONDS = int(os.getenv("OAR_WORKFLOW_TIMEOUT_SECONDS", "1800"))

    # --- 外部检索/下载超时 ---
    RETRIEVAL_REQUEST_TIMEOUT_SECONDS = int(os.getenv("RETRIEVAL_REQUEST_TIMEOUT_SECONDS", "30"))
    DOWNLOAD_REQUEST_TIMEOUT_SECONDS = int(os.getenv("DOWNLOAD_REQUEST_TIMEOUT_SECONDS", "60"))

    # --- 通用本地检索（可复用于 ai_reply / patent_analysis）---
    LOCAL_RETRIEVAL_ENABLED = os.getenv("LOCAL_RETRIEVAL_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
    LOCAL_RETRIEVAL_CHUNK_CHARS = max(200, int(os.getenv("LOCAL_RETRIEVAL_CHUNK_CHARS", "600")))
    LOCAL_RETRIEVAL_CHUNK_OVERLAP = max(0, int(os.getenv("LOCAL_RETRIEVAL_CHUNK_OVERLAP", "120")))
    LOCAL_RETRIEVAL_CANDIDATE_K = max(4, int(os.getenv("LOCAL_RETRIEVAL_CANDIDATE_K", "24")))
    LOCAL_RETRIEVAL_RERANK_K = max(2, int(os.getenv("LOCAL_RETRIEVAL_RERANK_K", "8")))
    LOCAL_RETRIEVAL_CONTEXT_K = max(1, int(os.getenv("LOCAL_RETRIEVAL_CONTEXT_K", "6")))
    LOCAL_RETRIEVAL_MAX_CONTEXT_CHARS = max(400, int(os.getenv("LOCAL_RETRIEVAL_MAX_CONTEXT_CHARS", "2200")))
    LOCAL_RETRIEVAL_MAX_QUOTE_CHARS = max(80, int(os.getenv("LOCAL_RETRIEVAL_MAX_QUOTE_CHARS", "180")))
    RETRIEVAL_API_KEY = os.getenv("RETRIEVAL_API_KEY", "").strip()
    RETRIEVAL_BASE_URL = os.getenv("RETRIEVAL_BASE_URL", "").strip()
    RETRIEVAL_EMBEDDING_MODEL = (
        os.getenv("RETRIEVAL_EMBEDDING_MODEL", "text-embedding-v4").strip() or "text-embedding-v4"
    )
    RETRIEVAL_RERANK_MODEL = (
        os.getenv("RETRIEVAL_RERANK_MODEL", "qwen3-rerank").strip() or "qwen3-rerank"
    )
    LOCAL_RETRIEVAL_SQLITE_VEC_EXTENSION_PATH = os.getenv(
        "LOCAL_RETRIEVAL_SQLITE_VEC_EXTENSION_PATH", ""
    ).strip()
    
    # --- PaddleOCR ---
    OCR_API_KEY = os.getenv("OCR_API_KEY", "")  
    OCR_BASE_URL = os.getenv("OCR_BASE_URL", "https://j9dd7babo5tcocz9.aistudio-app.com/ocr")

    # --- Authing ---
    AUTHING_APP_ID = os.getenv("AUTHING_APP_ID", "").strip()
    AUTHING_APP_SECRET = os.getenv("AUTHING_APP_SECRET", "").strip()
    AUTHING_DOMAIN = os.getenv("AUTHING_DOMAIN", "").strip()

    # --- Email notifications ---
    EMAIL_NOTIFICATIONS_ENABLED = os.getenv("EMAIL_NOTIFICATIONS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    EMAIL_PROVIDER = os.getenv("EMAIL_PROVIDER", "brevo").strip().lower() or "brevo"
    BREVO_API_KEY = os.getenv("BREVO_API_KEY", "").strip()
    BREVO_API_BASE_URL = os.getenv("BREVO_API_BASE_URL", "https://api.brevo.com/v3").strip()
    BREVO_FROM_ADDRESS = os.getenv("BREVO_FROM_ADDRESS", "").strip()
    BREVO_FROM_NAME = os.getenv("BREVO_FROM_NAME", "").strip()
    BREVO_TIMEOUT_SECONDS = int(os.getenv("BREVO_TIMEOUT_SECONDS", "30"))
    SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME", "").strip()
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM_ADDRESS = os.getenv("SMTP_FROM_ADDRESS", "").strip()
    SMTP_FROM_NAME = os.getenv("SMTP_FROM_NAME", "").strip()
    SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "false").strip().lower() in {"1", "true", "yes", "on"}
    SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "false").strip().lower() in {"1", "true", "yes", "on"}

    # --- WeChat integration / internal gateway ---
    WECHAT_INTEGRATION_ENABLED = os.getenv("WECHAT_INTEGRATION_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    INTERNAL_GATEWAY_TOKEN = os.getenv("INTERNAL_GATEWAY_TOKEN", "").strip()
    WECHAT_BIND_SESSION_TTL_SECONDS = max(60, int(os.getenv("WECHAT_BIND_SESSION_TTL_SECONDS", "600")))
    WECHAT_DELIVERY_MAX_ATTEMPTS = max(1, int(os.getenv("WECHAT_DELIVERY_MAX_ATTEMPTS", "3")))

    @property
    def ZHIHUIYA_ACCOUNTS(self) -> List[Dict[str, str]]:
        return load_zhihuiya_accounts()


settings = Settings()

# 自动创建基础目录
settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
settings.ASSETS_DIR.mkdir(parents=True, exist_ok=True)
