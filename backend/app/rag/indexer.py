"""upsert / delete_by_doc / 批量索引

设计依据：详细设计 4.6 / 4.7.3、架构文档 5.3 / D-6 / G-6。

两条不可违反的顺序：
1. 先写 Chroma，成功后才把 ``chunk.index_state`` 改成 indexed；Chroma 失败时
   chunk 保持 pending，重跑即可续上。
2. 写库事务里不出现任何网络/模型调用：embed 与 upsert 都在 SQL 事务之外。

失败不降级：缺模型/缺依赖直接抛 ``RagUnavailable`` / ``IndexingError``。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from app.rag import chroma_store
from app.rag.chunking import ChunkingConfig, ChunkingResult, chunk_text
from app.rag.embeddings import BgeM3Embeddings
from app.rag.models import (
    EmbeddingConfig,
    EmbeddingDimensionMismatch,
    RagModels,
    get_models,
    resolve_index_version,
)
from app.repo import app_config_repo, chunk_repo, document_repo

log = logging.getLogger(__name__)


class IndexingError(RuntimeError):
    """向量化/写入 Chroma 失败；异常文本面向运维。"""


@dataclass(frozen=True, slots=True)
class VectorStack:
    models: RagModels
    embeddings: BgeM3Embeddings
    collection: Any
    index_version: str
    chunk_config: ChunkingConfig

    @property
    def dim(self) -> int:
        return self.models.fp.dim

    @property
    def batch_size(self) -> int:
        return self.embeddings.batch_size


@dataclass(frozen=True, slots=True)
class IndexStats:
    doc_id: str
    index_version: str
    chunks_total: int
    indexed: int
    pending: int
    stale: int

    @property
    def ready(self) -> bool:
        return self.chunks_total > 0 and self.pending == 0 and self.stale == 0


def chunking_config() -> ChunkingConfig:
    return ChunkingConfig(
        chunk_size=max(1, app_config_repo.get_int("index.chunk_size", 800)),
        overlap=max(0, app_config_repo.get_int("index.chunk_overlap", 100)),
    )


def chunk_document(content: str, *, config: ChunkingConfig | None = None) -> ChunkingResult:
    """纯计算：正文 → chunk 草稿（不碰数据库、不碰模型）。"""
    return chunk_text(content, config=config or chunking_config())


# ---------------------------------------------------------------- 向量栈


def build_stack(*, with_reranker: bool = False) -> VectorStack:
    config = EmbeddingConfig.load()
    models = get_models(with_reranker=with_reranker)
    actual = models.actual_dim()
    if actual != config.dim:
        raise EmbeddingDimensionMismatch(
            f"配置 llm.embedding.dim={config.dim} 与模型实际 {actual} 不一致；"
            "改配置前请按详细设计 4.7.5 重建索引"
        )
    index_version = resolve_index_version()
    collection = chroma_store.get_or_create_collection(index_version)
    chroma_store.assert_dimension(collection, config.dim)
    embeddings = BgeM3Embeddings(models, config)
    return VectorStack(
        models=models,
        embeddings=embeddings,
        collection=collection,
        index_version=index_version,
        chunk_config=chunking_config(),
    )


_stack: VectorStack | None = None


def get_stack() -> VectorStack:
    global _stack
    if _stack is None:
        _stack = build_stack()
    return _stack


# ---------------------------------------------------------------- 入库


def store_chunks(
    *,
    doc_id: str,
    version_id: str,
    region_id: str,
    result: ChunkingResult,
    index_version: str,
    ocr_flag: int = 0,
) -> list[str]:
    rows = [asdict(chunk) for chunk in result.chunks]
    return chunk_repo.insert_chunks(
        doc_id,
        version_id,
        region_id,
        rows,
        index_version=index_version,
        ocr_flag=ocr_flag,
    )


def ingest_document(
    *,
    doc_id: str,
    version_id: str,
    region_id: str,
    content: str,
    embed: bool = True,
    stack: VectorStack | None = None,
) -> IndexStats:
    """一版正文的完整入库：切块 → 写 chunk/FTS → 向量化 → 回写状态。"""
    active = stack or (get_stack() if embed else None)
    index_version = active.index_version if active else resolve_index_version()

    result = chunk_document(content, config=active.chunk_config if active else None)
    store_chunks(
        doc_id=doc_id,
        version_id=version_id,
        region_id=region_id,
        result=result,
        index_version=index_version,
    )
    document_repo.update_after_chunk(
        doc_id,
        version_id=version_id,
        chunk_count=len(result.chunks),
        index_version=index_version,
    )
    if embed:
        assert active is not None
        return index_pending(doc_id, stack=active)

    counts = chunk_repo.count_by_state(doc_id, index_version)
    return IndexStats(
        doc_id=doc_id,
        index_version=index_version,
        chunks_total=len(result.chunks),
        indexed=counts.get("indexed", 0),
        pending=counts.get("pending", 0),
        stale=counts.get("stale", 0),
    )


def index_pending(
    doc_id: str,
    *,
    stack: VectorStack | None = None,
    retry_stale: bool = True,
    progress: Callable[[int], None] | None = None,
) -> IndexStats:
    """把 pending 的 chunk 分批 embedding → upsert → 标 indexed。

    进度不落游标，靠 ``chunk.index_state='pending'`` 反查；进程被杀后重跑即可。
    """
    active = stack or get_stack()
    if retry_stale:
        chunk_repo.reset_stale(doc_id, active.index_version)
    batch_size = active.batch_size
    indexed = 0
    while True:
        rows = chunk_repo.list_pending(doc_id, active.index_version, limit=batch_size)
        if not rows:
            break
        ids = [row["chunk_id"] for row in rows]
        texts = [row["content"] for row in rows]
        try:
            vectors = active.embeddings.embed_documents(texts)
        except Exception as exc:
            chunk_repo.mark_index_state(ids, "stale", index_version=active.index_version)
            raise IndexingError(
                f"文档 {doc_id} 的 {len(rows)} 个片段向量化失败，已标记 stale。\n"
                f"原始错误：{type(exc).__name__}: {exc}"
            ) from exc

        metadatas = [_metadata(row, active.index_version) for row in rows]
        try:
            # documents 存原文（繁体），不是向量化输入；取证栏据此逐字核对。
            active.collection.upsert(
                ids=ids,
                embeddings=vectors,
                documents=texts,
                metadatas=metadatas,
            )
        except Exception as exc:
            raise IndexingError(
                f"写入 Chroma 失败（{len(rows)} 条）；chunk 状态保持 pending，可直接重跑。\n"
                f"collection={chroma_store.collection_name(active.index_version)}\n"
                f"原始错误：{type(exc).__name__}: {exc}"
            ) from exc

        chunk_repo.mark_index_state(ids, "indexed", index_version=active.index_version)
        indexed += len(rows)
        if progress is not None:
            progress(indexed)
        log.info("doc=%s 已索引 %d 条", doc_id, indexed)

    document_repo.set_active_index_version(doc_id, active.index_version)
    counts = chunk_repo.count_by_state(doc_id, active.index_version)
    return IndexStats(
        doc_id=doc_id,
        index_version=active.index_version,
        chunks_total=sum(counts.values()),
        indexed=counts.get("indexed", 0),
        pending=counts.get("pending", 0),
        stale=counts.get("stale", 0),
    )


def _metadata(row: Any, index_version: str) -> dict[str, Any]:
    return {
        "chunk_id": row["chunk_id"],
        "doc_id": row["doc_id"],
        "region_id": row["region_id"],
        "index_version": index_version,
        "ocr_flag": int(row["ocr_flag"] or 0),
    }


# ---------------------------------------------------------------- 删除


def delete_by_doc(doc_id: str, *, stack: VectorStack | None = None) -> int:
    """删除一版文档的 chunk：先改 SQL（真相源），再删 Chroma。"""
    removed = chunk_repo.delete_by_doc(doc_id)
    active = stack or _stack
    if active is not None:
        collection = active.collection
    else:
        # 没加载模型时也能清理向量：Chroma 与 embedding 是两件独立的事。
        collection = chroma_store.get_or_create_collection(resolve_index_version())
    collection.delete(where={"doc_id": {"$eq": doc_id}})
    return removed
