import os
from pathlib import Path
from dotenv import load_dotenv

# 项目版本号
VERSION = "1.0.0"

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

    # --- 核心 LLM 配置 (生成/推理) ---
    LLM_API_KEY = os.getenv("LLM_API_KEY")
    LLM_BASE_URL = os.getenv("LLM_BASE_URL")
    LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
    LLM_MODEL_REASONING = os.getenv("LLM_MODEL_REASONING", "deepseek-reasoner")

    # --- 视觉模型配置 ---
    VLM_API_KEY = os.getenv("VLM_API_KEY")
    VLM_BASE_URL = os.getenv("VLM_BASE_URL")
    VLM_MODEL = os.getenv("VLM_MODEL", "glm-4.6v")
    VLM_MODEL_MINI = os.getenv("VLM_MODEL_MINI", "").strip()

    # --- 视觉处理配置 ---
    LABEL_COLOR = (0, 0, 255)

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
    
    # --- PaddleOCR ---
    OCR_API_KEY = os.getenv("OCR_API_KEY", "")  
    OCR_BASE_URL = os.getenv("OCR_BASE_URL", "https://j9dd7babo5tcocz9.aistudio-app.com/ocr")

    # --- Retrieval (Qwen + Vector Store) ---
    QWEN_API_KEY = os.getenv("QWEN_API_KEY", os.getenv("DASHSCOPE_API_KEY", ""))
    QWEN_EMBEDDING_MODEL = os.getenv("QWEN_EMBEDDING_MODEL", "qwen3-vl-embedding")
    QWEN_TEXT_RERANK_MODEL = os.getenv("QWEN_TEXT_RERANK_MODEL", "qwen3-reranker")
    QWEN_VL_RERANK_MODEL = os.getenv("QWEN_VL_RERANK_MODEL", "qwen3-vl-reranker")
    RETRIEVAL_ALLOW_FAKE_MODEL = os.getenv("RETRIEVAL_ALLOW_FAKE_MODEL", "true").strip().lower() == "true"
    RETRIEVAL_MILVUS_URI = os.getenv("RETRIEVAL_MILVUS_URI", str(DATA_DIR / "retrieval_milvus.db"))
    RETRIEVAL_SESSION_TTL_MINUTES = int(os.getenv("RETRIEVAL_SESSION_TTL_MINUTES", "60"))

    def get_project_paths(self, workspace_id: str, artifact_name: str = ""):
        """
        根据工作区标识生成标准化路径。
        - workspace_id: 工作目录名（建议 task_id）
        - artifact_name: 产物命名用标识（如专利号），为空则使用 workspace_id
        """
        safe_workspace_id = "".join([c for c in workspace_id if c.isalnum() or c in ("-", "_")])
        safe_artifact_name = "".join([c for c in (artifact_name or workspace_id) if c.isalnum() or c in ("-", "_")])

        project_root = self.OUTPUT_DIR / safe_workspace_id
        mineru_output_dir = project_root / self.MINERU_TEMP_FOLDER

        return {
            "root": project_root,
            "mineru_dir": mineru_output_dir,
            "annotated_dir": project_root / "annotated_images",

            # 输入/中间文件
            "raw_pdf": project_root / "raw.pdf",
            "raw_md": mineru_output_dir / "raw.md",
            "raw_images_dir": mineru_output_dir / "images",

            # 结构化数据
            "patent_json": project_root / "patent.json",  # 专利数据
            "parts_json": project_root / "parts.json",  # 部件数据
            "image_parts_json": project_root / "image_parts.json",  # 图片部件数据
            "check_json": project_root / "check.json", # 专利形式检查
            "report_json": project_root / "report.json",  # 专利分析报告数据
            "search_strategy_json": project_root / "search_strategy.json",  # 检索策略数据

            # 最终产物
            "final_md": project_root / f"{safe_artifact_name}.md",
            "final_pdf": project_root / f"{safe_artifact_name}.pdf",
        }


settings = Settings()

# 自动创建基础目录
settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
settings.ASSETS_DIR.mkdir(parents=True, exist_ok=True)
