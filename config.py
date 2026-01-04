import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 环境变量
load_dotenv()

class Settings:
    # --- 基础路径配置 ---
    BASE_DIR = Path(__file__).resolve().parent
    INPUT_DIR = BASE_DIR / "input"
    OUTPUT_DIR = BASE_DIR / "output"
    ASSETS_DIR = BASE_DIR / "assets"
    
    # 确保字体文件路径 (请手动放入 simhei.ttf 到 assets 目录)
    FONT_PATH = ASSETS_DIR / "simhei.ttf"

    # --- LLM 配置 ---
    LLM_API_KEY = os.getenv("LLM_API_KEY")
    LLM_BASE_URL = os.getenv("LLM_BASE_URL")
    LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")
    LLM_TEMPERATURE = 0.1

    # --- 视觉处理配置 ---
    MAX_WORKERS = 4      # 图像处理并行线程数
    FONT_SIZE = 20       # 标注字体大小
    LABEL_COLOR = (0, 0, 255) # 标注颜色 (B, G, R) - 蓝色

    # Mineru 配置
    MINERU_TEMP_FOLDER = "mineru_raw"

    def get_project_paths(self, pdf_filename_stem: str):
        """
        根据 PDF文件名 生成该项目的特定路径结构
        """
        project_root = self.OUTPUT_DIR / pdf_filename_stem
        mineru_output_dir = project_root / self.MINERU_TEMP_FOLDER
        return {
            "root": project_root,
            "mineru_dir": mineru_output_dir,
            "raw_md": mineru_output_dir / f"{pdf_filename_stem}.md",
            "raw_images_dir": mineru_output_dir / "images",
            "annotated_dir": project_root / "annotated_images",
            "parts_json": project_root / "parts.json",
            "final_md": project_root / f"{pdf_filename_stem}_refined.md"
        }

settings = Settings()

# 自动创建基础目录
settings.INPUT_DIR.mkdir(exist_ok=True)
settings.OUTPUT_DIR.mkdir(exist_ok=True)
settings.ASSETS_DIR.mkdir(exist_ok=True)