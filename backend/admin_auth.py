"""
Admin authorization helpers.
"""

from __future__ import annotations

import os
import re
from typing import Any, Set

from fastapi import HTTPException

from backend.storage import get_pipeline_manager


ADMIN_ROLE_ENV_KEY = "AUTHING_ADMIN_ROLE_NAME"
DEFAULT_ADMIN_ROLE = "admin"
task_manager = get_pipeline_manager()


def _normalize_role(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text


def _parse_role_tokens(text: str) -> Set[str]:
    tokens = [item.strip().lower() for item in re.split(r"[\s,|;/]+", text or "") if item.strip()]
    return {item for item in tokens if item}


def _collect_role_values(value: Any, output: Set[str]):
    if isinstance(value, str):
        output.update(_parse_role_tokens(value))
        return
    if isinstance(value, list):
        for item in value:
            _collect_role_values(item, output)
        return
    if not isinstance(value, dict):
        return

    for key, item in value.items():
        lower_key = str(key or "").strip().lower()
        if lower_key in {"code", "name", "value", "slug", "id"} and isinstance(item, str):
            output.update(_parse_role_tokens(item))
        if "role" in lower_key:
            _collect_role_values(item, output)
        elif isinstance(item, (list, dict)):
            _collect_role_values(item, output)


def _extract_roles_from_raw_profile(raw_profile: Any) -> Set[str]:
    if not isinstance(raw_profile, dict):
        return set()

    candidates = []
    for key, value in raw_profile.items():
        if "role" in str(key or "").strip().lower():
            candidates.append(value)
        elif isinstance(value, dict):
            for child_key, child_value in value.items():
                if "role" in str(child_key or "").strip().lower():
                    candidates.append(child_value)

    roles: Set[str] = set()
    for candidate in candidates:
        _collect_role_values(candidate, roles)
    return {role for role in roles if role}


def get_admin_role_name() -> str:
    role_name = _normalize_role(os.getenv(ADMIN_ROLE_ENV_KEY, DEFAULT_ADMIN_ROLE))
    return role_name or DEFAULT_ADMIN_ROLE


def is_admin_owner(owner_id: str) -> bool:
    owner_text = str(owner_id or "").strip()
    if not owner_text.startswith("authing:"):
        return False

    user = task_manager.storage.get_user_by_owner_id(owner_text)
    if not user:
        return False

    roles = _extract_roles_from_raw_profile(getattr(user, "raw_profile", {}) or {})
    required_role = get_admin_role_name()
    return required_role in roles


def ensure_admin_owner(owner_id: str):
    if not is_admin_owner(owner_id):
        raise HTTPException(status_code=403, detail="仅管理员可访问。")
