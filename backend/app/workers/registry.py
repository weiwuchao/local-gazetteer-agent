"""task.kind → handler 的注册表。"""

from __future__ import annotations

from typing import Callable

HANDLERS: dict[str, Callable[..., None]] = {}


def register(kind: str) -> Callable:
    def deco(fn: Callable[..., None]) -> Callable[..., None]:
        HANDLERS[kind] = fn
        return fn

    return deco
