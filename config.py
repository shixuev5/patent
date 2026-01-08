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
        margin: 2cm; 
    }
    
    body {
        /* 增加 Arial 优化英文显示，SimHei 负责中文 */
        font-family: "Arial", "SimHei", "STHeiti", "Microsoft YaHei", sans-serif !important; 
        font-size: 14px; 
        line-height: 1.6; 
        color: #333;
        -webkit-print-color-adjust: exact; /* 强制打印背景色 */
        print-color-adjust: exact;
    }
    
    /* --- 1. 分页控制 (关键) --- */
    
    /* 标题防孤立 */
    h1, h2, h3, h4 { 
        page-break-after: avoid; 
        break-after: avoid; 
    }
    
    /* 强制分页类 (用于分割不同报告章节) */
    .page-break {
        page-break-before: always;
    }
    
    /* 防截断通用类 */
    .no-break, figure, blockquote, .strategy-step {
        page-break-inside: avoid;
        break-inside: avoid;
    }

    /* --- 2. 基础元素样式 --- */
    
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
        margin-bottom: 12px;
        border-left: 4px solid #3498db; 
        padding-left: 10px;
        color: #34495e;
        font-size: 16px;
    }
    
    p {
        margin-bottom: 10px;
        text-align: justify; /* 两端对齐更像正式文档 */
    }

    /* 列表优化 */
    ul, ol {
        padding-left: 20px;
        margin-bottom: 15px;
    }
    li {
        margin-bottom: 6px;
    }

    /* --- 3. 组件样式 --- */

    /* 表格：防截断，更紧凑的边框 */
    table {
        width: 100%;
        border-collapse: collapse;
        margin-bottom: 20px;
        page-break-inside: auto; 
        font-size: 13px; /* 稍微调小表格字号 */
    }
    
    tr {
        page-break-inside: avoid;
    }
    
    th, td { 
        border: 1px solid #dfe2e5; 
        padding: 8px 12px; 
        text-align: left; 
    }
    
    th { 
        background-color: #f2f6f9; 
        color: #2c3e50; 
        font-weight: bold; 
    }

    /* 图片：限制大小，居中，边框 */
    figure {
        margin: 20px auto;
        text-align: center;
        display: block;
    }
    
    img { 
        max-width: 95%; 
        max-height: 400px; /* 稍微放宽高度限制 */
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
    
    /* 引用块 (用于证据、审查员提示) */
    blockquote {
        border-left: 4px solid #3498db;
        background-color: #f8f9fa;
        margin: 15px 0;
        padding: 10px 15px;
        color: #555;
        font-style: italic;
    }

    /* --- 4. 代码块样式 --- */
    
    /* 外层容器 */
    pre {
        background-color: #f6f8fa;
        border: 1px solid #e1e4e8;
        border-radius: 6px;
        padding: 12px;
        margin: 15px 0;
        overflow-x: hidden; /* 防止超出打印纸张宽度 */
        white-space: pre-wrap; /* 核心：允许长代码自动换行，防止打印截断 */
        word-wrap: break-word;
    }

    /* 代码文字 */
    code {
        font-family: "Consolas", "Monaco", "Courier New", monospace; /* 等宽字体 */
        font-size: 12px;
        color: #24292e;
        background-color: transparent; /* 避免内联代码和块级代码背景冲突 */
    }
    
    /* 内联代码 (如文中提到的 `AND` 算符) */
    p code, li code, td code {
        background-color: #f0f0f0;
        padding: 2px 4px;
        border-radius: 3px;
        color: #e83e8c; /* 醒目的粉红色 */
    }

    /* --- 5. 检索策略专用样式 (可选优化) --- */
    
    .strategy-meta {
        background-color: #fff;
        border: 1px solid #eee;
        padding: 15px;
        border-radius: 4px;
        margin-bottom: 20px;
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
