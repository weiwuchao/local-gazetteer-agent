"""检索链路的内部载体：候选片段与最终结果。

两路检索器统一返回 LangChain ``Document``（见 4.2 / 4.3）；RRF 之后转成
``Candidate``，rerank 在它上面写 ``final_score``。``RetrievalResult`` 是 T1
对外的返回形状（4.4）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Candidate:
    chunk_id: str
    doc_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    rrf_score: float = 0.0
    vec_rank: int | None = None
    bm25_rank: int | None = None
    final_score: float = 0.0
    rerank_logit: float | None = None


@dataclass(slots=True)
class RetrievalResult:
    hits: list[Candidate]
    top1: float
    all_scores: list[float]
    top10: list[dict[str, Any]]
