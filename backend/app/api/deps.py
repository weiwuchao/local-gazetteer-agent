"""FastAPI 依赖：principal / 角色 / 归属 / 幂等键。

设计依据：详细设计 2.1（统一约定）。身份信息只能来自 JWT，请求 DTO 不得含 role/user_id。
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    username: str
    role: str  # admin | user


async def get_principal(authorization: str | None = Header(default=None)) -> Principal:
    """JWT → Principal；token_version 比对失败即 401。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing token")
    # TODO: 校验签名/过期/token_version，返回 Principal
    raise NotImplementedError


async def require_admin(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin only")
    return principal


async def require_owner(*args, **kwargs) -> None:
    """资源归属校验：非本人一律 404（不是 403），避免探测。"""
    raise NotImplementedError


async def idempotency_key(idempotency_key: str | None = Header(default=None)) -> str | None:
    return idempotency_key
