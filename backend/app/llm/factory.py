"""模型工厂：init_chat_model + with_fallbacks；embedding / rerank 单例。

设计依据：详细设计 1.3、4.7。
"""

from __future__ import annotations


def get_chat_model(config: dict | None = None):
    raise NotImplementedError


def get_embedder():
    raise NotImplementedError


def get_reranker():
    raise NotImplementedError
