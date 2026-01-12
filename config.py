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
    LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
    LLM_MODEL_REASONING = os.getenv("LLM_MODEL_REASONING", "deepseek-reasoner")

    # --- 视觉模型配置 ---
    VLM_API_KEY = os.getenv("VLM_API_KEY")
    VLM_BASE_URL = os.getenv("VLM_BASE_URL")
    VLM_MODEL = os.getenv("VLM_MODEL", "glm-4.6v")

    # --- 视觉处理配置 ---
    FONT_SIZE = 16       # 标注字体大小
    LABEL_COLOR = (0, 0, 255) # 标注颜色 (B, G, R) - 蓝色

    # Mineru 配置
    MINERU_TEMP_FOLDER = "mineru_raw"
    
    PDF_CSS = """    
    @page { 
        size: A4;
        margin: 2cm 1.5cm; 
    }
    
    body {
        font-family: "Arial", "SimHei", "STHeiti", "Microsoft YaHei", sans-serif !important; 
        font-size: 14px; 
        line-height: 1.6; 
        color: #333;
        -webkit-print-color-adjust: exact; 
        print-color-adjust: exact;
    }
    
    /* --- 1. 分页控制 --- */
    h1, h2, h3, h4 { 
        page-break-after: avoid; 
        break-after: avoid; 
    }
    
    .page-break { page-break-before: always; }
    
    .no-break, figure, blockquote, pre, tr {
        page-break-inside: avoid;
        break-inside: avoid;
    }

    /* --- 2. 基础元素 --- */
    h1 {
        text-align: center;
        color: #2c3e50;
        padding-bottom: 20px;
        border-bottom: 3px solid #3498db;
        margin-bottom: 30px;
    }

    h2 { 
        border-bottom: 2px solid #eee; 
        padding-bottom: 8px; 
        margin-top: 30px;
        margin-bottom: 15px;
        color: #2c3e50; 
        font-size: 18px;
    }
    
    h3 { 
        margin-top: 25px; 
        margin-bottom: 10px;
        border-left: 4px solid #3498db; 
        padding-left: 10px;
        color: #34495e;
        font-size: 16px;
    }
    
    p {
        margin-bottom: 10px;
        text-align: justify; 
    }

    ul, ol {
        padding-left: 20px;
        margin-bottom: 15px;
    }
    li { margin-bottom: 6px; }

    /* --- 3. 表格 --- */
    table {
        width: 100%;
        border-collapse: collapse;
        margin-bottom: 20px;
        font-size: 12px; 
        table-layout: auto;
    }
    
    th, td { 
        border: 1px solid #dfe2e5; 
        padding: 6px 8px;
        text-align: left; 
        vertical-align: top;
        word-break: break-word; 
        overflow-wrap: break-word;
    }
    
    th { 
        background-color: #f2f6f9; 
        color: #2c3e50; 
        font-weight: bold; 
        white-space: nowrap;
    }

    /* --- 4. 图片 --- */
    figure {
        margin: 20px auto;
        text-align: center;
        display: block;
    }
    
    img { 
        max-width: 95%; 
        max-height: 400px; 
        object-fit: contain; 
        border: 1px solid #e1e4e8; 
        border-radius: 4px;
        padding: 4px;
        background-color: #fff;
    }
    
    figcaption {
        margin-top: 8px;
        font-size: 12px;
        color: #7f8c8d;
        font-weight: bold;
    }
    
    blockquote {
        border-left: 4px solid #3498db;
        background-color: #f8f9fa;
        margin: 15px 0;
        padding: 10px 15px;
        color: #555;
        font-style: italic;
    }

    /* --- 5. 代码块 (用于检索式) --- */
    pre {
        background-color: #f0f4f8;
        border: 1px solid #d1d9e6;
        border-radius: 4px;
        padding: 10px;
        margin: 10px 0;
        white-space: pre-wrap; 
        word-wrap: break-word;
        font-family: "Consolas", "Monaco", "Courier New", monospace;
        font-size: 12px;
        color: #24292e;
    }

    /* 行内代码 */
    code {
        font-family: "Consolas", "Monaco", "Courier New", monospace;
        font-size: 12px;
        color: #2c3e50; 
        background-color: #f0f4f8; 
        padding: 2px 4px;
        border-radius: 3px;
    }
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
            "patent_json": project_root / "patent.json",  # 专利数据
            "parts_json": project_root / "parts.json",  # 部件数据
            "image_parts_json": project_root / "image_parts.json", # 图片部件数据
            "report_json": project_root / "report.json",  # 专利分析报告数据
            "search_strategy_json": project_root / "search_strategy.json", # 检索策略数据
            "final_md": project_root / f"{pdf_filename_stem}.md",
            "final_pdf": project_root / f"{pdf_filename_stem}.pdf"
        }

settings = Settings()

# 自动创建基础目录
settings.INPUT_DIR.mkdir(exist_ok=True)
settings.OUTPUT_DIR.mkdir(exist_ok=True)
settings.ASSETS_DIR.mkdir(exist_ok=True)
