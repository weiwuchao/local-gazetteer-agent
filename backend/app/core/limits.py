"""限额读取（app_config）：日 token 预算、并发、上传大小。"""

from __future__ import annotations


def daily_token_budget() -> int:
    raise NotImplementedError
