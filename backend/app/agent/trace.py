"""TraceCollector、@stage 装饰器、digest 生成、体积截断。

设计依据：架构文档 5.4.6、详细设计 G-7。轨迹只 add span/event，不写库。
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator


class TraceCollector:
    def __init__(self, trace_id: str) -> None:
        self.trace_id = trace_id
        self.spans: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []

    def add_span(self, **kwargs: Any) -> None:
        self.spans.append(kwargs)

    def add_event(self, **kwargs: Any) -> None:
        self.events.append(kwargs)

    def digest(self, payload: Any, limit: int = 512) -> str:
        raise NotImplementedError


@contextmanager
def stage(name: str, seq: int, budget_ms: int) -> Iterator[Any]:
    """阶段 span 上下文；超预算只记录不抛错。"""
    raise NotImplementedError
