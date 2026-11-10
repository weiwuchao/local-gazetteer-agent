"""FTS5 查询构造（jieba、AND/OR 三级回退、BM25 排序分）

设计依据：详细设计 4.3.2。

三级回退：AND 表达式 → 纯 OR → ``content LIKE '%q%'``。每一级都限死在
``SearchScope`` 内（``region_id + doc_id IN + is_searchable=1``），且把实际命中
级别写进 ``FtsHit.fallback``，供检索器与轨迹打点。

SQL 只出现在 ``repo/chunk_repo.py``；本模块只构造 MATCH 表达式、LIKE 模式与
结果映射（架构红线：SQL 只能写在 ``app/repo/**``）。

BM25 原始分只用于排序与诊断；最终相关度只看 rerank 分（D-5）。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from app.rag.normalizer import build_match_expr, quote_fts
from app.rag.scope import SearchScope
from app.repo import chunk_repo

# AND 结果少于这个数时放宽为纯 OR（详细设计 4.3.2）。
_FALLBACK_MIN = 5


@dataclass(frozen=True, slots=True)
class FtsHit:
    chunk_id: str
    doc_id: str
    content: str
    char_start: int | None
    char_end: int | None
    ocr_flag: int
    score: float
    fallback: str


def or_match_expr(tokens: Sequence[str]) -> str:
    """纯 OR 表达式；tokens 已由调用方去空。"""
    return " OR ".join(quote_fts(token) for token in tokens)


def like_pattern(query: str) -> str:
    """把用户输入转成 LIKE 模式，转义 ``%``、``_`` 和反斜杠。"""
    escaped = (
        query.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return f"%{escaped}%"


def _to_hits(rows: Sequence[sqlite3.Row], fallback: str) -> list[FtsHit]:
    return [
        FtsHit(
            chunk_id=row["chunk_id"],
            doc_id=row["doc_id"],
            content=row["content"],
            char_start=row["char_start"],
            char_end=row["char_end"],
            ocr_flag=int(row["ocr_flag"] or 0),
            score=float(row["score"] or 0.0),
            fallback=fallback,
        )
        for row in rows
    ]


def search(
    query: str,
    tokens: Sequence[str],
    *,
    scope: SearchScope,
    limit: int,
) -> list[FtsHit]:
    """在 scope 内做 FTS5 检索；空 scope / 空 query 直接返回空列表。"""
    if limit <= 0 or not scope.visible_doc_ids:
        return []
    clean = [token for token in tokens if token.strip()]
    if not clean:
        return []

    and_rows = chunk_repo.search_fts(
        build_match_expr(clean),
        region_id=scope.region_id,
        doc_ids=scope.visible_doc_ids,
        limit=limit,
    )
    if len(and_rows) >= _FALLBACK_MIN:
        return _to_hits(and_rows, "and")

    or_rows = chunk_repo.search_fts(
        or_match_expr(clean),
        region_id=scope.region_id,
        doc_ids=scope.visible_doc_ids,
        limit=limit,
    )
    if or_rows:
        return _to_hits(or_rows, "or")

    like_rows = chunk_repo.search_content_like(
        like_pattern(query),
        region_id=scope.region_id,
        doc_ids=scope.visible_doc_ids,
        limit=limit,
    )
    if like_rows:
        return _to_hits(like_rows, "like")

    # 放宽后也为空时保留 AND 的少量命中，避免把已有结果丢掉。
    return _to_hits(and_rows, "and")
