"""FTS5 表达式与 LIKE 转义。"""

from __future__ import annotations

from app.rag.fts_store import like_pattern, or_match_expr, search
from app.rag.scope import SearchScope


def test_like_pattern_escapes_wildcards() -> None:
    assert like_pattern("50%_a\\b") == "%50\\%\\_a\\\\b%"


def test_or_match_expr_quotes_every_token() -> None:
    assert or_match_expr(["法门寺", 'a"b']) == '"法门寺" OR "a""b"'


def test_search_short_circuits_empty_scope() -> None:
    scope = SearchScope(region_id="rg_1", visible_doc_ids=(), index_version="idx")
    assert search("法门寺", ["法门寺"], scope=scope, limit=5) == []


def test_search_short_circuits_blank_tokens() -> None:
    scope = SearchScope(region_id="rg_1", visible_doc_ids=("doc_1",), index_version="idx")
    assert search("  ", ["", "  "], scope=scope, limit=5) == []
