"""分块入库 + 向量写入的集成测试（用假 embedding/collection，不依赖 Chroma）。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.core.text import sha256
from app.rag import indexer
from app.rag.chunking import ChunkingConfig
from app.repo import app_config_repo, chunk_repo, db, document_repo, region_repo

DIM = 4


class FakeEmbeddings:
    batch_size = 7

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


class FakeCollection:
    def __init__(self) -> None:
        self.ids: list[str] = []
        self.documents: list[str] = []
        self.metadatas: list[dict[str, Any]] = []

    def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        for chunk_id, document, metadata in zip(ids, documents, metadatas, strict=True):
            if chunk_id in self.ids:
                index = self.ids.index(chunk_id)
                self.documents[index] = document
                self.metadatas[index] = metadata
            else:
                self.ids.append(chunk_id)
                self.documents.append(document)
                self.metadatas.append(metadata)

    def delete(self, where: dict[str, Any]) -> None:
        doc_id = where["doc_id"]["$eq"]
        keep = [i for i, meta in enumerate(self.metadatas) if meta["doc_id"] != doc_id]
        self.ids = [self.ids[i] for i in keep]
        self.documents = [self.documents[i] for i in keep]
        self.metadatas = [self.metadatas[i] for i in keep]

    def get(self, where: dict[str, Any], include: list[str]) -> dict[str, Any]:
        doc_id = where.get("doc_id", {}).get("$eq")
        ids = [
            chunk_id
            for chunk_id, meta in zip(self.ids, self.metadatas, strict=True)
            if doc_id is None or meta["doc_id"] == doc_id
        ]
        return {"ids": ids}


@pytest.fixture()
def temp_db(tmp_path):
    db.shutdown()
    db.init(tmp_path / "app.db")
    app_config_repo.seed_defaults()
    try:
        yield tmp_path
    finally:
        db.shutdown()


def make_stack() -> tuple[indexer.VectorStack, FakeEmbeddings, FakeCollection]:
    embeddings = FakeEmbeddings()
    collection = FakeCollection()
    stack = indexer.VectorStack(
        models=SimpleNamespace(fp=SimpleNamespace(dim=DIM)),
        embeddings=embeddings,  # type: ignore[arg-type]
        collection=collection,
        index_version="idx__fake__4__c400__v1",
        chunk_config=ChunkingConfig(),
    )
    return stack, embeddings, collection


def seed_document(content: str) -> tuple[str, str, str]:
    region_id = region_repo.get_or_create(name="扶风县")
    doc_id = document_repo.create_document(
        region_id=region_id,
        title="扶风县志",
        source_note="测试",
        dup_key=sha256(content),
        created_by="test",
        status="proofreading",
    )
    version_id, version_no = document_repo.add_version(doc_id=doc_id, content=content)
    assert version_no == 1
    return region_id, doc_id, version_id


def test_config_defaults_and_bool_decode(temp_db) -> None:
    assert app_config_repo.get_int("index.chunk_size") == 800
    assert app_config_repo.get_int("index.chunk_overlap") == 100
    assert app_config_repo.get_bool("feature.preview_mode") is True

    # 旧库里 bool 可能以字符串写过，解码必须按真假语义走，不能 bool("false")。
    app_config_repo.set_value(
        "feature.preview_mode", "false", value_type="bool", description="测试"
    )
    assert app_config_repo.get_bool("feature.preview_mode") is False


def test_ingest_document_writes_chunks_and_vectors(temp_db) -> None:
    content = (
        "卷一 建置\n\n沿革\n扶风县古称美阳。" * 1
        + "秦孝公十二年始置美阳县，唐武德三年设湋川县。"
        + "\n\n卷二 祠祀\n\n法门寺\n法门寺在县北二十里，唐时敕建，内有真身宝塔。"
    )
    region_id, doc_id, version_id = seed_document(content)
    stack, embeddings, collection = make_stack()

    stats = indexer.ingest_document(
        doc_id=doc_id,
        version_id=version_id,
        region_id=region_id,
        content=content,
        embed=True,
        stack=stack,
    )

    rows = chunk_repo.list_by_doc(doc_id)
    assert stats.chunks_total == len(rows) > 0
    assert stats.indexed == stats.chunks_total
    assert stats.pending == 0
    assert all(row["index_state"] == "indexed" for row in rows)
    assert all(row["index_version"] == stack.index_version for row in rows)
    assert all(content[row["char_start"] : row["char_end"]] == row["content"] for row in rows)
    assert all(row["content_seg"] for row in rows)
    assert rows[0]["token_count"] > 0

    # Chroma documents 必须是原文（繁体/标点逐字一致），不是分词结果。
    assert collection.documents == [row["content"] for row in rows]
    assert all(meta["index_version"] == stack.index_version for meta in collection.metadatas)
    assert {meta["doc_id"] for meta in collection.metadatas} == {doc_id}

    doc = document_repo.get(doc_id)
    assert doc is not None
    assert doc["chunk_count"] == len(rows)
    assert doc["active_index_version"] == stack.index_version
    # 批量上限生效
    assert all(len(batch) <= embeddings.batch_size for batch in embeddings.calls)


def test_ingest_without_embed_leaves_pending(temp_db) -> None:
    content = "第一段。\n\n第二段。\n\n第三段。"
    region_id, doc_id, version_id = seed_document(content)
    stats = indexer.ingest_document(
        doc_id=doc_id,
        version_id=version_id,
        region_id=region_id,
        content=content,
        embed=False,
    )
    assert stats.chunks_total == stats.pending
    assert stats.indexed == 0
    assert all(row["index_state"] == "pending" for row in chunk_repo.list_by_doc(doc_id))


def test_index_pending_retries_stale_chunks(temp_db) -> None:
    content = "第一段。\n\n第二段。\n\n第三段。"
    region_id, doc_id, version_id = seed_document(content)
    stack, _, collection = make_stack()
    indexer.ingest_document(
        doc_id=doc_id,
        version_id=version_id,
        region_id=region_id,
        content=content,
        embed=True,
        stack=stack,
    )
    stale_id = collection.ids[0]
    chunk_repo.mark_index_state([stale_id], "stale", index_version=stack.index_version)
    collection.ids.remove(stale_id)
    collection.documents.pop(0)
    collection.metadatas.pop(0)

    stats = indexer.index_pending(doc_id, stack=stack)
    assert stats.stale == 0
    assert stats.pending == 0
    states = {row["chunk_id"]: row["index_state"] for row in chunk_repo.list_by_doc(doc_id)}
    assert states[stale_id] == "indexed"


def test_delete_by_doc_clears_sql_and_vectors(temp_db) -> None:
    content = "第一段。\n\n第二段。"
    region_id, doc_id, version_id = seed_document(content)
    stack, _, collection = make_stack()
    indexer.ingest_document(
        doc_id=doc_id,
        version_id=version_id,
        region_id=region_id,
        content=content,
        embed=True,
        stack=stack,
    )
    assert collection.ids
    removed = indexer.delete_by_doc(doc_id, stack=stack)
    assert removed > 0
    assert chunk_repo.list_by_doc(doc_id) == []
    assert collection.ids == []
