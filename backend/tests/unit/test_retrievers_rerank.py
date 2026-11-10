"""rerank 打分与阈值选择。"""

from __future__ import annotations

import math

import pytest

from app.rag.diagnostics import RagUnavailable
from app.rag.rerank import RerankScore, _sigmoid, score_pairs
from app.rag.retrievers.rerank import score_candidates, select
from app.rag.retrievers.types import Candidate


class FakeReranker:
    def __init__(self, logits: list[float]) -> None:
        self.logits = logits
        self.calls: list[list[tuple[str, str]]] = []

    def predict(self, pairs, *, batch_size: int, show_progress_bar: bool):
        self.calls.append(list(pairs))
        return self.logits


def _candidate(chunk_id: str, text: str = "原文") -> Candidate:
    return Candidate(chunk_id=chunk_id, doc_id="doc_1", text=text)


def test_sigmoid_is_stable_and_bounded() -> None:
    assert _sigmoid(0) == 0.5
    assert _sigmoid(3) == pytest.approx(1 / (1 + math.exp(-3)))
    assert 0 <= _sigmoid(-1000) <= 1
    assert 0 <= _sigmoid(1000) <= 1


def test_score_pairs_applies_sigmoid_and_returns_logits() -> None:
    reranker = FakeReranker([-3.0, 0.0, 3.0])

    scores = score_pairs("法门寺", ["甲", "乙", "丙"], reranker=reranker)

    assert [item.logit for item in scores] == [-3.0, 0.0, 3.0]
    assert scores[1].prob == 0.5
    assert scores[0].prob < 0.5 < scores[2].prob
    assert reranker.calls == [[("法门寺", "甲"), ("法门寺", "乙"), ("法门寺", "丙")]]


def test_score_pairs_empty_does_not_call_model() -> None:
    reranker = FakeReranker([])
    assert score_pairs("q", [], reranker=reranker) == []
    assert reranker.calls == []


def test_score_pairs_rejects_length_mismatch() -> None:
    reranker = FakeReranker([0.1])
    with pytest.raises(RagUnavailable, match="期望 2 条"):
        score_pairs("q", ["甲", "乙"], reranker=reranker)


def test_score_candidates_normalizes_and_sorts() -> None:
    candidates = [_candidate("c_1", "甲乙"), _candidate("c_2", "丙丁")]
    seen: list[str] = []

    def scorer(query: str, texts):
        seen.extend(texts)
        return [
            RerankScore(prob=0.3, logit=-1.0),
            RerankScore(prob=0.8, logit=2.0),
        ]

    ranked = score_candidates("法门寺", candidates, scorer=scorer)

    assert seen == ["甲乙", "丙丁"]
    assert [item.chunk_id for item in ranked] == ["c_2", "c_1"]
    assert ranked[0].final_score == 0.8
    assert ranked[0].rerank_logit == 2.0


def test_select_applies_threshold_then_top_n() -> None:
    candidates = [
        _candidate("c_1"),
        _candidate("c_2"),
        _candidate("c_3"),
    ]
    for item, score in zip(candidates, [0.9, 0.8, 0.7], strict=True):
        item.final_score = score

    picked = select(candidates, top_n=2, threshold=0.75)
    assert [item.chunk_id for item in picked] == ["c_1", "c_2"]


def test_select_falls_back_to_top_three_when_all_below_threshold() -> None:
    candidates = [_candidate(f"c_{index}") for index in range(5)]
    for index, item in enumerate(candidates):
        item.final_score = 0.1 * (5 - index)

    picked = select(candidates, top_n=8, threshold=0.62)
    assert [item.chunk_id for item in picked] == ["c_0", "c_1", "c_2"]


def test_select_empty() -> None:
    assert select([], top_n=8, threshold=0.62) == []
