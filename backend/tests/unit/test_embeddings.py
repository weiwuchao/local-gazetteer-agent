"""向量化适配层：繁简口径、query 指令、维度校验（不加载真实模型）。"""

from __future__ import annotations

import pytest

from app.rag.embeddings import BgeM3Embeddings
from app.rag.models import (
    EmbeddingConfig,
    EmbeddingDimensionMismatch,
    EmbeddingFingerprint,
    RagModels,
)


class _Vectors(list):
    def tolist(self):
        return list(self)


class FakeEmbedder:
    def __init__(self) -> None:
        self.inputs: list[list[str]] = []

    def encode(self, texts, **kwargs):
        self.inputs.append(list(texts))
        return _Vectors([0.1] * 4 for _ in texts)

    def get_embedding_dimension(self):
        return 4


def make_config(*, instruction: str = "") -> EmbeddingConfig:
    from pathlib import Path

    return EmbeddingConfig(
        model="BAAI/bge-m3",
        revision="test",
        dim=4,
        query_instruction=instruction,
        batch_size=16,
        device="cpu",
        cache_dir=Path("."),
    )


def make_embeddings(instruction: str = "") -> tuple[BgeM3Embeddings, FakeEmbedder]:
    embedder = FakeEmbedder()
    models = RagModels(
        fp=EmbeddingFingerprint(model="BAAI/bge-m3", revision="test", dim=4),
        device="cpu",
        embedder=embedder,
    )
    return BgeM3Embeddings(models, make_config(instruction=instruction)), embedder


def test_documents_stay_verbatim_and_not_tokenized() -> None:
    embeddings, embedder = make_embeddings()
    vectors = embeddings.embed_documents(["縣北有法門寺。"])
    assert len(vectors) == 1
    assert embedder.inputs == [["縣北有法門寺。"]]


def test_query_instruction_only_on_query_side() -> None:
    embeddings, embedder = make_embeddings("为这个句子生成表示以用于检索相关文章：")
    embeddings.embed_query("縣北有法門寺。")
    assert embedder.inputs[0] == ["为这个句子生成表示以用于检索相关文章：縣北有法門寺。"]

    embeddings.embed_documents(["縣北有法門寺。"])
    assert embedder.inputs[1] == ["縣北有法門寺。"]


def test_dimension_mismatch_is_rejected() -> None:
    embedder = FakeEmbedder()
    models = RagModels(
        fp=EmbeddingFingerprint(model="BAAI/bge-m3", revision="test", dim=8),
        device="cpu",
        embedder=embedder,
    )
    with pytest.raises(EmbeddingDimensionMismatch):
        BgeM3Embeddings(models, make_config())
