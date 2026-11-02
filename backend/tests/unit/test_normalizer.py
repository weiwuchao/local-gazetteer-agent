"""FTS5 表达式与 rerank 输入清洗。"""

from __future__ import annotations

from app.rag.normalizer import (
    build_match_expr,
    normalize_for_rerank,
    quote_fts,
    tokenize,
)


def test_quote_fts_escapes_double_quote() -> None:
    assert quote_fts('a"b') == '"a""b"'


def test_build_match_expr_three_tokens() -> None:
    assert build_match_expr(["县", "志", "法门寺"]) == '"县" AND "志" AND "法门寺"'


def test_build_match_expr_many_tokens() -> None:
    expr = build_match_expr(["a", "b", "c", "d", "e"])
    assert expr == '("a" AND "b" AND "c") OR ("a" AND "b" AND "c" AND ("d" OR "e"))'


def test_tokenize_keeps_original_script() -> None:
    seg = tokenize("縣北有法門寺")
    assert "縣" in seg          # 不做繁简转换
    assert "县" not in seg
    assert " " in seg           # 已分词


def test_normalize_for_rerank_keeps_traditional() -> None:
    assert normalize_for_rerank("  縣北　有法門寺。 \n") == "縣北 有法門寺。"
