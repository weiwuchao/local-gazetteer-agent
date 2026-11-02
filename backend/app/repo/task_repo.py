"""task 表：异步任务、幂等键、租约、进度。

约束：本模块是 SQL 的唯一出口；软删条件必须内置（deleted_at IS NULL）。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.core.ids import new_id
from app.core.time import utc_now_iso
from app.repo import db


def create(
    *,
    kind: str,
    idempotency_key: str,
    payload: dict[str, Any] | None = None,
    priority: int = 0,
    created_by: str | None = None,
    max_attempts: int = 3,
) -> str:
    task_id = new_id("task")

    def _do(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO task(id, kind, idempotency_key, priority, payload_json, "
            " max_attempts, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                kind,
                idempotency_key,
                priority,
                json.dumps(payload or {}, ensure_ascii=False),
                max_attempts,
                created_by,
            ),
        )

    db.get_write_queue().submit(_do)
    return task_id


def get(task_id: str) -> sqlite3.Row | None:
    return db.query_one("SELECT * FROM task WHERE id = ?", (task_id,))


def get_by_idempotency_key(key: str) -> sqlite3.Row | None:
    return db.query_one("SELECT * FROM task WHERE idempotency_key = ?", (key,))


def payload(row: sqlite3.Row) -> dict[str, Any]:
    raw = row["payload_json"]
    return json.loads(raw) if raw else {}


def mark_running(task_id: str, *, worker_id: str | None = None) -> None:
    db.get_write_queue().execute(
        "UPDATE task SET status = 'running', started_at = ?, worker_id = ? WHERE id = ?",
        (utc_now_iso(), worker_id, task_id),
    )


def set_progress(task_id: str, progress: int) -> None:
    db.get_write_queue().execute(
        "UPDATE task SET progress = ? WHERE id = ?",
        (max(0, min(100, progress)), task_id),
    )


def mark_succeeded(task_id: str, result: dict[str, Any] | None = None) -> None:
    db.get_write_queue().execute(
        "UPDATE task SET status = 'succeeded', finished_at = ?, progress = 100, "
        "result_json = ?, error_code = NULL, error_detail = NULL WHERE id = ?",
        (utc_now_iso(), json.dumps(result or {}, ensure_ascii=False), task_id),
    )


def mark_failed(task_id: str, *, error_code: str, error_detail: str | None = None) -> None:
    db.get_write_queue().execute(
        "UPDATE task SET status = 'failed', finished_at = ?, attempt = attempt + 1, "
        "error_code = ?, error_detail = ? WHERE id = ?",
        (utc_now_iso(), error_code, error_detail, task_id),
    )
