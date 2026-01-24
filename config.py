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

    # --- 核心 LLM 配置 (生成/推理) ---
    LLM_API_KEY = os.getenv("LLM_API_KEY")
    LLM_BASE_URL = os.getenv("LLM_BASE_URL")
    LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
    LLM_MODEL_REASONING = os.getenv("LLM_MODEL_REASONING", "deepseek-reasoner")

    # --- 专利审查模型配置 (Patent Examination) ---
    LLM_MODEL_EXAM = os.getenv("LLM_MODEL_EXAM", "deepseek-chat")
    LLM_EXAM_API_KEY = os.getenv("LLM_EXAM_API_KEY")  # 可选，若为空则复用 LLM_API_KEY
    LLM_EXAM_BASE_URL = os.getenv(
        "LLM_EXAM_BASE_URL"
    )  # 可选，若为空则复用 LLM_BASE_URL

    # --- 视觉模型配置 ---
    VLM_API_KEY = os.getenv("VLM_API_KEY")
    VLM_BASE_URL = os.getenv("VLM_BASE_URL")
    VLM_MODEL = os.getenv("VLM_MODEL", "glm-4.6v")

    # --- 视觉处理配置 ---
    FONT_SIZE = 20  # 标注字体大小
    LABEL_COLOR = (0, 0, 255)  # 标注颜色 (B, G, R) - 蓝色

    # --- 智慧芽 ---
    ZHIHUIYA_USERNAME = os.getenv("ZHIHUIYA_USERNAME", "")
    ZHIHUIYA_PASSWORD = os.getenv("ZHIHUIYA_PASSWORD", "")
    ZHIHUIYA_CLIENT_ID = os.getenv(
        "ZHIHUIYA_CLIENT_ID", "f58bbdfdd63549dbb64fed4b816c8bfc"
    )

    # --- Google Patents / SerpApi Configuration ---
    SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")

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
    
    /* 强制换行类 */
    .page-break { 
        page-break-before: always; 
        break-before: page;
    }
    
    /* 标题永远不要作为页面的最后元素，也不要被切断 */
    h1, h2, h3, h4, h5, h6 { 
        page-break-after: avoid; 
        break-after: avoid; 
        page-break-inside: avoid;
        break-inside: avoid;
    }
    
    /* 这里的元素作为一个整体，尽量不要内部断开 */
    figure, blockquote, pre, .no-break {
        page-break-inside: avoid;
        break-inside: avoid;
    }

    /* 表格行尝试保持完整，不要把一行字切成两半 */
    tr {
        page-break-inside: avoid;
        break-inside: avoid;
    }

    /* 段落孤行控制：页底至少留2行，页顶至少留2行 */
    p {
        orphans: 2;
        widows: 2;
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

    /* --- 3. 表格 (增强) --- */
    table {
        width: 100%;
        border-collapse: collapse;
        margin-bottom: 20px;
        font-size: 12px; 
        table-layout: auto;
        /* 允许表格整体跨页 */
        page-break-inside: auto;
        break-inside: auto;
    }
    
    /* 关键：表格跨页时，自动在新页面重复表头 */
    thead {
        display: table-header-group;
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
        white-space: nowrap; /* 表头尽量不换行 */
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

    def get_project_paths(self, pn: str):
        """
        根据专利号生成标准化的工作区路径
        结构: output/{pn}/...
        """
        # 清理文件名中的非法字符
        safe_pn = "".join([c for c in pn if c.isalnum() or c in ('-', '_')])

        project_root = self.OUTPUT_DIR / safe_pn
        mineru_output_dir = project_root / self.MINERU_TEMP_FOLDER

        return {
            "root": project_root,
            "mineru_dir": mineru_output_dir,
            "annotated_dir": project_root / "annotated_images",

            # 输入/中间文件
            "raw_pdf": project_root / "raw.pdf",
            "raw_md": mineru_output_dir / f"{safe_pn}.md",
            "raw_images_dir": mineru_output_dir / "images",

            # 结构化数据
            "patent_json": project_root / "patent.json",  # 专利数据
            "parts_json": project_root / "parts.json",  # 部件数据
            "image_parts_json": project_root / "image_parts.json",  # 图片部件数据
            "check_json": project_root / "check.json", # 专利形式检查
            "report_json": project_root / "report.json",  # 专利分析报告数据

            # 搜索与查新
            "search_strategy_json": project_root / "search_strategy.json",  # 检索策略数据
            "examination_results_json": project_root / "examination_results.json",  # 审查结果数据

            # 最终产物
            "final_md": project_root / f"{safe_pn}.md",
            "final_pdf": project_root / f"{safe_pn}.pdf",
        }


settings = Settings()

# 自动创建基础目录
settings.INPUT_DIR.mkdir(exist_ok=True)
settings.OUTPUT_DIR.mkdir(exist_ok=True)
settings.ASSETS_DIR.mkdir(exist_ok=True)
