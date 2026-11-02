"""app_config 表：可调参数（模型指纹、索引版本、阈值、限额）。

设计依据：架构文档 6.1、详细设计附录 A。

只放"不改代码就能调"的参数；启动必需项（数据库目录、JWT 密钥）仍在环境变量。
配置项首次启动时由 ``seed_defaults()`` 写入，之后 ``INSERT OR IGNORE`` 不会覆盖
用户改过的值。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Literal

from app.core.time import utc_now_iso
from app.repo import db

ValueType = Literal["int", "float", "string", "bool", "json"]

# 初值来自架构文档 6.1 / 详细设计附录 A。新增键必须先改文档，再改这里。
DEFAULTS: dict[str, tuple[Any, ValueType, str]] = {
    "llm.chat.model": ("", "string", "主模型（待定，AS-02）"),
    "llm.chat.fallback_model": ("", "string", "限流/超时降级目标"),
    "llm.embedding.model": (
        "data/models/bge-m3",
        "string",
        "向量模型；换它必须新建 collection + 全量 reindex（D-7、4.7.5）",
    ),    "llm.embedding.revision": (
        "5617a9f61b028005a4858fdac845db406aefb181",
        "string",
        "HF commit hash，锁死权重；留空则启动报错",
    ),
    "llm.embedding.dim": (1024, "int", "与 collection 实际维度比对，不一致拒绝启动"),
    "llm.embedding.query_instruction": (
        "",
        "string",
        "bge-m3 为空；换 bge-large-zh-v1.5 时填写其检索指令（只加 query 侧）",
    ),
    "llm.embedding.batch_size": (16, "int", "CPU 16 / GPU 64"),
    "llm.embedding.device": ("cpu", "string", "cpu / cuda"),
    "llm.embedding.cache_dir": (
        "data/models",
        "string",
        "模型缓存目录；配合 HF_HUB_OFFLINE=1 可离线加载",
    ),
    "llm.rerank.model": (
        "BAAI/bge-reranker-v2-m3",
        "string",
        "none 时关闭 rerank，阈值语义变为余弦（D-5）",
    ),
    "index.active_version": (
        "",
        "string",
        "当前生效的索引版本；为空时按模型指纹自动计算，切换版本只改这一项",
    ),
    "index.chunk_size": (800, "int", "每块目标字数（按长度切块）；改动等于换索引版本"),
    "index.chunk_overlap": (100, "int", "相邻块的重叠字数"),
    "retrieval.top_k_vec": (20, "int", "向量路召回数"),
    "retrieval.top_k_bm25": (20, "int", "全文路召回数"),
    "retrieval.rerank_top_n": (8, "int", "送生成的片段上限"),
    "retrieval.score_threshold": (
        0.62,
        "float",
        "只作用于 sigmoid 后的 rerank 分，绝不能用于 RRF 分（D-5）",
    ),
    "retrieval.rrf_k": (60, "int", "RRF 平滑常数"),
    "prompt.qa.version": ("p_v3", "string", "引证档/解读模板版本"),
    "prompt.outline.version": ("p_v2", "string", "报告大纲模板版本"),
    "limits.context_tokens": (8192, "int", "上下文预算"),
    "limits.daily_token_budget": (0, "int", "日限额，0 表示不启用"),
    "limits.max_tool_calls": (6, "int", "工具调用上限"),
    "limits.task_timeout_ms": (25000, "int", "单任务超时"),
    "checklist.version": (7, "int", "自查清单版本"),
    "feature.preview_mode": (True, "bool", "预览模式开关"),
}


def _value_type(value: Any) -> ValueType:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    return "json"


def _decode(value_json: str, value_type: str) -> Any:
    value = json.loads(value_json)
    if value_type == "int":
        return int(value)
    if value_type == "float":
        return float(value)
    if value_type == "bool":
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    return value


def seed_defaults() -> int:
    """写入缺失的默认配置，返回本次新增条数（幂等）。"""
    rows = [
        (
            key,
            json.dumps(value, ensure_ascii=False),
            value_type,
            description,
        )
        for key, (value, value_type, description) in DEFAULTS.items()
    ]

    def _do(conn: sqlite3.Connection) -> int:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO app_config(key, value_json, value_type, description) "
            "VALUES (?, ?, ?, ?)",
            rows,
        )
        return conn.total_changes - before

    return db.get_write_queue().submit(_do)


def get_value(key: str, default: Any = None) -> Any:
    row = db.query_one("SELECT value_json, value_type FROM app_config WHERE key = ?", (key,))
    if row is None:
        return default
    return _decode(row["value_json"], row["value_type"])


def get_str(key: str, default: str = "") -> str:
    value = get_value(key, default)
    return default if value is None else str(value)


def get_int(key: str, default: int = 0) -> int:
    value = get_value(key, default)
    return default if value is None else int(value)


def get_float(key: str, default: float = 0.0) -> float:
    value = get_value(key, default)
    return default if value is None else float(value)


def get_bool(key: str, default: bool = False) -> bool:
    value = get_value(key, default)
    return default if value is None else bool(value)


def set_value(
    key: str,
    value: Any,
    *,
    value_type: ValueType | None = None,
    description: str | None = None,
    updated_by: str | None = None,
) -> None:
    """新增或更新配置；``version`` 单调递增，用于回溯。"""
    kind = value_type or _value_type(value)
    payload = json.dumps(value, ensure_ascii=False)

    def _do(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO app_config(key, value_json, value_type, description, updated_by) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "  value_json = excluded.value_json, "
            "  value_type = excluded.value_type, "
            "  description = COALESCE(excluded.description, app_config.description), "
            "  updated_by = excluded.updated_by, "
            "  version = app_config.version + 1, "
            "  updated_at = ?",
            (key, payload, kind, description, updated_by, utc_now_iso()),
        )

    db.get_write_queue().submit(_do)
