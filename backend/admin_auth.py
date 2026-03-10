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

    roles: Set[str] = set()
    user_role = str(getattr(user, "role", "") or "").strip()
    if user_role:
        roles.update(_parse_role_tokens(user_role))
    required_role = get_admin_role_name()
    return required_role in roles


def ensure_admin_owner(owner_id: str):
    if not is_admin_owner(owner_id):
        raise HTTPException(status_code=403, detail="仅管理员可访问。")
