"""模型指纹与索引版本（不加载模型、不读数据库）。"""

from __future__ import annotations

from types import SimpleNamespace

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


def test_reranker_load_disables_default_sigmoid(monkeypatch) -> None:
    """ST >= 4 对单标签默认 Sigmoid；必须显式改成 Identity，否则会二次 sigmoid。"""
    from app.rag import models as models_module

    captured: dict[str, object] = {}

    class FakeCrossEncoder:
        def __init__(self, model: str, **kwargs) -> None:
            captured["model"] = model
            captured.update(kwargs)

    class FakeSentenceTransformers:
        pass

    FakeSentenceTransformers.CrossEncoder = FakeCrossEncoder

    class FakeTorch:
        class nn:
            @staticmethod
            def Identity():
                return "identity"

    def fake_require(name: str):
        return {"sentence_transformers": FakeSentenceTransformers, "torch": FakeTorch}[name]

    monkeypatch.setattr(models_module, "require_module", fake_require)
    monkeypatch.setattr(
        models_module.app_config_repo,
        "get_str",
        lambda *args, **kwargs: "BAAI/bge-reranker-v2-m3",
    )

    models_module._load_reranker(SimpleNamespace(device="cpu"))

    assert captured["model"] == "BAAI/bge-reranker-v2-m3"
    assert captured["activation_fn"] == "identity"
