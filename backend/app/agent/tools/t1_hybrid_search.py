"""T1 混合检索：向量 + BM25 → RRF → rerank → 阈值

签名与线程约定见详细设计 3.10。同步函数，内部不再切线程。
"""

from __future__ import annotations


def run(*args, **kwargs):
    raise NotImplementedError
