"""cross-encoder 重排：0–1 分，阈值唯一作用点。

设计依据：详细设计 4.4、4.7.4。

``score_candidates`` 只负责把 rerank 概率写进 ``final_score`` 并排序；
``select`` 是阈值唯一作用点。两处都只比较 rerank 分，绝不碰 RRF 分（D-5）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from app.rag.normalizer import normalize_for_rerank
from app.rag.rerank import RerankScore, score_pairs
from app.rag.retrievers.types import Candidate

Scorer = Callable[[str, Sequence[str]], list[RerankScore]]


def score_candidates(
    query: str,
    candidates: Sequence[Candidate],
    *,
    scorer: Scorer | None = None,
) -> list[Candidate]:
    """给候选写 ``final_score`` / ``rerank_logit``，按分数降序返回。"""
    if not candidates:
        return []

    score = scorer or score_pairs
    texts = [normalize_for_rerank(candidate.text) for candidate in candidates]
    scores = score(query, texts)
    if len(scores) != len(candidates):
        raise ValueError(f"rerank 返回 {len(scores)} 条，期望 {len(candidates)} 条")

    for candidate, score_item in zip(candidates, scores, strict=True):
        candidate.final_score = score_item.prob
        candidate.rerank_logit = score_item.logit

    return sorted(candidates, key=lambda item: (-item.final_score, item.chunk_id))


def select(
    candidates: Sequence[Candidate],
    *,
    top_n: int,
    threshold: float,
) -> list[Candidate]:
    """按 rerank 分筛选；全低于阈值时返回前 3 条，交给 DR-09 判拒答。"""
    if not candidates:
        return []

    limit = max(1, top_n)
    picked = [item for item in candidates if item.final_score >= threshold][:limit]
    if picked:
        return picked
    return list(candidates[: min(3, len(candidates))])
