import os
import time
import zipfile
import requests
import shutil
from pathlib import Path
from loguru import logger

# Import config
from config import settings

# Import Mineru local backend (only used if local parsing is active)
try:
    from mineru.cli.common import read_fn
    from mineru.backend.hybrid.hybrid_analyze import doc_analyze as hybrid_doc_analyze
    from mineru.backend.vlm.vlm_middle_json_mkcontent import union_make as vlm_union_make
    from mineru.data.data_reader_writer import FileBasedDataWriter
    from mineru.utils.enum_class import MakeMode
    from mineru.utils.engine_utils import get_vlm_engine
except ImportError:
    # Allow running online-only without heavy local dependencies
    pass


class LocalPDFParser:
    """
    Executes PDF parsing using the local Mineru Python library (requires GPU/heavy CPU).
    """
    @staticmethod
    def parse(pdf_path: Path, output_dir: Path) -> Path:
        pdf_name = pdf_path.stem
        logger.info(f"[LocalParser] Starting to parse: {pdf_path}")

        try:
            # 1. Read file
            pdf_bytes = read_fn(str(pdf_path))

            # 2. Prepare environment
            local_md_dir = output_dir
            local_image_dir = output_dir / "images"
            local_md_dir.mkdir(parents=True, exist_ok=True)
            local_image_dir.mkdir(parents=True, exist_ok=True)

            logger.info(f"[LocalParser] Output directory set to: {local_md_dir}")

            image_writer = FileBasedDataWriter(str(local_image_dir))
            md_writer = FileBasedDataWriter(str(local_md_dir))

            # 3. Setup backend
            backend = get_vlm_engine(inference_engine="auto", is_async=False)
            parse_method = "hybrid_auto"

            # 4. Hybrid Analyze
            middle_json, infer_result, _vlm_ocr_enable = hybrid_doc_analyze(
                pdf_bytes,
                image_writer=image_writer,
                backend=backend,
                parse_method=parse_method,
                language="ch",
                inline_formula_enable=True,
                server_url=None,
            )

            pdf_info = middle_json["pdf_info"]

            # 5. Generate Markdown
            img_rel_dir = "images"  # Relative path for MD
            md_content = vlm_union_make(pdf_info, MakeMode.MM_MD, img_rel_dir)

            md_file_name = f"raw.md" # Standardize name to raw.md as expected by pipeline
            md_writer.write_string(md_file_name, md_content)

            final_md_path = Path(local_md_dir) / md_file_name

            if not final_md_path.exists():
                logger.error(f"[LocalParser] File check failed: {final_md_path} does not exist.")
            else:
                logger.success(f"[LocalParser] Success. MD saved at: {final_md_path}")

            return final_md_path

        except Exception as e:
            logger.exception(f"[LocalParser] Failed to parse PDF: {e}")
            raise e


