import os
import shutil
from pathlib import Path
from loguru import logger
from docx import Document
from PIL import Image
import io

# Import config
from config import settings
from agents.common.parsers.base import BaseParser


class LocalWordParser(BaseParser):
    """
    Executes Word document parsing using python-docx library (local parsing).
    """
    def parse(self, docx_path: Path, output_dir: Path) -> Path:
        docx_name = docx_path.stem
        logger.info(f"[LocalWordParser] Starting to parse: {docx_path}")

        try:
            # 1. Prepare environment
            local_md_dir = output_dir
            local_image_dir = output_dir / "images"
            local_md_dir.mkdir(parents=True, exist_ok=True)
            local_image_dir.mkdir(parents=True, exist_ok=True)

            logger.info(f"[LocalWordParser] Output directory set to: {local_md_dir}")

            # 2. Read Word document
            doc = Document(str(docx_path))

            # 3. Extract content
            md_content = []
            image_index = 1

            for para in doc.paragraphs:
                if para.text.strip():
                    md_content.append(para.text)

            # Extract images
            for rel in doc.part.rels.values():
                if "image" in rel.target_ref:
                    image_data = rel.target_part.blob
                    image_stream = io.BytesIO(image_data)

                    try:
                        # Try to identify image format
                        with Image.open(image_stream) as img:
                            img_format = img.format.lower()
                            image_filename = f"image_{image_index}.{img_format}"
                            image_path = local_image_dir / image_filename

                            with open(image_path, "wb") as f:
                                f.write(image_data)

                            md_content.append(f"![Figure {image_index}](images/{image_filename})")
                            image_index += 1
                    except Exception as e:
                        logger.warning(f"[LocalWordParser] Failed to process image: {e}")
                        continue

            # 4. Save Markdown
            md_content_str = "\n\n".join(md_content)
            md_file_name = "raw.md"
            md_file_path = local_md_dir / md_file_name

            with open(md_file_path, "w", encoding="utf-8") as f:
                f.write(md_content_str)

            final_md_path = Path(local_md_dir) / md_file_name

            if not final_md_path.exists():
                logger.error(f"[LocalWordParser] File check failed: {final_md_path} does not exist.")
            else:
                logger.success(f"[LocalWordParser] Success. MD saved at: {final_md_path}")

            return final_md_path

        except Exception as e:
            logger.exception(f"[LocalWordParser] Failed to parse Word document: {e}")
            raise e


class WordParser(BaseParser):
    """
    Factory class for Word document parsing (only local parsing supported).
    """
    def parse(self, docx_path: Path, output_dir: Path) -> Path:
        logger.info("Using Local Word Parser (python-docx)")
        return LocalWordParser().parse(docx_path, output_dir)
