"""文本工具：截断（不截半句）、sha256、token 估算、quote 定位。"""

from __future__ import annotations


def sha256(text: str) -> str:
    raise NotImplementedError


def truncate(text: str, limit: int) -> str:
    raise NotImplementedError
