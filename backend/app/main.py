"""应用入口：创建 app、注册路由/中间件/异常映射，lifespan 启停 worker。

设计依据：架构文档 2.5（运行时视图）、3.1（包结构）、详细设计 2.2（端点总表）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.routers import (
    admin,
    auth,
    chat,
    chunks,
    corpus,
    eval as eval_router,
    polish,
    regions,
    reports,
    sessions,
    tasks,
    traces,
)
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.repo import db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动/停止顺序（骨架，实现见各模块）。

    启动：加载配置 → 迁移 DB → 校验 embedding 维度（D-7 fail fast）
          → 预热模型 → 启动 worker 线程 → 注册信号处理
    停止：停止领取新任务 → 等待在途任务/租约释放 → 落盘轨迹 → 关连接
    """
    settings = get_settings()
    setup_logging(settings.log_level)

    # 迁移必须在写线程/worker 之前完成；停止时先停写线程再退出。
    db.init()
    try:
        # TODO: 校验 embedding 维度 / 预热模型 / workers.worker.start()
        yield
    finally:
        # TODO: workers.worker.stop()
        db.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="中国县市 AI 智能体",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.env != "prod" else None,
        openapi_url="/api/openapi.json" if settings.env != "prod" else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)

    for router in (
        auth.router,
        regions.router,
        corpus.router,
        polish.router,
        chat.router,
        sessions.router,
        chunks.router,
        reports.router,
        traces.router,
        admin.router,
        eval_router.router,
        tasks.router,
    ):
        app.include_router(router)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
