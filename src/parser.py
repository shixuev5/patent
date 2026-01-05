import os
from pathlib import Path
from loguru import logger
from mineru.cli.common import read_fn
from mineru.backend.hybrid.hybrid_analyze import doc_analyze as hybrid_doc_analyze
from mineru.backend.vlm.vlm_middle_json_mkcontent import union_make as vlm_union_make
from mineru.data.data_reader_writer import FileBasedDataWriter
from mineru.utils.enum_class import MakeMode
from mineru.utils.engine_utils import get_vlm_engine

class PDFParser:
    @staticmethod
    def parse(pdf_path: Path, output_dir: Path) -> Path:
        """
        解析 PDF 并输出 Markdown 和 图片。
        :param pdf_path: PDF文件路径
        :param output_dir: 输出根目录 (会在该目录下创建 mineru_raw 文件夹)
        :return: 生成的 Markdown 文件路径
        """
        pdf_name = pdf_path.stem
        
        logger.info(f"[Parser] Starting to parse: {pdf_path}")
        
        try:
            # 1. 读取文件
            pdf_bytes = read_fn(str(pdf_path))
            
            # 2. 准备环境
            local_md_dir = output_dir
            local_image_dir = output_dir / "images"
            
            # 确保目录存在
            local_md_dir.mkdir(parents=True, exist_ok=True)
            local_image_dir.mkdir(parents=True, exist_ok=True)
            
            logger.info(f"[Parser] Output directory set to: {local_md_dir}")
            
            image_writer = FileBasedDataWriter(str(local_image_dir))
            md_writer = FileBasedDataWriter(str(local_md_dir))
            
            # 3. 设置 backend 和 parse_method
            backend = get_vlm_engine(inference_engine='auto', is_async=False)
            parse_method = "hybrid_auto"
            
            # 4. Hybrid 分析
            middle_json = hybrid_doc_analyze(
                pdf_bytes,
                image_writer=image_writer,
                backend=backend,
                parse_method=parse_method,
                language="ch",
                inline_formula_enable=True,
                server_url=None,
            )
            
            pdf_info = middle_json["pdf_info"]
            
            # 5. 生成 Markdown
            # 图片目录相对于 md 文件的路径
            img_rel_dir = os.path.basename(local_image_dir)
            md_content = vlm_union_make(pdf_info, MakeMode.MM_MD, img_rel_dir)
            
            md_file_name = f"{pdf_name}.md"
            md_writer.write_string(md_file_name, md_content)
            
            final_md_path = Path(local_md_dir) / md_file_name

            if not final_md_path.exists():
                logger.error(f"[Parser] File check failed: {final_md_path} does not exist.")
            else:
                logger.success(f"[Parser] Success. MD saved at: {final_md_path}")
                
            return final_md_path

        except Exception as e:
            logger.exception(f"[Parser] Failed to parse PDF: {e}")
            raise e