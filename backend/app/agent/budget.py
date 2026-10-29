"""上下文 token 预算分配与截断（G-8，limits.context_tokens=8192）。"""

from __future__ import annotations


def allocate(chunks: list[object], context_tokens: int = 8192) -> list[object]:
    """按预算裁剪片段；保留引用完整性，宁少不截半句。"""
    raise NotImplementedError
