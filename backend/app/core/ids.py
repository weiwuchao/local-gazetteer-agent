"""ULID 生成：tr_/sp_/ev_/msg_/c_ 等业务前缀。"""

from __future__ import annotations

import secrets


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"
