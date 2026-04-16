"""User and auth session repository methods."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from backend.time_utils import to_utc_z, utc_now_z
from ..models import RefreshSession, User


class UserRepositoryMixin:
    def upsert_authing_user(self, user: User) -> User:
        now_iso = utc_now_z()
        created_at_iso = to_utc_z(user.created_at, naive_strategy="utc") if user.created_at else now_iso
        raw_profile = json.dumps(user.raw_profile, ensure_ascii=False) if user.raw_profile else None
        self._request(
            """
            INSERT INTO users (
                owner_id, authing_sub, role, name, nickname, email, phone, picture,
                notification_email_enabled, work_notification_email, personal_notification_email,
                raw_profile, created_at, updated_at, last_login_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_id) DO UPDATE SET
                authing_sub = excluded.authing_sub,
                role = CASE WHEN users.role IS NULL OR TRIM(users.role) = '' THEN excluded.role ELSE users.role END,
                name = CASE WHEN users.name IS NULL OR TRIM(users.name) = '' THEN excluded.name ELSE users.name END,
                nickname = CASE WHEN users.nickname IS NULL OR TRIM(users.nickname) = '' THEN excluded.nickname ELSE users.nickname END,
                email = excluded.email,
                phone = excluded.phone,
                picture = CASE WHEN users.picture IS NULL OR TRIM(users.picture) = '' THEN excluded.picture ELSE users.picture END,
                notification_email_enabled = users.notification_email_enabled,
                work_notification_email = CASE WHEN users.work_notification_email IS NULL OR TRIM(users.work_notification_email) = '' THEN excluded.work_notification_email ELSE users.work_notification_email END,
                personal_notification_email = CASE WHEN users.personal_notification_email IS NULL OR TRIM(users.personal_notification_email) = '' THEN excluded.personal_notification_email ELSE users.personal_notification_email END,
                raw_profile = excluded.raw_profile,
                updated_at = excluded.updated_at,
                last_login_at = excluded.last_login_at
            """,
            [
                user.owner_id, user.authing_sub, user.role, user.name, user.nickname, user.email, user.phone, user.picture,
                1 if user.notification_email_enabled else 0, user.work_notification_email, user.personal_notification_email,
                raw_profile, created_at_iso, now_iso, now_iso,
            ],
        )
        row = self._fetchone("SELECT * FROM users WHERE owner_id = ?", [user.owner_id])
        return self._row_to_user(row) if row else user

    def get_user_by_owner_id(self, owner_id: str) -> Optional[User]:
        row = self._fetchone("SELECT * FROM users WHERE owner_id = ?", [owner_id])
        return self._row_to_user(row) if row else None

    def get_user_by_name(self, name: str) -> Optional[User]:
        normalized = str(name or "").strip()
        if not normalized:
            return None
        row = self._fetchone("SELECT * FROM users WHERE name = ?", [normalized])
        return self._row_to_user(row) if row else None

    def upsert_refresh_session(self, session: RefreshSession) -> RefreshSession:
        created_at_iso = to_utc_z(session.created_at, naive_strategy="utc") if session.created_at else utc_now_z()
        updated_at_iso = to_utc_z(session.updated_at, naive_strategy="utc") if session.updated_at else utc_now_z()
        self._request(
            """
            INSERT INTO refresh_sessions (
                token_hash, owner_id, expires_at, created_at, updated_at, revoked_at, replaced_by_token_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(token_hash) DO UPDATE SET
                owner_id = excluded.owner_id,
                expires_at = excluded.expires_at,
                updated_at = excluded.updated_at,
                revoked_at = excluded.revoked_at,
                replaced_by_token_hash = excluded.replaced_by_token_hash
            """,
            [
                session.token_hash,
                session.owner_id,
                to_utc_z(session.expires_at, naive_strategy="utc"),
                created_at_iso,
                updated_at_iso,
                to_utc_z(session.revoked_at, naive_strategy="utc") if session.revoked_at else None,
                session.replaced_by_token_hash,
            ],
        )
        row = self._fetchone("SELECT * FROM refresh_sessions WHERE token_hash = ?", [session.token_hash])
        return self._row_to_refresh_session(row) if row else session

    def get_refresh_session(self, token_hash: str) -> Optional[RefreshSession]:
        normalized = str(token_hash or "").strip()
        if not normalized:
            return None
        row = self._fetchone("SELECT * FROM refresh_sessions WHERE token_hash = ?", [normalized])
        return self._row_to_refresh_session(row) if row else None

    def rotate_refresh_session(self, current_session: RefreshSession, next_session: RefreshSession) -> bool:
        current_token_hash = str(current_session.token_hash or "").strip()
        current_owner_id = str(current_session.owner_id or "").strip()
        next_token_hash = str(next_session.token_hash or "").strip()
        if not current_token_hash or not current_owner_id or not next_token_hash:
            return False
        result = self._request(
            """
            UPDATE refresh_sessions
            SET token_hash = ?, expires_at = ?, created_at = ?, updated_at = ?, revoked_at = NULL, replaced_by_token_hash = NULL
            WHERE token_hash = ? AND owner_id = ? AND revoked_at IS NULL AND expires_at = ?
            """,
            [
                next_token_hash,
                to_utc_z(next_session.expires_at, naive_strategy="utc"),
                to_utc_z(next_session.created_at, naive_strategy="utc"),
                to_utc_z(next_session.updated_at, naive_strategy="utc"),
                current_token_hash,
                current_owner_id,
                to_utc_z(current_session.expires_at, naive_strategy="utc"),
            ],
        )
        return self._changed_rows(result) > 0

    def revoke_refresh_session(self, token_hash: str, replaced_by_token_hash: Optional[str] = None) -> bool:
        normalized = str(token_hash or "").strip()
        if not normalized:
            return False
        now_iso = utc_now_z()
        result = self._request(
            """
            UPDATE refresh_sessions
            SET revoked_at = ?, replaced_by_token_hash = ?, updated_at = ?
            WHERE token_hash = ? AND revoked_at IS NULL
            """,
            [now_iso, replaced_by_token_hash, now_iso, normalized],
        )
        return self._changed_rows(result) > 0

    def revoke_refresh_sessions_by_owner(self, owner_id: str) -> int:
        normalized = str(owner_id or "").strip()
        if not normalized:
            return 0
        now_iso = utc_now_z()
        result = self._request(
            """
            UPDATE refresh_sessions
            SET revoked_at = ?, updated_at = ?
            WHERE owner_id = ? AND revoked_at IS NULL
            """,
            [now_iso, now_iso, normalized],
        )
        return self._changed_rows(result)

    def update_user_profile(self, owner_id: str, name: Optional[str], picture: Optional[str]) -> Optional[User]:
        now_iso = utc_now_z()
        result = self._request("UPDATE users SET name = ?, picture = ?, updated_at = ? WHERE owner_id = ?", [name, picture, now_iso, owner_id])
        if self._changed_rows(result) <= 0:
            return None
        row = self._fetchone("SELECT * FROM users WHERE owner_id = ?", [owner_id])
        return self._row_to_user(row) if row else None

    def update_user_notification_settings(
        self,
        owner_id: str,
        notification_email_enabled: bool,
        work_notification_email: Optional[str],
        personal_notification_email: Optional[str],
    ) -> Optional[User]:
        now_iso = utc_now_z()
        result = self._request(
            """
            UPDATE users
            SET notification_email_enabled = ?, work_notification_email = ?,
                personal_notification_email = ?, updated_at = ?
            WHERE owner_id = ?
            """,
            [1 if notification_email_enabled else 0, work_notification_email, personal_notification_email, now_iso, owner_id],
        )
        if self._changed_rows(result) <= 0:
            return None
        row = self._fetchone("SELECT * FROM users WHERE owner_id = ?", [owner_id])
        return self._row_to_user(row) if row else None

    def list_admin_users(self, *, q: Optional[str] = None, role: Optional[str] = None, page: int = 1, page_size: int = 10, sort_by: str = "latest_task_at", sort_order: str = "desc") -> Dict[str, Any]:
        where = ["1=1"]
        params: list[Any] = []
        if role:
            where.append("base.role = ?")
            params.append(role)
        if q:
            wildcard = f"%{q}%"
            where.append("(base.owner_id LIKE ? OR COALESCE(base.user_name, '') LIKE ? OR COALESCE(base.email, '') LIKE ?)")
            params.extend([wildcard, wildcard, wildcard])
        where_clause = " AND ".join(where)
        safe_sort_map = {
            "owner_id": "base.owner_id",
            "user_name": "COALESCE(base.user_name, '')",
            "email": "COALESCE(base.email, '')",
            "role": "COALESCE(base.role, '')",
            "last_login_at": "COALESCE(base.last_login_at, '')",
            "created_at": "COALESCE(base.created_at, '')",
            "task_count": "base.task_count",
            "latest_task_at": "COALESCE(base.latest_task_at, '')",
        }
        safe_sort = safe_sort_map.get(sort_by, "COALESCE(base.latest_task_at, '')")
        direction = "ASC" if str(sort_order or "").strip().lower() == "asc" else "DESC"
        offset = max(0, (page - 1) * page_size)
        total_row = self._fetchone(
            f"""
            WITH user_task_stats AS (
                SELECT owner_id, COUNT(*) AS task_count, MAX(updated_at) AS latest_task_at
                FROM tasks
                WHERE deleted_at IS NULL AND owner_id IS NOT NULL AND TRIM(owner_id) <> ''
                GROUP BY owner_id
            ),
            base AS (
                SELECT u.owner_id AS owner_id, u.name AS user_name, u.email AS email, u.role AS role,
                       u.last_login_at AS last_login_at, u.created_at AS created_at, COALESCE(s.task_count, 0) AS task_count, s.latest_task_at AS latest_task_at
                FROM users u
                LEFT JOIN user_task_stats s ON u.owner_id = s.owner_id
                UNION
                SELECT s.owner_id AS owner_id, NULL AS user_name, NULL AS email, NULL AS role, NULL AS last_login_at, NULL AS created_at, s.task_count AS task_count, s.latest_task_at AS latest_task_at
                FROM user_task_stats s
                LEFT JOIN users u ON s.owner_id = u.owner_id
                WHERE u.owner_id IS NULL
            )
            SELECT COUNT(*) AS c
            FROM base
            WHERE {where_clause}
            """,
            params,
        )
        rows = self._fetchall(
            f"""
            WITH user_task_stats AS (
                SELECT owner_id, COUNT(*) AS task_count, MAX(updated_at) AS latest_task_at
                FROM tasks
                WHERE deleted_at IS NULL AND owner_id IS NOT NULL AND TRIM(owner_id) <> ''
                GROUP BY owner_id
            ),
            base AS (
                SELECT u.owner_id AS owner_id, u.name AS user_name, u.email AS email, u.role AS role,
                       u.last_login_at AS last_login_at, u.created_at AS created_at, COALESCE(s.task_count, 0) AS task_count, s.latest_task_at AS latest_task_at
                FROM users u
                LEFT JOIN user_task_stats s ON u.owner_id = s.owner_id
                UNION
                SELECT s.owner_id AS owner_id, NULL AS user_name, NULL AS email, NULL AS role, NULL AS last_login_at, NULL AS created_at, s.task_count AS task_count, s.latest_task_at AS latest_task_at
                FROM user_task_stats s
                LEFT JOIN users u ON s.owner_id = u.owner_id
                WHERE u.owner_id IS NULL
            )
            SELECT *
            FROM base
            WHERE {where_clause}
            ORDER BY {safe_sort} {direction}, base.owner_id ASC
            LIMIT ? OFFSET ?
            """,
            params + [page_size, offset],
        )
        return {"total": int((total_row or {}).get("c") or 0), "items": [{
            "owner_id": row.get("owner_id"),
            "user_name": row.get("user_name"),
            "email": row.get("email"),
            "role": row.get("role"),
            "last_login_at": row.get("last_login_at"),
            "created_at": row.get("created_at"),
            "task_count": int(row.get("task_count") or 0),
            "latest_task_at": row.get("latest_task_at"),
        } for row in rows]}
