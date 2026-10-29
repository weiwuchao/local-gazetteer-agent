"""auth_refresh_token 表：刷新令牌轮换与吊销。

约束：本模块是 SQL 的唯一出口；软删条件必须内置（deleted_at IS NULL）。
"""

from __future__ import annotations

# TODO: 实现该表的 CRUD / 查询方法
