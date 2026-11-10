"""T1 工具边界：只做参数透传与 tool_call 打点。"""

from __future__ import annotations

from app.agent.tools import t1_hybrid_search
from app.rag.retrievers.types import Candidate, RetrievalResult
from app.rag.scope import SearchScope


class FakeSpan:
    def __init__(self) -> None:
        self.events: list[tuple[tuple, dict]] = []

    def event(self, *args, **kwargs) -> None:
        self.events.append((args, kwargs))


def test_t1_delegates_and_emits_tool_call(monkeypatch) -> None:
    scope = SearchScope(
        region_id="rg_1",
        visible_doc_ids=("doc_1",),
        index_version="idx",
    )
    result = RetrievalResult(
        hits=[Candidate(chunk_id="c_1", doc_id="doc_1", text="法门寺", final_score=0.8)],
        top1=0.8,
        all_scores=[0.8],
        top10=[{"chunk_id": "c_1", "score": 0.8}],
    )
    calls: list[tuple[str, SearchScope, int | None]] = []

    def fake_hybrid_search(query: str, active_scope: SearchScope, *, top_k=None):
        calls.append((query, active_scope, top_k))
        return result

    monkeypatch.setattr(t1_hybrid_search, "hybrid_search", fake_hybrid_search)
    span = FakeSpan()

    actual = t1_hybrid_search.t1_hybrid_search(
        q="法门寺",
        scope=scope,
        top_k=7,
        span=span,
    )

    assert actual is result
    assert calls == [("法门寺", scope, 7)]
    assert span.events
    args, kwargs = span.events[0]
    assert args == ("tool_call",)
    assert kwargs["tool_name"] == "hybrid_search"
    assert kwargs["result_count"] == 1
    assert kwargs["top_score"] == 0.8
