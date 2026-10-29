"""AppError 层级 → HTTP 状态码 / 错误码（EX-01..EX-25）。

设计依据：架构文档 6.4（错误码分层）、详细设计 2.4。
错误体：{"code": "EX-07", "message": "...", "request_id": "...", "detail": {}}
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    code = "EX-99"
    status_code = 500
    message = "internal error"

    def __init__(self, message: str | None = None, detail: dict | None = None) -> None:
        self.message = message or self.message
        self.detail = detail or {}
        super().__init__(self.message)


class AuthError(AppError):
    code = "EX-01"
    status_code = 401


class ForbiddenError(AppError):
    code = "EX-02"
    status_code = 403


class NotFoundError(AppError):
    code = "EX-03"
    status_code = 404


class ConflictError(AppError):
    code = "EX-11"
    status_code = 409


class ValidationError(AppError):
    code = "EX-20"
    status_code = 422


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "message": exc.message,
                "request_id": None,
                "detail": exc.detail,
            },
        )
