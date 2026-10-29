"""Prompt 模板 registry：版本号 + sha256，不存原文。"""

from __future__ import annotations

from pathlib import Path

PROMPT_DIR = Path(__file__).parent


def load(name: str, version: str) -> str:
    """加载 {name}_{version}.jinja2，并校验 registry 中的 hash。"""
    raise NotImplementedError
