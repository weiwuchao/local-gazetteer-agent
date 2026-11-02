"""Chroma 适配层测试；未安装 chromadb 时跳过。"""

from __future__ import annotations

import pytest

pytest.importorskip("chromadb")

from app.rag import chroma_store  # noqa: E402

VERSION = "idx__fake__4__c400__o0__v1"


def test_collection_roundtrip(chroma_client) -> None:
    client = chroma_client
    collection = chroma_store.get_or_create_collection(VERSION, client=client)
    assert chroma_store.collection_name(VERSION) == f"chunks__{VERSION}"
    assert chroma_store.probe_dimension(collection) is None

    collection.upsert(
        ids=["c_1", "c_2"],
        embeddings=[[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]],
        documents=["縣北有法門寺。", "城周九里。"],
        metadatas=[
            {
                "chunk_id": "c_1",
                "doc_id": "doc_1",
                "region_id": "rg_1",
                "index_version": VERSION,
                "ocr_flag": 0,
            },
            {
                "chunk_id": "c_2",
                "doc_id": "doc_2",
                "region_id": "rg_1",
                "index_version": VERSION,
                "ocr_flag": 0,
            },
        ],
    )
    assert collection.count() == 2
    assert chroma_store.probe_dimension(collection) == 4

    where = chroma_store.build_where(
        region_id="rg_1",
        visible_doc_ids=("doc_1",),
        index_version=VERSION,
    )
    result = collection.query(
        query_embeddings=[[1.0, 0.0, 0.0, 0.0]],
        n_results=5,
        where=where,
        include=["metadatas", "distances", "documents"],
    )
    assert result["ids"][0] == ["c_1"]
    assert result["documents"][0] == ["縣北有法門寺。"]

    collection.delete(where={"doc_id": {"$eq": "doc_1"}})
    assert collection.count() == 1


def test_dimension_guard(chroma_client) -> None:
    collection = chroma_store.get_or_create_collection(VERSION, client=chroma_client)
    collection.upsert(
        ids=["c_1"],
        embeddings=[[1.0, 0.0, 0.0, 0.0]],
        documents=["原文"],
        metadatas=[
            {
                "chunk_id": "c_1",
                "doc_id": "doc_1",
                "region_id": "rg_1",
                "index_version": VERSION,
                "ocr_flag": 0,
            }
        ],
    )
    with pytest.raises(RuntimeError, match="拒绝启动"):
        chroma_store.assert_dimension(collection, 1024)
