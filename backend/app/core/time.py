"""时间统一 TEXT ISO-8601 UTC 毫秒（D-2）。

存储：2026-10-29T07:12:33.412Z；展示层转 +08:00。
"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def to_display(value: str) -> str:
    """UTC ISO → +08:00 展示串。"""
    raise NotImplementedError
