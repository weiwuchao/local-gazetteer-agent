"""bge-reranker-v2-m3：logits 必须过 sigmoid

设计依据：详细设计 4.7.4。

``CrossEncoder.predict()`` 返回的是 logits（约 −10 ~ +10），不是 0–1 概率。
不转 sigmoid，``final_score`` 会是 −3.2 之类的值，与 0.62 比较恒为不足，系统会
对所有问题稳定拒答，而日志里看分数确实"低于阈值"，非常难排查。

rerank 是必选步骤：``llm.rerank.model`` 为空或 ``none`` 时加载阶段直接
``RagUnavailable``，不存在"关闭 rerank 退回余弦"的分支。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.rag.diagnostics import RagUnavailable
from app.rag.models import get_models


@dataclass(frozen=True, slots=True)
class RerankScore:
    prob: float
    logit: float


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp = math.exp(value)
    return exp / (1.0 + exp)


def score_pairs(
    query: str,
    texts: Sequence[str],
    *,
    reranker: Any = None,
    batch_size: int = 16,
) -> list[RerankScore]:
    """对 (query, text) 对打分；返回 sigmoid 后的概率与原始 logit。"""
    if not texts:
        return []

    active = reranker
    if active is None:
        models = get_models(with_reranker=True)
        active = models.reranker
        if active is None:
            raise RagUnavailable("rerank 模型不可用；llm.rerank.model 不能为空或 none")

    pairs = [(query, text) for text in texts]
    logits = active.predict(
        pairs,
        batch_size=batch_size,
        show_progress_bar=False,
    )
    values = [float(value) for value in logits]
    if len(values) != len(texts):
        raise RagUnavailable(f"rerank 返回 {len(values)} 条，期望 {len(texts)} 条")

    return [RerankScore(prob=_sigmoid(value), logit=value) for value in values]