class OnlinePDFParser:
    """
    Executes PDF parsing using the Mineru.net REST API.
    Flow: Get Upload URL -> Upload File -> Create Task -> Poll Status -> Download Result
    """
    def __init__(self):
        self.api_key = settings.MINERU_API_KEY
        self.base_url = settings.MINERU_BASE_URL.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    def parse(self, pdf_path: Path, output_dir: Path) -> Path:
        logger.info(f"[OnlineParser] Starting online parsing for: {pdf_path}")
        
        if not self.api_key:
            raise ValueError("MINERU_API_KEY is missing in config.")

        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            # 1. Upload File (Get Data ID)
            batch_id = self._upload_file(pdf_path)
            logger.info(f"[OnlineParser] File uploaded. Batch ID: {batch_id}")

            # 2. Poll for Completion
            result_data = self._poll_task(batch_id)
            
            # 3. Process Results (Download and unzip)
            final_md_path = self._process_results(result_data, output_dir)
            
            logger.success(f"[OnlineParser] Success. MD saved at: {final_md_path}")
            return final_md_path

        except Exception as e:
            logger.exception(f"[OnlineParser] API Processing failed: {e}")
            raise e

    def _upload_file(self, file_path: Path) -> str:
        """
        Uploads a local file.
        Strategy: Request a presigned URL batch, then PUT the file.
        """
        file_name = file_path.name
        
        # A. Request Upload URL
        url_batch = f"{self.base_url}/file-urls/batch"
        payload = {"files": [{"name": file_name}], "model_version": "vlm"}
        
        resp = requests.post(url_batch, headers=self.headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        
        if data.get("code") != 0:
            raise Exception(f"Failed to get upload URL: {data}")

        res_data = data["data"]
        
        if "file_urls" not in res_data or not res_data["file_urls"]:
             raise Exception(f"Invalid API response: 'file_urls' missing. Data: {res_data}")
         
        upload_url = res_data["file_urls"][0]
        batch_id = res_data["batch_id"]

        # B. Upload Content (PUT)
        with open(file_path, "rb") as f:
            put_resp = requests.put(upload_url, data=f)
            
            if put_resp.status_code != 200:
                logger.error(f"OSS Upload Failed [{put_resp.status_code}]: {put_resp.text}")
                
            put_resp.raise_for_status()
            
        return batch_id

    def _poll_task(self, batch_id: str, interval=5, timeout=600) -> dict:
        """Polls the task status until success or failure."""
        url = f"{self.base_url}/extract-results/batch/{batch_id}"
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            resp = requests.get(url, headers=self.headers)
            if resp.status_code != 200:
                logger.warning(f"[OnlineParser] Poll status check failed: {resp.status_code}")
                time.sleep(interval)
                continue
                
            data = resp.json()
            if data.get("code") != 0:
                raise Exception(f"Poll API Error: {data}")
            
            # The API returns a list of extracts for the batch. We uploaded one file.
            task_info = data["data"]["extract_result"][0]
            state = task_info["state"]
            
            if state == "done":
                return task_info
            elif state == "failed":
                raise Exception(f"Task failed on server: {task_info.get('err_msg')}")
            else:
                logger.info(f"[OnlineParser] Status: {state}... waiting {interval}s")
                time.sleep(interval)
        
        raise TimeoutError("Extraction task timed out.")

    def _process_results(self, task_info: dict, output_dir: Path) -> Path:
        """Downloads the zip, extracts it, and arranges files."""
        # 1. Download
        download_url = task_info.get("full_zip_url") 

        if not download_url:
            raise Exception("No download URL found in task response.")

        zip_path = output_dir / "result.zip"
        
        with requests.get(download_url, stream=True) as r:
            r.raise_for_status()
            with open(zip_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

        # 2. Extract
        extract_tmp = output_dir / "tmp_extract"
        extract_tmp.mkdir(exist_ok=True)
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(extract_tmp)

        # 3. Organize (Move files to expected structure)
        # Expected: output_dir/raw.md, output_dir/images/
        
        # Find the markdown file in the extracted folders
        md_files = list(extract_tmp.rglob("*.md"))
        if not md_files:
            raise FileNotFoundError("No markdown file found in the downloaded zip.")
        
        source_md = md_files[0]
        target_md = output_dir / "raw.md"
        shutil.move(str(source_md), str(target_md))
        
        # Find images folder (usually in the same dir as the md file)
        source_img_dir = source_md.parent / "images"
        target_img_dir = output_dir / "images"
        
        if source_img_dir.exists():
            if target_img_dir.exists():
                shutil.rmtree(target_img_dir)
            shutil.move(str(source_img_dir), str(target_img_dir))
        else:
            target_img_dir.mkdir(exist_ok=True)

        # Cleanup
        zip_path.unlink()
        shutil.rmtree(extract_tmp)
        
        return target_md


class PDFParser:
    """
    Factory class that routes to Local or Online parser based on configuration.
    """
    @staticmethod
    def parse(pdf_path: Path, output_dir: Path) -> Path:
        pdf_parser = os.getenv("PDF_PARSER", "local").lower()
        if pdf_parser == 'online':
            logger.info("Using Online PDF Parser (Mineru API)")
            return OnlinePDFParser().parse(pdf_path, output_dir)
        else:
            logger.info("Using Local PDF Parser (Mineru Python Lib)")
            return LocalPDFParser.parse(pdf_path, output_dir)
