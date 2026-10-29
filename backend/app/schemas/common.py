"""通用 DTO：错误体、分页、任务回执。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """所有请求 DTO 的基类：拒绝伪造字段（AT-02）。"""

    model_config = ConfigDict(extra="forbid")


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    detail: dict[str, Any] = {}


class TaskAccepted(BaseModel):
    task_id: str
    status: str = "pending"
