"""单节重试（attempt 幂等键）。"""

from __future__ import annotations


def handle(task_id: str) -> None:
    raise NotImplementedError
