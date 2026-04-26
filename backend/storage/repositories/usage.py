"""Usage and admin usage repository methods."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from backend.time_utils import utc_now_z


class UsageRepositoryMixin:
    def _row_to_task_llm_usage(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "task_id": row.get("task_id"),
            "owner_id": row.get("owner_id"),
            "task_type": row.get("task_type"),
            "task_status": row.get("task_status") or "",
            "prompt_tokens": int(row.get("prompt_tokens") or 0),
            "completion_tokens": int(row.get("completion_tokens") or 0),
            "total_tokens": int(row.get("total_tokens") or 0),
            "reasoning_tokens": int(row.get("reasoning_tokens") or 0),
            "llm_call_count": int(row.get("llm_call_count") or 0),
            "estimated_cost_cny": float(row.get("estimated_cost_cny") or 0),
            "price_missing": bool(int(row.get("price_missing") or 0)),
            "model_breakdown_json": self._parse_metadata(row.get("model_breakdown_json")),
            "first_usage_at": row.get("first_usage_at"),
            "last_usage_at": row.get("last_usage_at"),
            "currency": row.get("currency") or "CNY",
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    def get_task_llm_usage(self, task_id: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone("SELECT * FROM task_llm_usage WHERE task_id = ?", [str(task_id or "").strip()])
        return self._row_to_task_llm_usage(row) if row else None

    def upsert_task_llm_usage(self, usage: Dict[str, Any]) -> bool:
        payload = {
            "task_id": str(usage.get("task_id", "")).strip(),
            "owner_id": str(usage.get("owner_id", "")).strip(),
            "task_type": str(usage.get("task_type", "")).strip(),
            "task_status": str(usage.get("task_status", "")).strip(),
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
            "reasoning_tokens": int(usage.get("reasoning_tokens") or 0),
            "llm_call_count": int(usage.get("llm_call_count") or 0),
            "estimated_cost_cny": float(usage.get("estimated_cost_cny") or 0),
            "price_missing": 1 if usage.get("price_missing") else 0,
            "model_breakdown_json": usage.get("model_breakdown_json") or {},
            "first_usage_at": self._normalize_usage_timestamp(usage.get("first_usage_at"), field="first_usage_at"),
            "last_usage_at": self._normalize_usage_timestamp(usage.get("last_usage_at"), field="last_usage_at"),
            "currency": str(usage.get("currency") or "CNY").strip() or "CNY",
            "created_at": self._normalize_usage_timestamp(usage.get("created_at") or utc_now_z(), field="created_at"),
            "updated_at": self._normalize_usage_timestamp(usage.get("updated_at") or utc_now_z(), field="updated_at"),
        }
        if not payload["task_id"] or not payload["owner_id"] or not payload["task_type"]:
            return False

        result = self._request(
            """
            INSERT INTO task_llm_usage (
                task_id, owner_id, task_type, task_status,
                prompt_tokens, completion_tokens, total_tokens, reasoning_tokens,
                llm_call_count, estimated_cost_cny, price_missing, model_breakdown_json,
                first_usage_at, last_usage_at, currency, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                owner_id = excluded.owner_id,
                task_type = excluded.task_type,
                task_status = excluded.task_status,
                prompt_tokens = excluded.prompt_tokens,
                completion_tokens = excluded.completion_tokens,
                total_tokens = excluded.total_tokens,
                reasoning_tokens = excluded.reasoning_tokens,
                llm_call_count = excluded.llm_call_count,
                estimated_cost_cny = excluded.estimated_cost_cny,
                price_missing = excluded.price_missing,
                model_breakdown_json = excluded.model_breakdown_json,
                first_usage_at = excluded.first_usage_at,
                last_usage_at = excluded.last_usage_at,
                currency = excluded.currency,
                created_at = COALESCE(task_llm_usage.created_at, excluded.created_at),
                updated_at = excluded.updated_at
            """,
            [
                payload["task_id"],
                payload["owner_id"],
                payload["task_type"],
                payload["task_status"],
                payload["prompt_tokens"],
                payload["completion_tokens"],
                payload["total_tokens"],
                payload["reasoning_tokens"],
                payload["llm_call_count"],
                payload["estimated_cost_cny"],
                payload["price_missing"],
                payload["model_breakdown_json"],
                payload["first_usage_at"],
                payload["last_usage_at"],
                payload["currency"],
                payload["created_at"],
                payload["updated_at"],
            ],
        )
        return self._changed_rows(result) > 0

    def list_task_llm_usage_by_last_usage_range(self, start_iso: str, end_iso: str) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            """
            SELECT * FROM task_llm_usage
            WHERE last_usage_at IS NOT NULL
              AND last_usage_at >= ?
              AND last_usage_at < ?
            ORDER BY last_usage_at DESC
            """,
            [start_iso, end_iso],
        )
        return [self._row_to_task_llm_usage(row) for row in rows]

    @staticmethod
    def _row_to_llm_pricing_entry(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "model": str(row.get("model") or "").strip(),
            "region": str(row.get("region") or "").strip(),
            "billing_mode": str(row.get("billing_mode") or "").strip(),
            "input_tier_min_tokens": int(row.get("input_tier_min_tokens") or 0),
            "input_tier_max_tokens": int(row["input_tier_max_tokens"]) if row.get("input_tier_max_tokens") is not None else None,
            "prompt_price_per_million_cny": float(row.get("prompt_price_per_million_cny") or 0),
            "completion_price_per_million_cny": float(row.get("completion_price_per_million_cny") or 0),
            "source_url": str(row.get("source_url") or "").strip(),
            "source_hash": str(row.get("source_hash") or "").strip(),
            "fetched_at": row.get("fetched_at"),
            "expires_at": row.get("expires_at"),
            "parse_status": str(row.get("parse_status") or "").strip(),
            "parse_error": str(row.get("parse_error") or "").strip(),
            "updated_at": row.get("updated_at"),
        }

    @staticmethod
    def _row_to_llm_pricing_sync_state(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "region": str(row.get("region") or "").strip(),
            "billing_mode": str(row.get("billing_mode") or "").strip(),
            "cache_entry_count": int(row.get("cache_entry_count") or 0),
            "last_success_at": row.get("last_success_at"),
            "last_attempt_at": row.get("last_attempt_at"),
            "expires_at": row.get("expires_at"),
            "source_url": str(row.get("source_url") or "").strip(),
            "source_hash": str(row.get("source_hash") or "").strip(),
            "parse_status": str(row.get("parse_status") or "").strip(),
            "last_error": str(row.get("last_error") or "").strip(),
            "updated_at": row.get("updated_at"),
        }

    def list_llm_pricing_entries(self, *, region: str, billing_mode: str) -> List[Dict[str, Any]]:
        rows = self._fetchall(
            """
            SELECT * FROM llm_pricing_entries
            WHERE region = ? AND billing_mode = ?
            ORDER BY LOWER(model) ASC, input_tier_min_tokens ASC
            """,
            [str(region or "").strip(), str(billing_mode or "").strip()],
        )
        return [self._row_to_llm_pricing_entry(row) for row in rows]

    def replace_llm_pricing_entries(self, entries: List[Dict[str, Any]], *, region: str, billing_mode: str) -> int:
        normalized_region = str(region or "").strip()
        normalized_billing_mode = str(billing_mode or "").strip()
        if not normalized_region or not normalized_billing_mode:
            return 0

        self._request(
            "DELETE FROM llm_pricing_entries WHERE region = ? AND billing_mode = ?",
            [normalized_region, normalized_billing_mode],
        )
        inserted = 0
        for entry in entries or []:
            payload = {
                "model": str(entry.get("model") or "").strip(),
                "region": normalized_region,
                "billing_mode": normalized_billing_mode,
                "input_tier_min_tokens": int(entry.get("input_tier_min_tokens") or 0),
                "input_tier_max_tokens": int(entry["input_tier_max_tokens"]) if entry.get("input_tier_max_tokens") is not None else None,
                "prompt_price_per_million_cny": float(entry.get("prompt_price_per_million_cny") or 0),
                "completion_price_per_million_cny": float(entry.get("completion_price_per_million_cny") or 0),
                "source_url": str(entry.get("source_url") or "").strip(),
                "source_hash": str(entry.get("source_hash") or "").strip(),
                "fetched_at": self._normalize_usage_timestamp(entry.get("fetched_at") or utc_now_z(), field="fetched_at"),
                "expires_at": self._normalize_usage_timestamp(entry.get("expires_at") or utc_now_z(), field="expires_at"),
                "parse_status": str(entry.get("parse_status") or "ok").strip() or "ok",
                "parse_error": str(entry.get("parse_error") or "").strip(),
                "updated_at": self._normalize_usage_timestamp(entry.get("updated_at") or utc_now_z(), field="updated_at"),
            }
            if not payload["model"] or payload["input_tier_min_tokens"] <= 0:
                continue
            result = self._request(
                """
                INSERT INTO llm_pricing_entries (
                    model, region, billing_mode, input_tier_min_tokens, input_tier_max_tokens,
                    prompt_price_per_million_cny, completion_price_per_million_cny,
                    source_url, source_hash, fetched_at, expires_at, parse_status, parse_error, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(model, region, billing_mode, input_tier_min_tokens) DO UPDATE SET
                    input_tier_max_tokens = excluded.input_tier_max_tokens,
                    prompt_price_per_million_cny = excluded.prompt_price_per_million_cny,
                    completion_price_per_million_cny = excluded.completion_price_per_million_cny,
                    source_url = excluded.source_url,
                    source_hash = excluded.source_hash,
                    fetched_at = excluded.fetched_at,
                    expires_at = excluded.expires_at,
                    parse_status = excluded.parse_status,
                    parse_error = excluded.parse_error,
                    updated_at = excluded.updated_at
                """,
                [
                    payload["model"],
                    payload["region"],
                    payload["billing_mode"],
                    payload["input_tier_min_tokens"],
                    payload["input_tier_max_tokens"],
                    payload["prompt_price_per_million_cny"],
                    payload["completion_price_per_million_cny"],
                    payload["source_url"],
                    payload["source_hash"],
                    payload["fetched_at"],
                    payload["expires_at"],
                    payload["parse_status"],
                    payload["parse_error"],
                    payload["updated_at"],
                ],
            )
            inserted += max(0, self._changed_rows(result))
        return inserted

    def get_llm_pricing_sync_state(self, *, region: str, billing_mode: str) -> Optional[Dict[str, Any]]:
        row = self._fetchone(
            """
            SELECT * FROM llm_pricing_sync_state
            WHERE region = ? AND billing_mode = ?
            LIMIT 1
            """,
            [str(region or "").strip(), str(billing_mode or "").strip()],
        )
        return self._row_to_llm_pricing_sync_state(row) if row else None

    def upsert_llm_pricing_sync_state(self, state: Dict[str, Any]) -> bool:
        payload = {
            "region": str(state.get("region") or "").strip(),
            "billing_mode": str(state.get("billing_mode") or "").strip(),
            "cache_entry_count": int(state.get("cache_entry_count") or 0),
            "last_success_at": self._normalize_usage_timestamp(state.get("last_success_at"), field="last_success_at"),
            "last_attempt_at": self._normalize_usage_timestamp(state.get("last_attempt_at"), field="last_attempt_at"),
            "expires_at": self._normalize_usage_timestamp(state.get("expires_at"), field="expires_at"),
            "source_url": str(state.get("source_url") or "").strip(),
            "source_hash": str(state.get("source_hash") or "").strip(),
            "parse_status": str(state.get("parse_status") or "unknown").strip() or "unknown",
            "last_error": str(state.get("last_error") or "").strip(),
            "updated_at": self._normalize_usage_timestamp(state.get("updated_at") or utc_now_z(), field="updated_at"),
        }
        if not payload["region"] or not payload["billing_mode"]:
            return False
        result = self._request(
            """
            INSERT INTO llm_pricing_sync_state (
                region, billing_mode, cache_entry_count, last_success_at, last_attempt_at,
                expires_at, source_url, source_hash, parse_status, last_error, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(region, billing_mode) DO UPDATE SET
                cache_entry_count = excluded.cache_entry_count,
                last_success_at = excluded.last_success_at,
                last_attempt_at = excluded.last_attempt_at,
                expires_at = excluded.expires_at,
                source_url = excluded.source_url,
                source_hash = excluded.source_hash,
                parse_status = excluded.parse_status,
                last_error = excluded.last_error,
                updated_at = excluded.updated_at
            """,
            [
                payload["region"],
                payload["billing_mode"],
                payload["cache_entry_count"],
                payload["last_success_at"],
                payload["last_attempt_at"],
                payload["expires_at"],
                payload["source_url"],
                payload["source_hash"],
                payload["parse_status"],
                payload["last_error"],
                payload["updated_at"],
            ],
        )
        return self._changed_rows(result) > 0

    @staticmethod
    def _normalize_usage_scope(scope: str) -> Literal["task", "user", "all"]:
        text = str(scope or "task").strip().lower()
        if text in {"task", "user", "all"}:
            return text  # type: ignore[return-value]
        return "task"

    def list_admin_usage_table(
        self,
        *,
        start_iso: str,
        end_iso: str,
        scope: str = "task",
        q: Optional[str] = None,
        task_type: Optional[str] = None,
        task_status: Optional[str] = None,
        model: Optional[str] = None,
        page: int = 1,
        page_size: int = 10,
        sort_by: str = "lastUsageAt",
        sort_order: str = "desc",
    ) -> Dict[str, Any]:
        normalized_scope = self._normalize_usage_scope(scope)
        direction = "ASC" if str(sort_order or "").strip().lower() == "asc" else "DESC"
        owner_expr = "CASE WHEN tu.owner_id IS NULL OR TRIM(tu.owner_id) = '' THEN '-' ELSE tu.owner_id END"
        base_where = ["tu.last_usage_at IS NOT NULL", "tu.last_usage_at >= ?", "tu.last_usage_at < ?"]
        base_params: List[Any] = [start_iso, end_iso]
        normalized_task_type = str(task_type or "").strip().lower()
        if normalized_task_type:
            base_where.append("LOWER(COALESCE(tu.task_type, '')) = ?")
            base_params.append(normalized_task_type)
        normalized_task_status = str(task_status or "").strip().lower()
        if normalized_task_status:
            base_where.append("LOWER(COALESCE(tu.task_status, '')) = ?")
            base_params.append(normalized_task_status)
        normalized_model = str(model or "").strip().lower()
        if normalized_model:
            base_where.append(
                "EXISTS (SELECT 1 FROM json_each(COALESCE(tu.model_breakdown_json, '{}')) jm WHERE LOWER(CAST(jm.key AS TEXT)) = ?)"
            )
            base_params.append(normalized_model)

        base_where_clause = " AND ".join(base_where)
        summary_row = self._fetchone(
            f"""
            SELECT
                COUNT(*) AS total_tasks,
                COUNT(DISTINCT {owner_expr}) AS total_users,
                COALESCE(SUM(tu.total_tokens), 0) AS total_tokens,
                COALESCE(SUM(tu.llm_call_count), 0) AS total_llm_call_count,
                COALESCE(SUM(tu.estimated_cost_cny), 0) AS total_estimated_cost_cny,
                MAX(CASE WHEN tu.price_missing = 1 THEN 1 ELSE 0 END) AS price_missing
            FROM task_llm_usage tu
            WHERE {base_where_clause}
            """,
            base_params,
        ) or {}
        total_tasks = int(summary_row.get("total_tasks") or 0)
        total_users = int(summary_row.get("total_users") or 0)
        total_tokens = int(summary_row.get("total_tokens") or 0)
        total_llm_call_count = int(summary_row.get("total_llm_call_count") or 0)
        total_estimated_cost_cny = float(summary_row.get("total_estimated_cost_cny") or 0.0)
        price_missing = bool(int(summary_row.get("price_missing") or 0))
        entity_count = total_users if normalized_scope == "user" else total_tasks
        summary = {
            "total_tasks": total_tasks,
            "total_users": total_users,
            "total_tokens": total_tokens,
            "total_llm_call_count": total_llm_call_count,
            "total_estimated_cost_cny": round(total_estimated_cost_cny, 6),
            "avg_tokens_per_entity": round((total_tokens / entity_count), 3) if entity_count else 0.0,
            "avg_cost_per_entity_cny": round((total_estimated_cost_cny / entity_count), 6) if entity_count else 0.0,
            "entity_type": normalized_scope,
            "price_missing": price_missing,
        }
        if normalized_scope == "all":
            items = []
            if total_tasks:
                items = [{
                    "task_count": total_tasks,
                    "user_count": total_users,
                    "total_tokens": total_tokens,
                    "llm_call_count": total_llm_call_count,
                    "estimated_cost_cny": round(total_estimated_cost_cny, 6),
                    "price_missing": price_missing,
                }]
            return {"total": len(items), "price_missing": price_missing, "summary": {**summary, "entity_type": "all"}, "items": items}

        normalized_q = str(q or "").strip()
        q_where_clause = ""
        q_params: List[Any] = []
        if normalized_q:
            wildcard = f"%{normalized_q}%"
            if normalized_scope == "user":
                q_where_clause = "WHERE (g.owner_id LIKE ? OR COALESCE(g.user_name, '') LIKE ?)"
                q_params.extend([wildcard, wildcard])
            else:
                q_where_clause = (
                    f" AND (tu.task_id LIKE ? OR {owner_expr} LIKE ? OR COALESCE(u.name, '') LIKE ? "
                    "OR COALESCE(tu.task_type, '') LIKE ? OR COALESCE(tu.task_status, '') LIKE ? "
                    "OR COALESCE(tu.model_breakdown_json, '') LIKE ?)"
                )
                q_params.extend([wildcard, wildcard, wildcard, wildcard, wildcard, wildcard])
        offset = max(0, (page - 1) * page_size)
        if normalized_scope == "user":
            safe_sort_map = {
                "ownerId": "g.owner_id",
                "userName": "COALESCE(g.user_name, '')",
                "taskCount": "g.task_count",
                "totalTokens": "g.total_tokens",
                "llmCallCount": "g.llm_call_count",
                "estimatedCostCny": "g.estimated_cost_cny",
                "latestUsageAt": "COALESCE(g.latest_usage_at, '')",
            }
            safe_sort = safe_sort_map.get(sort_by, "g.total_tokens")
            total_row = self._fetchone(
                f"""
                WITH filtered AS (
                    SELECT {owner_expr} AS owner_id, u.name AS user_name, tu.total_tokens AS total_tokens,
                           tu.llm_call_count AS llm_call_count, tu.estimated_cost_cny AS estimated_cost_cny,
                           tu.price_missing AS price_missing, tu.last_usage_at AS last_usage_at
                    FROM task_llm_usage tu
                    LEFT JOIN users u ON tu.owner_id = u.owner_id
                    WHERE {base_where_clause}
                ),
                grouped AS (
                    SELECT f.owner_id AS owner_id, MAX(f.user_name) AS user_name, COUNT(*) AS task_count,
                           COALESCE(SUM(f.total_tokens), 0) AS total_tokens,
                           COALESCE(SUM(f.llm_call_count), 0) AS llm_call_count,
                           COALESCE(SUM(f.estimated_cost_cny), 0) AS estimated_cost_cny,
                           MAX(CASE WHEN f.price_missing = 1 THEN 1 ELSE 0 END) AS price_missing,
                           MAX(f.last_usage_at) AS latest_usage_at
                    FROM filtered f
                    GROUP BY f.owner_id
                )
                SELECT COUNT(*) AS c
                FROM grouped g
                {q_where_clause}
                """,
                base_params + q_params,
            ) or {}
            rows = self._fetchall(
                f"""
                WITH filtered AS (
                    SELECT {owner_expr} AS owner_id, u.name AS user_name, tu.total_tokens AS total_tokens,
                           tu.llm_call_count AS llm_call_count, tu.estimated_cost_cny AS estimated_cost_cny,
                           tu.price_missing AS price_missing, tu.last_usage_at AS last_usage_at
                    FROM task_llm_usage tu
                    LEFT JOIN users u ON tu.owner_id = u.owner_id
                    WHERE {base_where_clause}
                ),
                grouped AS (
                    SELECT f.owner_id AS owner_id, MAX(f.user_name) AS user_name, COUNT(*) AS task_count,
                           COALESCE(SUM(f.total_tokens), 0) AS total_tokens,
                           COALESCE(SUM(f.llm_call_count), 0) AS llm_call_count,
                           COALESCE(SUM(f.estimated_cost_cny), 0) AS estimated_cost_cny,
                           MAX(CASE WHEN f.price_missing = 1 THEN 1 ELSE 0 END) AS price_missing,
                           MAX(f.last_usage_at) AS latest_usage_at
                    FROM filtered f
                    GROUP BY f.owner_id
                )
                SELECT *
                FROM grouped g
                {q_where_clause}
                ORDER BY {safe_sort} {direction}, g.owner_id ASC
                LIMIT ? OFFSET ?
                """,
                base_params + q_params + [page_size, offset],
            )
            return {
                "total": int(total_row.get("c") or 0),
                "price_missing": price_missing,
                "summary": summary,
                "items": [{
                    "owner_id": row.get("owner_id"),
                    "user_name": row.get("user_name"),
                    "task_count": int(row.get("task_count") or 0),
                    "total_tokens": int(row.get("total_tokens") or 0),
                    "llm_call_count": int(row.get("llm_call_count") or 0),
                    "estimated_cost_cny": round(float(row.get("estimated_cost_cny") or 0), 6),
                    "price_missing": bool(int(row.get("price_missing") or 0)),
                    "latest_usage_at": row.get("latest_usage_at"),
                } for row in rows],
            }
        safe_sort_map = {
            "taskId": "tu.task_id",
            "ownerId": owner_expr,
            "userName": "COALESCE(u.name, '')",
            "taskType": "COALESCE(tu.task_type, '')",
            "taskStatus": "COALESCE(tu.task_status, '')",
            "totalTokens": "tu.total_tokens",
            "estimatedCostCny": "tu.estimated_cost_cny",
            "llmCallCount": "tu.llm_call_count",
            "lastUsageAt": "COALESCE(tu.last_usage_at, '')",
        }
        safe_sort = safe_sort_map.get(sort_by, "COALESCE(tu.last_usage_at, '')")
        total_row = self._fetchone(
            f"""
            SELECT COUNT(*) AS c
            FROM task_llm_usage tu
            LEFT JOIN users u ON tu.owner_id = u.owner_id
            WHERE {base_where_clause}{q_where_clause}
            """,
            base_params + q_params,
        ) or {}
        rows = self._fetchall(
            f"""
            SELECT tu.task_id AS task_id, {owner_expr} AS owner_id, u.name AS user_name, tu.task_type AS task_type,
                   tu.task_status AS task_status, tu.total_tokens AS total_tokens, tu.llm_call_count AS llm_call_count,
                   tu.estimated_cost_cny AS estimated_cost_cny, tu.price_missing AS price_missing,
                   tu.model_breakdown_json AS model_breakdown_json, tu.last_usage_at AS last_usage_at
            FROM task_llm_usage tu
            LEFT JOIN users u ON tu.owner_id = u.owner_id
            WHERE {base_where_clause}{q_where_clause}
            ORDER BY {safe_sort} {direction}, tu.task_id DESC
            LIMIT ? OFFSET ?
            """,
            base_params + q_params + [page_size, offset],
        )
        items: List[Dict[str, Any]] = []
        for row in rows:
            model_breakdown = self._parse_metadata(row.get("model_breakdown_json"))
            models = [str(key) for key in model_breakdown.keys()] if isinstance(model_breakdown, dict) else []
            items.append({
                "task_id": row.get("task_id"),
                "owner_id": row.get("owner_id"),
                "user_name": row.get("user_name"),
                "task_type": row.get("task_type") or "",
                "task_status": row.get("task_status") or "",
                "total_tokens": int(row.get("total_tokens") or 0),
                "llm_call_count": int(row.get("llm_call_count") or 0),
                "estimated_cost_cny": round(float(row.get("estimated_cost_cny") or 0), 6),
                "price_missing": bool(int(row.get("price_missing") or 0)),
                "models": models,
                "last_usage_at": row.get("last_usage_at"),
            })
        return {"total": int(total_row.get("c") or 0), "price_missing": price_missing, "summary": summary, "items": items}
