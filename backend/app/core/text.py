"""文本工具：截断（不截半句）、sha256、token 估算、quote 定位。"""

from __future__ import annotations

import hashlib
import re

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_SENTENCE_END = "。！？!?；;…"


def sha256(text: str) -> str:
    """正文指纹：用于 document.dup_key 与幂等判断。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def truncate(text: str, limit: int) -> str:
    """截断到 ``limit`` 字符以内，尽量停在句末，不截半句。

    在 ``[limit // 2, limit]`` 区间内找最后一个句末标点；找不到才硬切。
    """
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    floor = max(0, limit // 2)
    cut = max(text.rfind(ch, floor, limit) for ch in _SENTENCE_END)
    if cut < floor:
        return text[:limit].rstrip() + "…"
    return text[: cut + 1]


def count_tokens(text: str) -> int:
    """估算 token 数：汉字按 1 个、非汉字按 4 字符 1 个（详细设计 4.6）。

    这是预算用的保守估算，不是 tokenizer 精确值；组装上下文时直接求和即可。
    """
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return cjk + (other + 3) // 4
