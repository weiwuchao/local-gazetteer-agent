"""可选依赖不可用时的统一异常与导入辅助。

设计依据：架构 D-7。本项目不做降级：缺 ``torch`` / ``chromadb`` / 模型权重时直接失败，
错误信息保持简短，修复办法看 ``backend/README.md``。
"""

from __future__ import annotations

import importlib
from types import ModuleType


class RagUnavailable(RuntimeError):
    """向量链路不可用（缺依赖 / 模型 / collection）。"""


def require_module(module: str) -> ModuleType:
    """导入可选依赖；失败转成 ``RagUnavailable``，原始异常作为 cause 保留。"""
    try:
        return importlib.import_module(module)
    except Exception as exc:  # ImportError 之外还可能是 OSError（DLL 缺失）
        raise RagUnavailable(f"缺少依赖 {module}：{type(exc).__name__}: {exc}") from exc
