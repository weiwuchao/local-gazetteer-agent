"""分页参数与响应包装。?page=1&size=20（size ≤ 100），或游标 ?before_seq=。"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageParams(BaseModel):
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=100)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    size: int
