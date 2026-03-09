from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Tuple

from config import settings


class PipelineCancelled(RuntimeError):
    pass


def item_get(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def safe_artifact_name(value: str) -> str:
    return "".join([c for c in str(value or "") if c.isalnum() or c in ("-", "_")])


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
        "check_json": output_dir / "check.json",
        "report_core_json": output_dir / "report_core.json",
        "report_json": output_dir / "report.json",
        "search_strategy_json": output_dir / "search_strategy.json",
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
            path_dict = settings.get_project_paths(workspace_id=workspace_id, artifact_name=artifact_name)

    path_dict["root"].mkdir(parents=True, exist_ok=True)
    path_dict["annotated_dir"].mkdir(parents=True, exist_ok=True)

    str_paths = {k: str(v) for k, v in path_dict.items()}
    return str_paths, path_dict


def resolve_pn(input_pn: str, patent_data: Dict[str, Any], fallback: str) -> str:
    input_value = safe_artifact_name(str(input_pn or "").strip())
    if input_value:
        return input_value

    biblio = patent_data.get("bibliographic_data", {}) if isinstance(patent_data, dict) else {}
    publication_number = safe_artifact_name(str(biblio.get("publication_number", "")).strip())
    if publication_number:
        return publication_number

    return safe_artifact_name(fallback) or "task"


def refresh_output_artifact_paths(paths: Dict[str, str], resolved_pn: str) -> Dict[str, str]:
    safe_pn = safe_artifact_name(resolved_pn)
    if not safe_pn:
        return paths

    root = Path(paths["root"])
    updated = dict(paths)
    updated["final_md"] = str(root / f"{safe_pn}.md")
    updated["final_pdf"] = str(root / f"{safe_pn}.pdf")
    return updated


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def get_node_cache_file(cache_dir: str, node_name: str) -> Path:
    cache_root = Path(cache_dir)
    cache_root.mkdir(parents=True, exist_ok=True)
    return cache_root / f"{node_name}_cache.json"
