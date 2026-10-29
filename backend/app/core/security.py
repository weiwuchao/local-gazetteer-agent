"""argon2id 密码哈希、JWT 签发/校验、token_version 失效。"""

from __future__ import annotations


def hash_password(raw: str) -> str:
    raise NotImplementedError


def verify_password(raw: str, hashed: str) -> bool:
    raise NotImplementedError


def encode_access(user_id: str, role: str, token_version: int) -> str:
    raise NotImplementedError


def decode_token(token: str) -> dict:
    raise NotImplementedError
