from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

from agents.common.workflow.path_utils import (
    PipelineCancelled,
    get_node_cache_file,
    item_get,
    read_json,
    refresh_output_artifact_paths,
    resolve_pn,
    safe_artifact_name,
    write_json,
)
from config import settings


def _build_paths_from_output_dir(output_dir: Path, artifact_name: str) -> Dict[str, Path]:
    mineru_dir = output_dir / settings.MINERU_TEMP_FOLDER
    safe_artifact = safe_artifact_name(artifact_name or output_dir.name)
    return {
        "root": output_dir,
        "mineru_dir": mineru_dir,
        "annotated_dir": output_dir / "annotated_images",
        "raw_pdf": output_dir / "raw.pdf",
        "raw_md": mineru_dir / "raw.md",
        "raw_images_dir": mineru_dir / "images",
        "patent_json": output_dir / "patent.json",
        "parts_json": output_dir / "parts.json",
        "image_parts_json": output_dir / "image_parts.json",
        "image_labels_json": output_dir / "image_labels.json",
        "final_md": output_dir / f"{safe_artifact}.md",
        "final_pdf": output_dir / f"{safe_artifact}.pdf",
    }


def ensure_pipeline_paths(state: Any) -> Tuple[Dict[str, str], Dict[str, Path]]:
    raw_paths = item_get(state, "paths", {}) or {}
    if raw_paths:
        path_dict = {k: Path(v) for k, v in raw_paths.items()}
    else:
        pn = str(item_get(state, "pn", "") or "").strip()
        task_id = str(item_get(state, "task_id", "") or "").strip()
        output_dir = str(item_get(state, "output_dir", "") or "").strip()

        workspace_id = safe_artifact_name(task_id or pn or "task") or "task"
        artifact_name = safe_artifact_name(pn or workspace_id) or workspace_id

        if output_dir:
            path_dict = _build_paths_from_output_dir(Path(output_dir), artifact_name)
        else:
            path_dict = _build_paths_from_output_dir(settings.OUTPUT_DIR / workspace_id, artifact_name)

    path_dict["root"].mkdir(parents=True, exist_ok=True)
    path_dict["annotated_dir"].mkdir(parents=True, exist_ok=True)

    str_paths = {k: str(v) for k, v in path_dict.items()}
    return str_paths, path_dict
