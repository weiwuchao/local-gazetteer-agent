"""worker 主循环：领取 → 租约 → 执行 → 心跳 → 重试/死信；优雅停机。"""

from __future__ import annotations


def start() -> None:
    raise NotImplementedError


def stop(timeout_s: float = 10.0) -> None:
    raise NotImplementedError
