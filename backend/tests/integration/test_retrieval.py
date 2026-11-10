"""双路召回、RRF、rerank 的集成链路（临时 SQLite + 假向量栈）。"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.text import sha256
from app.rag import fts_store, indexer
from app.rag.diagnostics import RagUnavailable
from app.rag.models import resolve_index_version
from app.rag.retrievers.bm25 import FtsRetriever
from app.rag.retrievers.hybrid import RetrievalSettings, hybrid_search
from app.rag.retrievers.rerank import select
from app.rag.retrievers.types import Candidate
from app.rag.retrievers.vector import VectorRetriever
from app.rag.scope import SearchScope
from app.repo import app_config_repo, chunk_repo, db, document_repo, region_repo

REGION_NAME = "扶风县"


@pytest.fixture()
def temp_db(tmp_path):
    db.shutdown()
    db.init(tmp_path / "app.db")
    app_config_repo.seed_defaults()
    try:
        yield tmp_path
    finally:
        db.shutdown()


def seed_document(content: str, *, title: str = "扶风县志") -> tuple[str, str]:
    region_id = region_repo.get_or_create(name=REGION_NAME)
    doc_id = document_repo.create_document(
        region_id=region_id,
        title=title,
        source_note="测试",
        dup_key=sha256(f"{title}:{content}"),
        created_by="test",
        status="published",
    )
    version_id, _ = document_repo.add_version(doc_id=doc_id, content=content)
    indexer.ingest_document(
        doc_id=doc_id,
        version_id=version_id,
        region_id=region_id,
        content=content,
        embed=False,
    )
    return region_id, doc_id


def make_scope(region_id: str, doc_ids: tuple[str, ...]) -> SearchScope:
    return SearchScope(
        region_id=region_id,
        visible_doc_ids=doc_ids,
        index_version=resolve_index_version(),
    )


class FakeEmbedder:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [1.0, 0.0, 0.0]


class FakeCollection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.where: dict[str, Any] | None = None
        self.n_results: int | None = None

    def query(
        self,
        *,
        query_embeddings: list[list[float]],
        n_results: int,
        where: dict[str, Any],
        include: list[str],
    ) -> dict[str, Any]:
        self.where = where
        self.n_results = n_results
        subset = self.rows[:n_results]
        return {
            "documents": [[row["content"] for row in subset]],
            "metadatas": [[row["metadata"] for row in subset]],
            "distances": [[row["distance"] for row in subset]],
        }


class RaisingCollection:
    def query(self, **kwargs: Any) -> dict[str, Any]:
        raise RagUnavailable("向量库不可用")


def vector_rows(doc_id: str, *, distance: float = 0.2) -> list[dict[str, Any]]:
    rows = chunk_repo.list_by_doc(doc_id)
    return [
        {
            "content": row["content"],
            "distance": distance,
            "metadata": {
                "chunk_id": row["chunk_id"],
                "doc_id": row["doc_id"],
                "region_id": row["region_id"],
                "index_version": row["index_version"],
                "ocr_flag": row["ocr_flag"],
            },
        }
        for row in rows
    ]


def test_vector_retriever_pushes_scope_and_drops_leaks(temp_db) -> None:
    region_id, doc_visible = seed_document("法门寺在县北二十里，唐时敕建。")
    _, doc_hidden = seed_document("美阳县古称美阳。", title="扶风县志·沿革")
    scope = make_scope(region_id, (doc_visible,))

    collection = FakeCollection(vector_rows(doc_visible) + vector_rows(doc_hidden))
    embedder = FakeEmbedder()
    docs = VectorRetriever(
        scope=scope,
        k=5,
        embed=embedder,
        collection=collection,
    ).invoke("法门寺")

    assert embedder.queries == ["法门寺"]
    assert collection.n_results == 5
    assert collection.where == {
        "$and": [
            {"region_id": {"$eq": region_id}},
            {"doc_id": {"$in": [doc_visible]}},
            {"index_version": {"$eq": scope.index_version}},
        ]
    }
    assert docs
    assert {doc.metadata["doc_id"] for doc in docs} == {doc_visible}
    assert all(doc.metadata["score"] == pytest.approx(0.8) for doc in docs)


def test_vector_retriever_sorts_by_distance_not_input_order(temp_db) -> None:
    region_id, doc_visible = seed_document("法门寺在县北二十里，唐时敕建。")
    scope = make_scope(region_id, (doc_visible,))
    rows = [
        {"chunk_id": chunk_id, "distance": distance}
        for chunk_id, distance in [("c0", 0.9), ("c1", 0.1), ("c2", 0.5), ("c3", 0.1)]
    ]
    collection = FakeCollection(
        [
            {
                "content": f"片段{index}",
                "distance": row["distance"],
                "metadata": {
                    "chunk_id": row["chunk_id"],
                    "doc_id": doc_visible,
                    "region_id": region_id,
                    "index_version": scope.index_version,
                    "ocr_flag": 0,
                },
            }
            for index, row in enumerate(rows)
        ]
    )

    docs = VectorRetriever(
        scope=scope,
        k=4,
        embed=FakeEmbedder(),
        collection=collection,
    ).invoke("法门寺")

    # 输入是乱序的，输出仍按 1 - distance 降序；同分按 chunk_id 保证可复现。
    assert [doc.metadata["chunk_id"] for doc in docs] == ["c1", "c3", "c2", "c0"]
    assert [doc.metadata["score"] for doc in docs] == pytest.approx([0.9, 0.9, 0.5, 0.1])


def test_fts_retriever_respects_scope_and_is_searchable(temp_db) -> None:
    region_id, doc_visible = seed_document("法门寺在县北二十里，唐时敕建。")
    _, doc_hidden = seed_document("法门寺在另一县。", title="外县县志")
    scope = make_scope(region_id, (doc_visible,))

    docs = FtsRetriever(scope=scope, k=5).invoke("法门寺")
    assert len(docs) == 1
    assert docs[0].metadata["doc_id"] == doc_visible
    assert docs[0].metadata["fts_fallback"] in {"and", "or"}

    db.get_write_queue().execute(
        "UPDATE chunk SET is_searchable = 0 WHERE doc_id = ?",
        (doc_visible,),
    )
    assert FtsRetriever(scope=scope, k=5).invoke("法门寺") == []

    # 外县片段不在 scope 内，即使命中也不能返回。
    scope_hidden = make_scope(region_id, (doc_hidden,))
    hidden_docs = FtsRetriever(scope=scope_hidden, k=5).invoke("法门寺")
    assert {doc.metadata["doc_id"] for doc in hidden_docs} == {doc_hidden}


def test_fts_falls_back_to_or_then_like(temp_db) -> None:
    region_id, doc_a = seed_document("法门寺在县北二十里。")
    _, doc_b = seed_document("真身宝塔高耸。", title="扶风县志·祠祀")

    scope = make_scope(region_id, (doc_a, doc_b))
    or_hits = fts_store.search(
        "法门寺 宝塔",
        ["法门寺", "宝塔"],
        scope=scope,
        limit=5,
    )
    assert or_hits
    assert {hit.chunk_id for hit in or_hits} == {
        row["chunk_id"] for row in chunk_repo.list_by_doc(doc_a) + chunk_repo.list_by_doc(doc_b)
    }
    assert {hit.fallback for hit in or_hits} == {"or"}

    like_hits = fts_store.search(
        "法门寺",
        ["完全不存在的词"],
        scope=make_scope(region_id, (doc_a,)),
        limit=5,
    )
    assert like_hits
    assert {hit.fallback for hit in like_hits} == {"like"}


def test_hybrid_search_fuses_reranks_and_filters(temp_db) -> None:
    region_id, doc_visible = seed_document("法门寺在县北二十里，唐时敕建。")
    _, doc_hidden = seed_document("美阳县古称美阳。", title="扶风县志·沿革")
    scope = make_scope(region_id, (doc_visible,))

    collection = FakeCollection(vector_rows(doc_visible) + vector_rows(doc_hidden))
    embedder = FakeEmbedder()

    def scorer(query: str, texts):
        from app.rag.rerank import RerankScore

        return [
            RerankScore(prob=0.9 if "法门寺" in text else 0.1, logit=2.0)
            for text in texts
        ]

    settings = RetrievalSettings(k_vec=5, k_bm25=5, rrf_k=60, top_n=3, threshold=0.62)
    result = hybrid_search(
        "法门寺",
        scope,
        settings=settings,
        embedder=embedder,
        collection=collection,
        scorer=scorer,
    )

    assert result.top1 == pytest.approx(0.9)
    assert result.hits
    assert {hit.doc_id for hit in result.hits} == {doc_visible}
    assert result.all_scores == [pytest.approx(0.9)]


def test_hybrid_search_returns_top_three_when_all_below_threshold(temp_db) -> None:
    region_id, doc_id = seed_document("法门寺在县北二十里。")
    scope = make_scope(region_id, (doc_id,))

    def low_scorer(query: str, texts):
        from app.rag.rerank import RerankScore

        return [RerankScore(prob=0.1, logit=-2.0) for _ in texts]

    result = hybrid_search(
        "法门寺",
        scope,
        settings=RetrievalSettings(k_vec=5, k_bm25=5, top_n=3, threshold=0.62),
        embedder=FakeEmbedder(),
        collection=FakeCollection(vector_rows(doc_id)),
        scorer=low_scorer,
    )

    assert result.top1 == pytest.approx(0.1)
    assert len(result.hits) == 1


def test_hybrid_search_empty_scope_short_circuits(temp_db, monkeypatch) -> None:
    region_id, _ = seed_document("法门寺在县北二十里。")

    def fail_if_loaded():
        raise AssertionError("空 scope 不应加载向量栈")

    monkeypatch.setattr(indexer, "get_stack", fail_if_loaded)
    result = hybrid_search("法门寺", make_scope(region_id, ()))

    assert result.hits == []
    assert result.top1 == 0.0
    assert result.all_scores == []


def test_hybrid_search_propagates_vector_failure(temp_db) -> None:
    region_id, doc_id = seed_document("法门寺在县北二十里。")
    with pytest.raises(RagUnavailable, match="向量库不可用"):
        hybrid_search(
            "法门寺",
            make_scope(region_id, (doc_id,)),
            embedder=FakeEmbedder(),
            collection=RaisingCollection(),
        )


def test_hybrid_search_can_answer_from_bm25_only(temp_db) -> None:
    region_id, doc_id = seed_document("法门寺在县北二十里。")

    def scorer(query: str, texts):
        from app.rag.rerank import RerankScore

        return [RerankScore(prob=0.8, logit=1.4) for _ in texts]

    result = hybrid_search(
        "法门寺",
        make_scope(region_id, (doc_id,)),
        settings=RetrievalSettings(k_vec=5, k_bm25=5, top_n=3, threshold=0.62),
        embedder=FakeEmbedder(),
        collection=FakeCollection([]),
        scorer=scorer,
    )

    assert result.hits
    assert result.hits[0].doc_id == doc_id


def test_select_is_the_only_threshold_point() -> None:
    candidates = [
        Candidate(chunk_id="c_1", doc_id="doc_1", text="a", final_score=0.9),
        Candidate(chunk_id="c_2", doc_id="doc_1", text="b", final_score=0.7),
    ]
    assert [item.chunk_id for item in select(candidates, top_n=1, threshold=0.8)] == ["c_1"]
