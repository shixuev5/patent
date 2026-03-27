import shutil
import subprocess
import time
import zipfile
from pathlib import Path

from loguru import logger

try:
    import pypandoc
except ImportError:  # pragma: no cover - exercised via fallback paths in tests
    pypandoc = None

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


class LocalWordParser(BaseParser):
    """Parse Word files locally via pandoc, using LibreOffice as a .doc pre-converter."""

    _SOFFICE_CANDIDATES = (
        "soffice",
        "/usr/bin/soffice",
        "/usr/local/bin/soffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    )

    def parse(self, file_path: Path, output_dir: Path) -> Path:
        suffix = file_path.suffix.lower()
        if suffix not in {".doc", ".docx"}:
            raise ValueError(f"LocalWordParser does not support: {suffix}")

        output_dir.mkdir(parents=True, exist_ok=True)
        target_md = output_dir / "raw.md"
        media_dir = output_dir / "images"
        media_dir.mkdir(parents=True, exist_ok=True)

        source_path = file_path
        if suffix == ".doc":
            logger.info(f"[本地Word解析器] 先将 DOC 转换为 DOCX：{file_path}")
            source_path = self._convert_doc_to_docx(file_path, output_dir)
        else:
            logger.info(f"[本地Word解析器] 开始本地解析 DOCX：{file_path}")

        self._convert_docx_to_markdown(source_path, target_md, media_dir)
        if not target_md.exists() or target_md.stat().st_size == 0:
            raise RuntimeError(f"Pandoc conversion did not produce markdown: {target_md}")

        logger.success(f"[本地Word解析器] 解析成功，MD 已保存至：{target_md}")
        return target_md

    def _convert_doc_to_docx(self, doc_path: Path, output_dir: Path) -> Path:
        soffice_binary = self._resolve_soffice_binary()
        conversion_dir = output_dir / "libreoffice_tmp"
        conversion_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            soffice_binary,
            "--headless",
            "--convert-to",
            "docx",
            "--outdir",
            str(conversion_dir),
            str(doc_path),
        ]
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            message = stderr or stdout or f"exit code {result.returncode}"
            raise RuntimeError(f"LibreOffice failed to convert .doc to .docx: {message}")

        converted_path = conversion_dir / f"{doc_path.stem}.docx"
        if not converted_path.exists():
            raise FileNotFoundError(f"LibreOffice did not create expected DOCX: {converted_path}")
        return converted_path

    def _convert_docx_to_markdown(self, source_path: Path, target_md: Path, media_dir: Path) -> None:
        if pypandoc is None:
            raise RuntimeError("pypandoc is not installed")

        pypandoc.convert_file(
            str(source_path),
            to="gfm",
            format="docx",
            outputfile=str(target_md),
            extra_args=[f"--extract-media={media_dir}"],
        )

    def _resolve_soffice_binary(self) -> str:
        for candidate in self._SOFFICE_CANDIDATES:
            resolved = shutil.which(candidate) if "/" not in candidate else candidate
            if resolved and Path(resolved).exists():
                return str(resolved)
        raise FileNotFoundError("LibreOffice soffice binary not found")


class WordParser(BaseParser):
    """
    Factory class that prefers local parsing and falls back to the online parser.
    """
    @staticmethod
    def parse(file_path: Path, output_dir: Path) -> Path:
        try:
            logger.info("优先使用本地 Word 解析器（pandoc / LibreOffice）")
            return LocalWordParser().parse(file_path, output_dir)
        except Exception as exc:
            logger.warning(f"本地 Word 解析失败，回退在线解析：{exc}")
            logger.info("使用在线 Word 解析器（Mineru 接口）")
            return OnlineWordParser().parse(file_path, output_dir)
