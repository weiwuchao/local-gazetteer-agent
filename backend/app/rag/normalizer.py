"""文本处理：FTS5 预分词、MATCH 表达式、rerank 输入清洗。

**不做繁简转换**：原文与检索都用原字（本项目不引入 opencc）。

两个口径不要混（详细设计 4.7.2）：
- ``content``       原字原文，落 chunk.content，进 Chroma documents 与引文；
- ``content_seg``   原文 + jieba 空格分词，只给 FTS5。
"""

from __future__ import annotations

import re
import unicodedata

from app.rag.diagnostics import require_module

# jieba 首次分词会构建前缀树，进程内只初始化一次；只读对象，单进程多线程安全。
_jieba = None

_WS = re.compile(r"\s+")


def _jieba_module():
    global _jieba
    if _jieba is None:
        _jieba = require_module("jieba")
        _jieba.setLogLevel(20)  # INFO 以下不打印构建前缀树的日志
    return _jieba


def tokenize(text: str | None) -> str:
    """FTS5 写入侧：jieba 搜索模式分词 → 空格连接（不转简）。"""
    if not text:
        return ""
    jieba = _jieba_module()
    return " ".join(token for token in jieba.cut_for_search(text) if token.strip())


def query_tokens(query: str) -> list[str]:
    """FTS5 查询侧：与写入侧走同一套分词。"""
    return [token for token in tokenize(query).split(" ") if token]


def quote_fts(token: str) -> str:
    """把单个 token 包成 FTS5 字符串字面量，内部双引号转义为两个双引号。"""
    return '"' + token.replace('"', '""') + '"'


def build_match_expr(tokens: list[str]) -> str:
    """MATCH 表达式三级回退的第一级（详细设计 4.3.2）。

    <=3 个 token 全 AND；更多时"前三个 AND，其余 OR"，兼顾精确与召回。
    命中不足时的纯 OR / LIKE 回退由检索器负责，这里只生成表达式。
    """
    tokens = [token for token in tokens if token.strip()]
    if not tokens:
        return ""
    if len(tokens) <= 3:
        return " AND ".join(quote_fts(token) for token in tokens)
    head = " AND ".join(quote_fts(token) for token in tokens[:3])
    tail = " OR ".join(quote_fts(token) for token in tokens[3:])
    return f"({head}) OR ({head} AND ({tail}))"


def normalize_for_rerank(text: str) -> str:
    """重排输入清洗：只做 NFKC 全角半角归一与空白压缩，不做繁简转换。"""
    normalized = unicodedata.normalize("NFKC", text or "")
    return _WS.sub(" ", normalized).strip()


def ensure_text_stack() -> None:
    """启动自检：缺 jieba 时尽早抛出（不降级）。"""
    tokenize("縣志")
