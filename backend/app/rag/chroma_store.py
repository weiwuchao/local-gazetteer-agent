"""PersistentClient、collection=chunks__{index_version}、where 构造

设计依据：详细设计 4.2 / 4.7.1、架构文档 D-6。

Chroma 里的元数据不用于权限判断：权限过滤由 SQL 算出 ``visible_doc_ids``
后下推 ``where``（doc_id $in），状态变化只改 SQL，不依赖向量库及时同步。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from app.core.config import get_settings, resolve_path
from app.rag.diagnostics import RagUnavailable, require_module
from app.rag.models import resolve_index_version

log = logging.getLogger(__name__)

COLLECTION_PREFIX = "chunks__"
_LOCK = threading.Lock()
_client: Any = None
_client_path: Path | None = None


def default_chroma_path() -> Path:
    return resolve_path(get_settings().data_dir) / "chroma"


def collection_name(index_version: str | None = None) -> str:
    version = index_version or resolve_index_version()
    return f"{COLLECTION_PREFIX}{version}"


def get_client(path: Path | str | None = None) -> Any:
    """进程内单例 PersistentClient（单进程是硬约束，架构 D-1）。"""
    global _client, _client_path

    target = Path(path) if path is not None else default_chroma_path()
    target = target.expanduser().resolve()
    with _LOCK:
        if _client is not None and _client_path == target:
            return _client
        chromadb = require_module("chromadb")
        target.mkdir(parents=True, exist_ok=True)
        try:
            settings = chromadb.config.Settings(anonymized_telemetry=False)
            _client = chromadb.PersistentClient(path=str(target), settings=settings)
        except Exception as exc:
            raise RagUnavailable(
                f"打开 Chroma 失败（{target}）：{type(exc).__name__}: {exc}"
            ) from exc
        _client_path = target
        log.info("Chroma PersistentClient 已打开：%s", target)
        return _client


def close_client() -> None:
    """关闭进程内单例；换数据目录或停机时调用。"""
    global _client, _client_path
    with _LOCK:
        _client = None
        _client_path = None


def _create_kwargs() -> dict[str, Any]:
    # Chroma 用余弦距离；向量在 embeddings 层已做 L2 归一，1 - distance 即余弦。
    return {"metadata": {"hnsw:space": "cosine"}}


def get_or_create_collection(
    index_version: str | None = None,
    *,
    client: Any | None = None,
) -> Any:
    version = index_version or resolve_index_version()
    if not version:
        raise RagUnavailable("index_version 为空，无法确定 collection")
    name = collection_name(version)
    chroma = client or get_client()
    try:
        return chroma.get_or_create_collection(name=name, **_create_kwargs())
    except Exception as exc:
        raise RagUnavailable(
            f"创建/打开 collection {name} 失败：{type(exc).__name__}: {exc}"
        ) from exc


def get_collection(index_version: str | None = None, *, client: Any | None = None) -> Any:
    version = index_version or resolve_index_version()
    chroma = client or get_client()
    try:
        return chroma.get_collection(name=collection_name(version))
    except Exception as exc:
        raise RagUnavailable(
            f"collection {collection_name(version)} 不存在，先跑 scripts/ingest_corpus.py"
        ) from exc


def probe_dimension(collection: Any) -> int | None:
    """读一条已有向量的维度；空 collection 返回 None。"""
    try:
        sample = collection.peek(limit=1)
    except Exception:
        return None
    embeddings = sample.get("embeddings") if hasattr(sample, "get") else None
    if embeddings is None or len(embeddings) == 0:
        return None
    return len(embeddings[0])


def assert_dimension(collection: Any, expected_dim: int) -> None:
    """启动自检：collection 实际维度与当前模型不一致时拒绝启动（D-7）。"""
    actual = probe_dimension(collection)
    if actual is not None and actual != expected_dim:
        raise RuntimeError(
            f"collection 维度 {actual} 与当前模型 {expected_dim} 不一致，拒绝启动"
            "（架构 D-7）。请新建索引版本并全量重灌。"
        )


def build_where(
    *,
    region_id: str,
    visible_doc_ids: tuple[str, ...] | list[str],
    index_version: str,
) -> dict[str, Any]:
    """权限过滤下推：region_id + doc_id $in + index_version。

    只按 region_id 过滤不够——同县可能有未发布语料（详细设计 4.2）。
    """
    return {
        "$and": [
            {"region_id": {"$eq": region_id}},
            {"doc_id": {"$in": list(visible_doc_ids)}},
            {"index_version": {"$eq": index_version}},
        ]
    }
