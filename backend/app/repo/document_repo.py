"""document / document_version 表：入库所需的读写。

设计依据：详细设计 4.6、架构文档 5.3。

document 与 document_version 是循环外键（AGENTS.md）：先插 document
（``searchable_version_id`` 为 NULL）→ 插 version → 需要发布时再 UPDATE 回填。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.core.ids import new_id
from app.core.time import utc_now_iso
from app.repo import db

DOC_STATUSES = ("draft", "parsing", "parse_failed", "proofreading", "published", "offline")


def get(doc_id: str) -> sqlite3.Row | None:
    return db.query_one(
        "SELECT * FROM document WHERE id = ? AND deleted_at IS NULL",
        (doc_id,),
    )


def find_by_dup_key(region_id: str, dup_key: str) -> sqlite3.Row | None:
    return db.query_one(
        "SELECT * FROM document WHERE region_id = ? AND dup_key = ? AND deleted_at IS NULL",
        (region_id, dup_key),
    )


def get_version(version_id: str) -> sqlite3.Row | None:
    return db.query_one("SELECT * FROM document_version WHERE id = ?", (version_id,))


def latest_version(doc_id: str, version_type: str = "raw") -> sqlite3.Row | None:
    return db.query_one(
        "SELECT * FROM document_version WHERE doc_id = ? AND version_type = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (doc_id, version_type),
    )


def create_document(
    *,
    region_id: str,
    title: str,
    source_note: str,
    dup_key: str,
    created_by: str,
    edition: str | None = None,
    year_text: str | None = None,
    status: str = "draft",
) -> str:
    if status not in DOC_STATUSES:
        raise ValueError(f"非法文档状态：{status}")
    doc_id = new_id("doc")

    def _do(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO document("
            " id, region_id, title, edition, year_text, source_note, status, version, "
            " chunk_count, dup_key, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)",
            (
                doc_id,
                region_id,
                title,
                edition,
                year_text,
                source_note,
                status,
                dup_key,
                created_by,
            ),
        )

    db.get_write_queue().submit(_do)
    return doc_id


def add_version(
    *,
    doc_id: str,
    content: str,
    version_type: str = "raw",
    stats: dict[str, Any] | None = None,
    created_by: str | None = None,
    source_version_id: str | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
) -> tuple[str, int]:
    """插入一版正文并让 document.version +1，返回 (version_id, 新版本号)。"""
    version_id = new_id("ver")

    def _do(conn: sqlite3.Connection) -> int:
        conn.execute(
            "INSERT INTO document_version("
            " id, doc_id, version_type, content, source_version_id, model, "
            " prompt_version, stats_json, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                version_id,
                doc_id,
                version_type,
                content,
                source_version_id,
                model,
                prompt_version,
                json.dumps(stats or {}, ensure_ascii=False),
                created_by,
            ),
        )
        conn.execute(
            "UPDATE document SET version = "
            "  (SELECT COUNT(*) FROM document_version WHERE doc_id = ?), "
            "  updated_at = ? WHERE id = ?",
            (doc_id, utc_now_iso(), doc_id),
        )
        row = conn.execute("SELECT version FROM document WHERE id = ?", (doc_id,)).fetchone()
        return int(row["version"]) if row else 1

    version_no = db.get_write_queue().submit(_do)
    return version_id, version_no


def update_after_chunk(
    doc_id: str,
    *,
    version_id: str,
    chunk_count: int,
    index_version: str,
    status: str | None = None,
) -> None:
    """分块完成后的回写：chunk_count、active_index_version，可选状态迁移。"""

    def _do(conn: sqlite3.Connection) -> None:
        if status is not None:
            if status not in DOC_STATUSES:
                raise ValueError(f"非法文档状态：{status}")
            conn.execute(
                "UPDATE document SET chunk_count = ?, active_index_version = ?, status = ?, "
                "updated_at = ? WHERE id = ?",
                (chunk_count, index_version, status, utc_now_iso(), doc_id),
            )
        else:
            conn.execute(
                "UPDATE document SET chunk_count = ?, active_index_version = ?, "
                "updated_at = ? WHERE id = ?",
                (chunk_count, index_version, utc_now_iso(), doc_id),
            )

    db.get_write_queue().submit(_do)


def set_status(
    doc_id: str,
    status: str,
    *,
    searchable_version_id: str | None = None,
) -> None:
    """状态迁移；publish 时回填 searchable_version_id（循环外键后补）。"""
    if status not in DOC_STATUSES:
        raise ValueError(f"非法文档状态：{status}")
    if status == "published" and not searchable_version_id:
        raise ValueError("发布必须指定 searchable_version_id")

    def _do(conn: sqlite3.Connection) -> None:
        if status == "published":
            conn.execute(
                "UPDATE document SET status = ?, searchable_version_id = ?, "
                "published_at = COALESCE(published_at, ?), updated_at = ? WHERE id = ?",
                (status, searchable_version_id, utc_now_iso(), utc_now_iso(), doc_id),
            )
        else:
            conn.execute(
                "UPDATE document SET status = ?, updated_at = ? WHERE id = ?",
                (status, utc_now_iso(), doc_id),
            )

    db.get_write_queue().submit(_do)


def set_active_index_version(doc_id: str, index_version: str) -> None:
    db.get_write_queue().execute(
        "UPDATE document SET active_index_version = ?, updated_at = ? WHERE id = ?",
        (index_version, utc_now_iso(), doc_id),
    )


def reset_for_rechunk(doc_id: str) -> None:
    """重新分块前把文档退回待校对，并断开旧的 searchable_version_id。

    否则旧版本仍会被当作可检索版本，而 chunk 已按新正文重建，引用会指向错内容。
    """
    db.get_write_queue().execute(
        "UPDATE document SET status = 'proofreading', searchable_version_id = NULL, "
        "chunk_count = 0, updated_at = ? WHERE id = ?",
        (utc_now_iso(), doc_id),
    )


def list_searchable_doc_ids(
    region_id: str,
    statuses: tuple[str, ...] = ("published",),
) -> list[str]:
    placeholders = ",".join("?" * len(statuses))
    rows = db.query_all(
        f"SELECT id FROM document WHERE region_id = ? AND status IN ({placeholders}) "
        "AND deleted_at IS NULL ORDER BY id",
        (region_id, *statuses),
    )
    return [row["id"] for row in rows]
