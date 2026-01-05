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
    
    # PDF 生成时的 CSS 样式 (控制 A4 大小和图片自适应)
    PDF_CSS = """
    @page {{ size: A4; margin: 2cm; }}
    body {{ 
        font-family: "SimHei", "STHeiti", "Microsoft YaHei", sans-serif; 
        font-size: 14px; line-height: 1.6; 
    }}
    h1 {{ text-align: center; color: #333; }}
    h2 {{ border-bottom: 2px solid #eee; padding-bottom: 10px; margin-top: 20px; color: #444; }}
    img {{ max-width: 60%; height: auto; display: block; margin: 10px auto; border: 1px solid #ddd; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 12px; }}
    th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; }}
    th {{ background-color: #f4f4f4; }}
    """

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
            "final_md": project_root / f"{pdf_filename_stem}.md",
            "final_pdf": project_root / f"{pdf_filename_stem}.pdf"
        }

settings = Settings()

# 自动创建基础目录
settings.INPUT_DIR.mkdir(exist_ok=True)
settings.OUTPUT_DIR.mkdir(exist_ok=True)
settings.ASSETS_DIR.mkdir(exist_ok=True)