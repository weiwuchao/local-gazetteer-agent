"""会话列表/新建/重命名/软删/消息分页（5 个端点）。

端点清单见详细设计 2.2；本文件目前只有路由骨架，处理函数待实现。
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

# TODO: 按详细设计 2.2 注册端点（鉴权依赖、DTO、错误映射、异步 202）
