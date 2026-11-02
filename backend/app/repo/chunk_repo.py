"""chunk 表：分块写入、待向量化查询、索引状态回写。

设计依据：详细设计 4.6 / 4.7.3、架构文档 5.3（chunk 是检索真相源）。

约定：
- 所有写走 ``db.get_write_queue()``，读走 ``db.query_*``；
- ``chunk.id`` 是 INTEGER（FTS5 rowid），``chunk_id`` 是 TEXT（对外引用）；
- 向量化成功后才把 ``index_state`` 从 pending 改 indexed；Chroma 失败时保持
  pending，任务可中断续跑（4.7.3）。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from app.core.ids import new_id
from app.repo import db

_INSERT_SQL = """
INSERT INTO chunk (
  chunk_id, doc_id, version_id, region_id, seq,
  char_start, char_end, content, content_seg,
  token_count, ocr_flag, sensitive_flag, is_searchable,
  index_version, index_state
) VALUES (
  :chunk_id, :doc_id, :version_id, :region_id, :seq,
  :char_start, :char_end, :content, :content_seg,
  :token_count, :ocr_flag, :sensitive_flag, 1,
  :index_version, 'pending'
)
"""


def insert_chunks(
    doc_id: str,
    version_id: str,
    region_id: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    index_version: str,
    ocr_flag: int = 0,
) -> list[str]:
    """单事务写入一版文档的全部 chunk，返回按 seq 生成的 chunk_id。"""
    chunk_ids: list[str] = []

    def _do(conn: sqlite3.Connection) -> None:
        for row in rows:
            chunk_id = new_id("c")
            params = {
                "chunk_id": chunk_id,
                "doc_id": doc_id,
                "version_id": version_id,
                "region_id": region_id,
                "seq": row["seq"],
                "char_start": row.get("char_start"),
                "char_end": row.get("char_end"),
                "content": row["content"],
                "content_seg": row["content_seg"],
                "token_count": row.get("token_count", 0),
                "ocr_flag": 1 if ocr_flag else 0,
                "sensitive_flag": 0,
                "index_version": index_version,
            }
            conn.execute(_INSERT_SQL, params)
            chunk_ids.append(chunk_id)

    db.get_write_queue().submit(_do)
    return chunk_ids


def get_by_chunk_id(chunk_id: str) -> sqlite3.Row | None:
    return db.query_one(
        "SELECT * FROM chunk WHERE chunk_id = ?",
        (chunk_id,),
    )


def list_by_doc(doc_id: str) -> list[sqlite3.Row]:
    return db.query_all(
        "SELECT * FROM chunk WHERE doc_id = ? ORDER BY seq",
        (doc_id,),
    )


def list_pending(
    doc_id: str,
    index_version: str | None = None,
    *,
    limit: int = 16,
) -> list[sqlite3.Row]:
    """待向量化的一批 chunk（按 seq 稳定排序，便于中断续跑）。"""
    if index_version:
        return db.query_all(
            "SELECT chunk_id, doc_id, region_id, seq, content, ocr_flag "
            "FROM chunk WHERE doc_id = ? AND index_state = 'pending' "
            "AND (index_version IS NULL OR index_version = ?) "
            "ORDER BY seq LIMIT ?",
            (doc_id, index_version, limit),
        )
    return db.query_all(
        "SELECT chunk_id, doc_id, region_id, seq, content, ocr_flag "
        "FROM chunk WHERE doc_id = ? AND index_state = 'pending' "
        "ORDER BY seq LIMIT ?",
        (doc_id, limit),
    )


def list_ids_by_doc(doc_id: str) -> list[str]:
    rows = db.query_all(
        "SELECT chunk_id FROM chunk WHERE doc_id = ? ORDER BY seq",
        (doc_id,),
    )
    return [row["chunk_id"] for row in rows]


def count_by_state(doc_id: str, index_version: str | None = None) -> dict[str, int]:
    if index_version:
        rows = db.query_all(
            "SELECT index_state, COUNT(*) AS n FROM chunk "
            "WHERE doc_id = ? AND index_version = ? GROUP BY index_state",
            (doc_id, index_version),
        )
    else:
        rows = db.query_all(
            "SELECT index_state, COUNT(*) AS n FROM chunk WHERE doc_id = ? GROUP BY index_state",
            (doc_id,),
        )
    return {row["index_state"]: int(row["n"]) for row in rows}


def count_chunks(doc_id: str) -> int:
    row = db.query_one("SELECT COUNT(*) AS n FROM chunk WHERE doc_id = ?", (doc_id,))
    return int(row["n"]) if row else 0


def mark_index_state(
    chunk_ids: Sequence[str],
    state: str,
    *,
    index_version: str | None = None,
) -> int:
    """回写索引状态；状态枚举由 DDL CHECK 约束兜底。"""
    if not chunk_ids:
        return 0
    ids = list(chunk_ids)
    placeholders = ",".join("?" * len(ids))

    def _do(conn: sqlite3.Connection) -> int:
        if index_version:
            cursor = conn.execute(
                f"UPDATE chunk SET index_state = ?, index_version = ? "
                f"WHERE chunk_id IN ({placeholders})",
                (state, index_version, *ids),
            )
        else:
            cursor = conn.execute(
                f"UPDATE chunk SET index_state = ? WHERE chunk_id IN ({placeholders})",
                (state, *ids),
            )
        return cursor.rowcount

    return db.get_write_queue().submit(_do)


def reset_stale(doc_id: str, index_version: str | None = None) -> int:
    """把 stale 重新置为 pending（失败重试/对账补偿后重跑向量化）。"""

    def _do(conn: sqlite3.Connection) -> int:
        if index_version:
            cursor = conn.execute(
                "UPDATE chunk SET index_state = 'pending' "
                "WHERE doc_id = ? AND index_version = ? AND index_state = 'stale'",
                (doc_id, index_version),
            )
        else:
            cursor = conn.execute(
                "UPDATE chunk SET index_state = 'pending' "
                "WHERE doc_id = ? AND index_state = 'stale'",
                (doc_id,),
            )
        return cursor.rowcount

    return db.get_write_queue().submit(_do)


def delete_by_doc(doc_id: str) -> int:
    def _do(conn: sqlite3.Connection) -> int:
        return conn.execute("DELETE FROM chunk WHERE doc_id = ?", (doc_id,)).rowcount

    return db.get_write_queue().submit(_do)


def rebuild_fts() -> None:
    """FTS5 外部内容表重建（架构 5.8）。"""
    db.get_write_queue().execute("INSERT INTO chunk_fts(chunk_fts) VALUES('rebuild')")
