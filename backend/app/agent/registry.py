"""工具注册与预算：≤6 次调用、25s 总超时。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ToolBudget:
    max_calls: int = 6
    timeout_ms: int = 25_000
    calls: int = 0

    def consume(self) -> None:
        if self.calls >= self.max_calls:
            raise RuntimeError("tool budget exhausted")
        self.calls += 1


@dataclass(slots=True)
class ToolRegistry:
    _tools: dict[str, Callable[..., Any]] = field(default_factory=dict)

    def register(self, name: str, fn: Callable[..., Any]) -> None:
        self._tools[name] = fn

    def get(self, name: str) -> Callable[..., Any]:
        return self._tools[name]


registry = ToolRegistry()
