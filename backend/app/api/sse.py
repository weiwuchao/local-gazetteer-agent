"""SSE 事件模型、编码、心跳、Last-Event-ID 重放。

设计依据：详细设计 2.3（SSE 协议）。事件表见文档；POST + fetch 流式读取（D-3）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator


async def encode_event(event: str, data: dict, event_id: str | None = None) -> str:
    """编码一帧：event / id / data(JSON) / 空行。"""
    raise NotImplementedError


async def heartbeat(interval_s: float = 15.0) -> AsyncIterator[str]:
    """空闲时发送注释帧，防止代理超时断开。"""
    raise NotImplementedError


async def replay(message_id: str, last_event_id: str | None) -> AsyncIterator[str]:
    """断流重放：从内存缓冲取 last_event_id 之后的事件（FR-C02）。"""
    raise NotImplementedError
