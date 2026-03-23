import os
import time
import zipfile
import shutil
from pathlib import Path
from loguru import logger

# Import config
from config import settings
from agents.common.parsers.base import BaseParser
from agents.common.utils.http import request_with_retry


class OnlineWordParser(BaseParser):
    """
    Executes Word document parsing using the Mineru.net REST API.
    Flow: Get Upload URL -> Upload File -> Create Task -> Poll Status -> Download Result
    """
    def __init__(self):
        self.api_key = settings.MINERU_API_KEY
        self.base_url = settings.MINERU_BASE_URL.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    def parse(self, docx_path: Path, output_dir: Path) -> Path:
        logger.info(f"[在线Word解析器] 开始在线解析：{docx_path}")

        if not self.api_key:
            raise ValueError("MINERU_API_KEY is missing in config.")

        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            # 1. Upload File (Get Data ID)
            batch_id = self._upload_file(docx_path)
            logger.info(f"[在线Word解析器] 文件上传完成，批次 ID：{batch_id}")

            # 2. Poll for Completion
            result_data = self._poll_task(batch_id)

            # 3. Process Results (Download and unzip)
            final_md_path = self._process_results(result_data, output_dir)

            logger.success(f"[在线Word解析器] 解析成功，MD 已保存至：{final_md_path}")
            return final_md_path

        except Exception as e:
            logger.exception(f"[在线Word解析器] API 处理失败：{e}")
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

        resp = request_with_retry(
            "post",
            url_batch,
            log_prefix="[在线Word解析器]",
            headers=self.headers,
            json=payload,
            timeout=settings.MINERU_REQUEST_TIMEOUT_SECONDS,
        )
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
            put_resp = request_with_retry(
                "put",
                upload_url,
                log_prefix="[在线Word解析器]",
                data=f,
                timeout=settings.MINERU_REQUEST_TIMEOUT_SECONDS,
            )

            if put_resp.status_code != 200:
                logger.error(f"OSS 上传失败 [{put_resp.status_code}]：{put_resp.text}")

            put_resp.raise_for_status()

        return batch_id

    def _poll_task(self, batch_id: str, interval=5, timeout=600) -> dict:
        """Polls the task status until success or failure."""
        url = f"{self.base_url}/extract-results/batch/{batch_id}"
        start_time = time.time()

        while time.time() - start_time < timeout:
            resp = request_with_retry(
                "get",
                url,
                log_prefix="[在线Word解析器]",
                headers=self.headers,
                timeout=settings.MINERU_REQUEST_TIMEOUT_SECONDS,
            )
            if resp.status_code != 200:
                logger.warning(f"[在线Word解析器] 轮询状态检查失败：{resp.status_code}")
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
                logger.info(f"[在线Word解析器] 当前状态：{state}，等待 {interval}s 后重试")
                time.sleep(interval)

        raise TimeoutError("Extraction task timed out.")

    def _process_results(self, task_info: dict, output_dir: Path) -> Path:
        """Downloads the zip, extracts it, and arranges files."""
        # 1. Ensure output directory exists
        output_dir.mkdir(parents=True, exist_ok=True)

        # 2. Download
        download_url = task_info.get("full_zip_url")

        if not download_url:
            raise Exception("No download URL found in task response.")

        zip_path = output_dir / "result.zip"

        with request_with_retry(
            "get",
            download_url,
            log_prefix="[在线Word解析器]",
            stream=True,
            verify=False,
            timeout=settings.MINERU_REQUEST_TIMEOUT_SECONDS,
        ) as r:
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


class WordParser(BaseParser):
    """
    Factory class that routes to Online parser for Word document parsing.
    """
    @staticmethod
    def parse(docx_path: Path, output_dir: Path) -> Path:
        logger.info("使用在线 Word 解析器（Mineru 接口）")
        return OnlineWordParser().parse(docx_path, output_dir)
