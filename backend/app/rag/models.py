"""bge-m3 适配：单例加载、维度校验、索引版本指纹。

设计依据：详细设计 4.7.1、架构文档 1.5 / D-7。

模型是进程级单例，绝不在请求里 ``SentenceTransformer(...)``；首次用到时加载一次。
"""

from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import resolve_path
from app.rag.diagnostics import RagUnavailable, require_module
from app.repo import app_config_repo

log = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 800
_INDEX_VERSION_PREFIX = "idx"


class EmbeddingDimensionMismatch(RuntimeError):
    """配置维度与模型/collection 不一致。必须拒绝启动（架构 D-7）。"""


@dataclass(frozen=True, slots=True)
class EmbeddingFingerprint:
    """模型指纹：任何一项变化都意味着换索引版本。"""

    model: str
    revision: str
    dim: int

    @property
    def slug(self) -> str:
        name = re.split(r"[\\/]", self.model.rstrip("\\/"))[-1]
        return re.sub(r"[^0-9A-Za-z]", "", name).lower() or "model"

    def index_version(self, chunk_size: int) -> str:
        """索引版本 = 模型 + 维度 + 块大小；换任一项都要新建 collection。"""
        return (
            f"{_INDEX_VERSION_PREFIX}__{self.slug}__{self.dim}"
            f"__c{chunk_size}__v1"
        )


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    model: str
    revision: str
    dim: int
    query_instruction: str
    batch_size: int
    device: str
    cache_dir: Path

    @classmethod
    def load(cls) -> EmbeddingConfig:
        return cls(
            model=app_config_repo.get_str("llm.embedding.model", "BAAI/bge-m3").strip(),
            revision=app_config_repo.get_str("llm.embedding.revision", "").strip(),
            dim=app_config_repo.get_int("llm.embedding.dim", 1024),
            query_instruction=app_config_repo.get_str("llm.embedding.query_instruction", ""),
            batch_size=max(1, app_config_repo.get_int("llm.embedding.batch_size", 16)),
            device=app_config_repo.get_str("llm.embedding.device", "cpu"),
            cache_dir=resolve_path(
                app_config_repo.get_str("llm.embedding.cache_dir", "data/models")
            ),
        )

    @property
    def fingerprint(self) -> EmbeddingFingerprint:
        return EmbeddingFingerprint(model=self.model, revision=self.revision, dim=self.dim)


def chunk_size() -> int:
    return max(1, app_config_repo.get_int("index.chunk_size", DEFAULT_CHUNK_SIZE))


def compute_index_version(fingerprint: EmbeddingFingerprint | None = None) -> str:
    """按当前模型指纹计算索引版本；不读 ``index.active_version``。"""
    fp = fingerprint or EmbeddingConfig.load().fingerprint
    return fp.index_version(chunk_size())


def resolve_index_version() -> str:
    """当前检索/写入使用的索引版本。

    ``index.active_version`` 为空时按指纹计算（首次启动、本地开发）；
    非空时以它为准，用于 4.7.5 第 5 步的切换。
    """
    configured = app_config_repo.get_str("index.active_version", "").strip()
    return configured or compute_index_version()


@dataclass(slots=True)
class RagModels:
    fp: EmbeddingFingerprint
    device: str
    embedder: Any
    reranker: Any | None = None

    def actual_dim(self) -> int:
        dim = self.embedder.get_embedding_dimension()
        if dim is None:
            raise EmbeddingDimensionMismatch(
                f"无法读取模型 {self.fp.model} 的输出维度；请检查模型文件是否完整"
            )
        return int(dim)

    def warmup(self) -> None:
        """冷启动预热；异常直接抛出，由启动层决定是否降级。"""
        self.embedder.encode(["预热"], normalize_embeddings=True, show_progress_bar=False)


def load_models(
    config: EmbeddingConfig | None = None,
    *,
    with_reranker: bool = False,
) -> RagModels:
    """加载 bge-m3（本地目录或 HF 缓存/仓库 ID）并校验维度。失败直接抛。"""
    cfg = config or EmbeddingConfig.load()
    if not cfg.model:
        raise RagUnavailable("llm.embedding.model 为空；请先在 app_config 中设置向量模型")

    torch = require_module("torch")
    sentence_transformers = require_module("sentence_transformers")
    torch.set_num_threads(max(1, (os.cpu_count() or 4) - 1))

    # 相对路径统一按 backend/ 解析（与 core.config.resolve_path 同口径），
    # 不能只看 cwd：从仓库根目录执行脚本时，data/models/bge-m3 会被误判成
    # HuggingFace 仓库 ID，最终报 "Path data/models/bge-m3 not found"。
    target = cfg.model
    resolved = resolve_path(target)
    if resolved.exists():
        target = str(resolved)
    elif Path(target).expanduser().exists():
        # 兼容显式相对 cwd 的路径；解析成绝对路径后再交给 SentenceTransformer。
        target = str(Path(target).expanduser().resolve())

    kwargs: dict[str, Any] = {
        "device": cfg.device,
        "cache_folder": str(cfg.cache_dir),
    }
    if not Path(target).expanduser().is_absolute():
        # 仓库 ID 才带 revision；本地目录没有版本概念。
        kwargs["revision"] = cfg.revision or None

    embedder = sentence_transformers.SentenceTransformer(target, **kwargs)

    fp = cfg.fingerprint
    actual = embedder.get_embedding_dimension()
    if actual is not None and int(actual) != fp.dim:
        raise EmbeddingDimensionMismatch(
            f"配置 dim={fp.dim} 与模型实际 {int(actual)} 不一致；"
            "改配置前请按详细设计 4.7.5 重建索引"
        )

    reranker = _load_reranker(cfg) if with_reranker else None
    return RagModels(fp=fp, device=cfg.device, embedder=embedder, reranker=reranker)


def _load_reranker(cfg: EmbeddingConfig) -> Any:
    model = app_config_repo.get_str("llm.rerank.model", "").strip()
    if not model or model == "none":
        raise RagUnavailable("llm.rerank.model 未配置；rerank 是核心步骤，不能留空")
    sentence_transformers = require_module("sentence_transformers")
    return sentence_transformers.CrossEncoder(model, max_length=512, device=cfg.device)


# ---------------------------------------------------------------- 进程级单例

_lock = threading.Lock()
_models: RagModels | None = None


def get_models(*, with_reranker: bool = False) -> RagModels:
    """获取（必要时加载）进程级模型；失败直接抛 ``RagUnavailable``。"""
    global _models

    with _lock:
        if _models is None:
            _models = load_models(with_reranker=with_reranker)
        elif with_reranker and _models.reranker is None:
            _models.reranker = _load_reranker(EmbeddingConfig.load())
        return _models
