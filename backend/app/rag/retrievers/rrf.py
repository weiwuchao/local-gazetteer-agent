"""RRF 融合：rrf_k=60，分数量纲 ≠ 相关度（D-5）。

设计依据：详细设计 4.4。

量纲必须记住：
  rrf_k = 60 时：
    单路 rank1 贡献 = 1/61  ≈ 0.0164
    两路都 rank1     = 2/61  ≈ 0.0328   ← 上限
  因此 0.62 这类阈值绝不可能作用于 rrf_score。
  若有人把 threshold 改成和 rrf 比较，检索会"永远不足"→ 全部拒答，
  而且日志里看不出错，因为分数确实在 0.03 附近"低于阈值"。
"""

from __future__ import annotations

from collections.abc import Sequence

from langchain_core.documents import Document

from app.rag.retrievers.types import Candidate


def _rank(candidate: Candidate) -> int:
    ranks = [rank for rank in (candidate.vec_rank, candidate.bm25_rank) if rank is not None]
    return min(ranks) if ranks else 1_000_000


def _sort_key(candidate: Candidate) -> tuple[float, int, str]:
    # 同分时先看哪一路排得更靠前，最后用 chunk_id 保证结果确定。
    return (-candidate.rrf_score, _rank(candidate), candidate.chunk_id)


def fuse(
    vector_docs: Sequence[Document],
    bm25_docs: Sequence[Document],
    *,
    rrf_k: int = 60,
    vector_weight: float = 1.0,
    bm25_weight: float = 1.0,
) -> list[Candidate]:
    """两路召回按 ``Σ weight/(k+rank)`` 融合，按 chunk_id 去重。"""
    if rrf_k < 0:
        raise ValueError("rrf_k 不能为负数")

    fused: dict[str, Candidate] = {}
    for docs, weight, tag in (
        (vector_docs, vector_weight, "vec"),
        (bm25_docs, bm25_weight, "bm25"),
    ):
        for rank, doc in enumerate(docs, 1):
            chunk_id = doc.metadata.get("chunk_id")
            if not chunk_id:
                raise ValueError("检索结果缺少 metadata['chunk_id']")

            candidate = fused.get(chunk_id)
            if candidate is None:
                candidate = Candidate(
                    chunk_id=chunk_id,
                    doc_id=doc.metadata.get("doc_id", ""),
                    text=doc.page_content,
                    metadata=dict(doc.metadata),
                )
                fused[chunk_id] = candidate

            candidate.rrf_score += weight / (rrf_k + rank)
            if tag == "vec":
                candidate.vec_rank = rank
            else:
                candidate.bm25_rank = rank

    return sorted(fused.values(), key=_sort_key)
