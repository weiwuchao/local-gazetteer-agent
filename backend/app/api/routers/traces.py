"""轨迹列表/详情/转评测样本（3 个端点，admin）。

端点清单见详细设计 2.2；本文件目前只有路由骨架，处理函数待实现。
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/admin/traces", tags=["traces"])

# TODO: 按详细设计 2.2 注册端点（鉴权依赖、DTO、错误映射、异步 202）
