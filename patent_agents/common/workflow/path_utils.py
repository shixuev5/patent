from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


class PipelineCancelled(RuntimeError):
    pass


def item_get(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def safe_artifact_name(value: str) -> str:
    return "".join([c for c in str(value or "") if c.isalnum() or c in ("-", "_")])


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
