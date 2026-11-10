"""T1 混合检索：向量 + BM25 → RRF → rerank → 阈值

签名与线程约定见详细设计 3.10。同步函数，内部不再切线程；两路召回任一路失败
直接向上抛，不降级为单路。
"""

from __future__ import annotations

from typing import Any

from app.rag.retrievers.hybrid import RetrievalResult, hybrid_search
from app.rag.scope import SearchScope


def t1_hybrid_search(
        *,
        q: str,
        scope: SearchScope,
        top_k: int | None = None,
        span: Any = None,
) -> RetrievalResult:
    result = hybrid_search(q, scope, top_k=top_k)
    if span is not None and hasattr(span, "event"):
        span.event(
            "tool_call",
            tool_name="hybrid_search",
            params_digest=f"q={q[:60]} region={scope.region_id} top_k={top_k}",
            result_count=len(result.hits),
            top_score=result.top1,
            detail={"hit_chunk_ids": result.top10},
        )
    return result
