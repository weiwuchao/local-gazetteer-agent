"""领域错误码与异常（与 api/errors.py 的 HTTP 映射分离）。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DomainError(Exception):
    code: str
    message: str
    detail: dict | None = None
