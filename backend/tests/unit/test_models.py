"""模型指纹与索引版本（不加载模型、不读数据库）。"""

from __future__ import annotations

from app.rag.models import EmbeddingFingerprint


def test_index_version_encodes_fingerprint() -> None:
    fingerprint = EmbeddingFingerprint(model="BAAI/bge-m3", revision="x", dim=1024)
    assert fingerprint.index_version(400) == "idx__bgem3__1024__c400__v1"


def test_index_version_changes_with_chunk_size() -> None:
    fingerprint = EmbeddingFingerprint(model="BAAI/bge-m3", revision="x", dim=1024)
    base = fingerprint.index_version(400)
    assert fingerprint.index_version(500) != base


def test_index_version_from_local_directory() -> None:
    fingerprint = EmbeddingFingerprint(model=r"D:\models\bge-m3", revision="", dim=1024)
    assert fingerprint.index_version(400) == "idx__bgem3__1024__c400__v1"
