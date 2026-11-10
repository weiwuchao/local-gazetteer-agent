"""RRF 融合：量纲、去重与确定性排序。"""

from __future__ import annotations

import pytest
from langchain_core.documents import Document

from app.rag.retrievers.rrf import fuse


def _doc(chunk_id: str, text: str = "片段", doc_id: str = "doc_1") -> Document:
    return Document(
        page_content=text,
        metadata={"chunk_id": chunk_id, "doc_id": doc_id},
    )


def test_single_path_rank_contribution() -> None:
    fused = fuse([_doc("c_1"), _doc("c_2")], [], rrf_k=60)

    assert [item.chunk_id for item in fused] == ["c_1", "c_2"]
    assert fused[0].rrf_score == pytest.approx(1 / 61)
    assert fused[1].rrf_score == pytest.approx(1 / 62)
    assert fused[0].vec_rank == 1
    assert fused[0].bm25_rank is None


def test_both_paths_accumulate_and_dedupe() -> None:
    fused = fuse([_doc("c_1", "向量原文")], [_doc("c_1", "全文原文")], rrf_k=60)

    assert len(fused) == 1
    assert fused[0].rrf_score == pytest.approx(2 / 61)
    assert fused[0].vec_rank == 1
    assert fused[0].bm25_rank == 1
    # 两路都命中时保留先到的原文（向量路 documents 存原文）。
    assert fused[0].text == "向量原文"


def test_weights_are_applied() -> None:
    fused = fuse([_doc("c_1")], [_doc("c_2")], rrf_k=60, vector_weight=2.0)

    assert fused[0].chunk_id == "c_1"
    assert fused[0].rrf_score == pytest.approx(2 / 61)


def test_tie_break_is_deterministic() -> None:
    fused = fuse([_doc("c_2")], [_doc("c_1")], rrf_k=60)

    assert [item.chunk_id for item in fused] == ["c_1", "c_2"]


def test_missing_chunk_id_is_an_error() -> None:
    bad = Document(page_content="x", metadata={"doc_id": "doc_1"})
    with pytest.raises(ValueError, match="chunk_id"):
        fuse([bad], [])


def test_negative_rrf_k_is_rejected() -> None:
    with pytest.raises(ValueError, match="rrf_k"):
        fuse([], [], rrf_k=-1)
