import os
from pathlib import Path
from dotenv import load_dotenv

# 项目版本号
VERSION = "1.1.0"

# 加载 .env 环境变量
load_dotenv()

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
    ZHIHUIYA_USERNAME = os.getenv("ZHIHUIYA_USERNAME", "")
    ZHIHUIYA_PASSWORD = os.getenv("ZHIHUIYA_PASSWORD", "")
    ZHIHUIYA_CLIENT_ID = os.getenv(
        "ZHIHUIYA_CLIENT_ID", "f58bbdfdd63549dbb64fed4b816c8bfc"
    )

    # --- Mineru ---
    MINERU_API_KEY = os.getenv("MINERU_API_KEY", "")  
    MINERU_BASE_URL = os.getenv("MINERU_BASE_URL", "https://mineru.net/api/v4")
    MINERU_TEMP_FOLDER = "mineru_raw"
    MINERU_REQUEST_TIMEOUT_SECONDS = int(os.getenv("MINERU_REQUEST_TIMEOUT_SECONDS", "60"))

    # --- Office Action Reply 并行配置 ---
    OAR_PARSE_MAX_CONCURRENCY = int(os.getenv("OAR_PARSE_MAX_CONCURRENCY", "5"))
    OAR_PATENT_RETRIEVAL_MAX_CONCURRENCY = int(os.getenv("OAR_PATENT_RETRIEVAL_MAX_CONCURRENCY", "5"))
    OAR_WORKFLOW_TIMEOUT_SECONDS = int(os.getenv("OAR_WORKFLOW_TIMEOUT_SECONDS", "1800"))

    # --- 外部检索/下载超时 ---
    RETRIEVAL_REQUEST_TIMEOUT_SECONDS = int(os.getenv("RETRIEVAL_REQUEST_TIMEOUT_SECONDS", "30"))
    DOWNLOAD_REQUEST_TIMEOUT_SECONDS = int(os.getenv("DOWNLOAD_REQUEST_TIMEOUT_SECONDS", "60"))
    
    # --- PaddleOCR ---
    OCR_API_KEY = os.getenv("OCR_API_KEY", "")  
    OCR_BASE_URL = os.getenv("OCR_BASE_URL", "https://j9dd7babo5tcocz9.aistudio-app.com/ocr")

    # --- Authing ---
    AUTHING_APP_ID = os.getenv("AUTHING_APP_ID", "").strip()
    AUTHING_APP_SECRET = os.getenv("AUTHING_APP_SECRET", "").strip()
    AUTHING_DOMAIN = os.getenv("AUTHING_DOMAIN", "").strip()


settings = Settings()

# 自动创建基础目录
settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
settings.ASSETS_DIR.mkdir(parents=True, exist_ok=True)
