"""混合路：双路召回 + RRF(top 40) + rerank(top_n=8)。

设计依据：详细设计 3.10、4.2–4.4。

两路召回在一个线程内顺序执行（3.10）：向量路真正耗时的是 ``embed_query`` 一次，
BM25 的 FTS 查询通常 <10ms；在协程层 gather 只会让两个线程抢同一批 CPU 核。
任一路失败直接向上抛，不降级为单路。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.rag import indexer
from app.rag.retrievers.bm25 import FtsRetriever
from app.rag.retrievers.rerank import Scorer, score_candidates, select
from app.rag.retrievers.rrf import fuse
from app.rag.retrievers.types import RetrievalResult
from app.rag.retrievers.vector import VectorRetriever
from app.rag.scope import SearchScope
from app.repo import app_config_repo

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetrievalSettings:
    k_vec: int = 20
    k_bm25: int = 20
    rrf_k: int = 60
    top_n: int = 8
    threshold: float = 0.62

    @classmethod
    def load(cls, top_k: int | None = None) -> RetrievalSettings:
        if top_k is not None:
            k_vec = max(1, top_k)
            k_bm25 = max(1, top_k)
        else:
            k_vec = max(1, app_config_repo.get_int("retrieval.top_k_vec", 20))
            k_bm25 = max(1, app_config_repo.get_int("retrieval.top_k_bm25", 20))
        return cls(
            k_vec=k_vec,
            k_bm25=k_bm25,
            rrf_k=max(0, app_config_repo.get_int("retrieval.rrf_k", 60)),
            top_n=max(1, app_config_repo.get_int("retrieval.rerank_top_n", 8)),
            threshold=app_config_repo.get_float("retrieval.score_threshold", 0.62),
        )

    @property
    def candidate_limit(self) -> int:
        return max(2 * max(self.k_vec, self.k_bm25), 40)


def hybrid_search(
    query: str,
    scope: SearchScope,
    *,
    top_k: int | None = None,
    settings: RetrievalSettings | None = None,
    embedder: object | None = None,
    collection: object | None = None,
    scorer: Scorer | None = None,
) -> RetrievalResult:
    """双路召回 → RRF → rerank → 阈值筛选。"""
    if not scope.visible_doc_ids:
        # 空 scope 在加载模型之前短路，绝不查全库（4.2）。
        return RetrievalResult(hits=[], top1=0.0, all_scores=[], top10=[])

    cfg = settings or RetrievalSettings.load(top_k)

    if embedder is None or collection is None:
        stack = indexer.get_stack()
        embedder = embedder if embedder is not None else stack.embeddings
        collection = collection if collection is not None else stack.collection

    vector_docs = VectorRetriever(
        scope=scope,
        k=cfg.k_vec,
        embed=embedder,
        collection=collection,
    ).invoke(query)
    bm25_docs = FtsRetriever(scope=scope, k=cfg.k_bm25).invoke(query)

    fused = fuse(vector_docs, bm25_docs, rrf_k=cfg.rrf_k)[: cfg.candidate_limit]
    ranked = score_candidates(query, fused, scorer=scorer)
    picked = select(ranked, top_n=cfg.top_n, threshold=cfg.threshold)

    top1 = ranked[0].final_score if ranked else 0.0
    log.info(
        "hybrid_search scope_docs=%d vec=%d bm25=%d fused=%d picked=%d top1=%.3f",
        len(scope.visible_doc_ids),
        len(vector_docs),
        len(bm25_docs),
        len(fused),
        len(picked),
        top1,
    )
    return RetrievalResult(
        hits=picked,
        top1=top1,
        all_scores=[item.final_score for item in ranked],
        top10=[
            {"chunk_id": item.chunk_id, "score": round(item.final_score, 3)}
            for item in ranked[:10]
        ],
    )
