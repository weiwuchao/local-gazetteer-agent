"""bge-m3 适配：批 16 + L2 归一 + 维度校验

设计依据：详细设计 4.7.2。

这一层只做三件事：批处理、L2 归一、原文直喂。不要加分词、繁简转换或截断——
``query_instruction`` 只加 query 侧，且必须走配置。
"""

from __future__ import annotations

from langchain_core.embeddings import Embeddings

from app.rag.diagnostics import RagUnavailable
from app.rag.models import EmbeddingConfig, EmbeddingDimensionMismatch, RagModels


class BgeM3Embeddings(Embeddings):
    """LangChain Embeddings 适配层：与 Chroma、检索器解耦。"""

    def __init__(self, models: RagModels, config: EmbeddingConfig | None = None) -> None:
        self.models = models
        self.config = config or EmbeddingConfig.load()
        self.batch_size = self.config.batch_size
        self.query_instruction = self.config.query_instruction
        if self.models.fp.dim != self.config.dim:
            raise EmbeddingDimensionMismatch(
                f"模型维度 {self.models.fp.dim} 与配置 dim={self.config.dim} 不一致"
            )

    @property
    def dim(self) -> int:
        return self.models.fp.dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self.models.embedder.encode(
            list(texts),
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        result = vectors.tolist()
        self._check(result, expected=len(texts))
        return result

    def embed_query(self, text: str) -> list[float]:
        # 指令只加 query 侧；两侧都用原字，保证与文档侧同一字面空间。
        payload = (self.query_instruction + text) if self.query_instruction else text
        vectors = self.models.embedder.encode(
            [payload],
            normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist()
        self._check(vectors, expected=1)
        return vectors[0]

    def _check(self, vectors: list[list[float]], *, expected: int) -> None:
        if len(vectors) != expected:
            raise RagUnavailable(f"embedding 返回 {len(vectors)} 条，期望 {expected} 条")
        for vector in vectors:
            if len(vector) != self.dim:
                raise EmbeddingDimensionMismatch(
                    f"embedding 维度 {len(vector)} 与模型 {self.dim} 不一致；"
                    "换模型必须新建 collection 并全量重灌"
                )
