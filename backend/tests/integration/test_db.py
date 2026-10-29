"""迁移与 WriteQueue 的集成测试。

只依赖标准库 + pytest，不需要 FastAPI / 模型依赖：
- 迁移：幂等、版本号、24 张实体表 + FTS5 虚表与触发器；
- 写队列：批次提交、单任务 SAVEPOINT 隔离、多语句事务。
"""

from __future__ import annotations

import concurrent.futures

import pytest

from app.repo import db


@pytest.fixture()
def db_path(tmp_path):
    """在临时目录建一个已迁移的库，避免污染 backend/data。"""
    path = tmp_path / "app.db"
    db.migrate(path)
    return path


@pytest.fixture()
def write_queue(db_path):
    """独立 WriteQueue（不走全局单例，避免测试之间互相污染）。"""
    queue = db.WriteQueue(db_path)
    queue.start()
    try:
        yield queue
    finally:
        queue.stop()


def _insert_corpus(queue: db.WriteQueue) -> int:
    """插入一条最小语料链路，返回 chunk 的 INTEGER 主键。"""
    queue.execute(
        "INSERT INTO region(id,name,aliases_json,era_note) VALUES(?,?,?,?)",
        ("610324", "扶风县", '["扶风"]', "据清乾隆本"),
    )
    queue.execute(
        "INSERT INTO document(id,region_id,title,source_note,status,dup_key,created_by) "
        "VALUES(?,?,?,?,?,?,?)",
        ("d1", "610324", "扶风县志", "测试", "proofreading", "k1", "u1"),
    )
    queue.execute(
        "INSERT INTO document_version(id,doc_id,version_type,content) VALUES(?,?,?,?)",
        ("v1", "d1", "raw", "法门寺在县北二十里。"),
    )
    return queue.insert(
        "INSERT INTO chunk(chunk_id,doc_id,version_id,region_id,volume,volume_no,seq,"
        "content,content_seg,volume_seg) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "c1", "d1", "v1", "610324", "卷二·建置", 2, 1,
            "法门寺在县北二十里。", "法门寺 在 县北 二十里", "卷二 建置",
        ),
    )


def test_migration_is_idempotent_and_complete(db_path):
    # 重复执行不应重复建表，也不应推进版本号
    assert db.migrate(db_path) == 1

    with db.read_connection(db_path) as conn:
        names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    # 24 张实体表 + chunk_fts 虚表（外加 4 张 FTS5 shadow 表）
    for table in (
        "user", "auth_refresh_token", "invite_code",
        "region", "document", "document_version", "chunk", "chunk_fts", "polish_segment",
        "task",
        "conversation", "message", "citation", "feedback",
        "report", "report_section", "export_artifact",
        "agent_trace", "agent_span", "agent_event",
        "operation_log", "eval_case", "eval_run", "app_config", "metric_daily",
    ):
        assert table in names, table
    assert "chunk_fts_data" in names
    assert len(names) == 29


def test_fts_trigger_tracks_chunk(write_queue):
    assert _insert_corpus(write_queue) > 0

    rows = db.query_all(
        "SELECT c.chunk_id FROM chunk_fts f JOIN chunk c ON c.id = f.rowid "
        "WHERE chunk_fts MATCH ?",
        ("法门寺",),
        db_path=write_queue.db_path,
    )
    assert [row["chunk_id"] for row in rows] == ["c1"]


def test_savepoint_isolates_failed_job(write_queue):
    """同批里一条失败，另一条仍应提交。"""
    def bad(conn):
        conn.execute("INSERT INTO region(id,name) VALUES(?,?)", ("610399", "坏数据"))
        raise ValueError("boom")

    def good(conn):
        conn.execute("INSERT INTO region(id,name) VALUES(?,?)", ("610326", "眉县"))
        return "ok"

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        bad_future = pool.submit(write_queue.submit, bad)
        good_future = pool.submit(write_queue.submit, good)

        with pytest.raises(ValueError, match="boom"):
            bad_future.result()
        assert good_future.result() == "ok"

    assert db.query_one(
        "SELECT id FROM region WHERE id='610326'", db_path=write_queue.db_path
    ) is not None
    assert db.query_one(
        "SELECT id FROM region WHERE id='610399'", db_path=write_queue.db_path
    ) is None


def test_multi_statement_submit_is_atomic(write_queue):
    _insert_corpus(write_queue)

    def update(conn):
        conn.execute("UPDATE region SET era_note=? WHERE id=?", ("新说明", "610324"))
        conn.execute("UPDATE region SET sort_order=sort_order+1 WHERE id=?", ("610324",))
        row = conn.execute("SELECT era_note FROM region WHERE id='610324'").fetchone()
        return row["era_note"]

    assert write_queue.submit(update) == "新说明"
