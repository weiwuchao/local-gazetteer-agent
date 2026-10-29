"""注册/登录/刷新/登出/我的身份/改密（6 个端点）。

端点清单见详细设计 2.2；本文件目前只有路由骨架，处理函数待实现。
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/auth", tags=["auth"])

# TODO: 按详细设计 2.2 注册端点（鉴权依赖、DTO、错误映射、异步 202）
