"""按长度切块：句末标点优先、块间重叠、不改写文本。

设计依据：详细设计 4.6。

- 先在句末标点/换行处切句；单句超过 ``chunk_size`` 时按长度硬切（兜底）；
- 顺序累积到不超过 ``chunk_size`` 就成一块；
- 下一块从上一块结尾回退 ``overlap`` 字开始，避免跨块句子被切断；
- 每块保留精确 ``char_start/char_end``，运行时不再用 ``content.find`` 猜位置。

不做结构识别（卷 / 类目 / 条目）：本项目的切块只看长度。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.text import count_tokens
from app.rag.normalizer import tokenize

_SENTENCE_BOUNDARY = re.compile(r"[。！？!?；;…\n]")
_CLOSERS = "”’」』）】》〉"


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """切块参数。``chunk_size`` 参与 index_version 指纹，改动必须重建索引。"""

    chunk_size: int = 800
    overlap: int = 100

    def __post_init__(self) -> None:
        if self.chunk_size <= 0:
            raise ValueError("chunk_size 必须为正数")
        if self.overlap < 0 or self.overlap >= self.chunk_size:
            raise ValueError("overlap 不能为负，且必须小于 chunk_size")


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    """待入库的 chunk 草稿；``chunk_id`` 由 repo 层生成。"""

    seq: int
    content: str
    content_seg: str
    char_start: int
    char_end: int
    token_count: int


@dataclass(frozen=True, slots=True)
class ChunkingResult:
    chunks: tuple[ChunkDraft, ...]

    @property
    def chars(self) -> int:
        return sum(len(chunk.content) for chunk in self.chunks)


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    """按句末标点/换行切句，返回相对偏移。标点留在前一句末尾。"""
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_BOUNDARY.finditer(text):
        end = match.end()
        while end < len(text) and text[end] in _CLOSERS:
            end += 1
        if end > start:
            spans.append((start, end))
        start = end
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def _hard_split(start: int, end: int, limit: int) -> list[tuple[int, int]]:
    """超长句在 ``limit`` 处硬切；正常句子不会走到这里。"""
    return [(pos, min(pos + limit, end)) for pos in range(start, end, limit)]


def _trim(content: str, start: int, end: int) -> tuple[str, int, int]:
    raw = content[start:end]
    lead = len(raw) - len(raw.lstrip())
    trail = len(raw) - len(raw.rstrip())
    return content[start + lead: end - trail], start + lead, end - trail


def _emit(content: str, start: int, end: int, *, seq: int) -> ChunkDraft | None:
    text, char_start, char_end = _trim(content, start, end)
    if len(text) < 2:
        return None
    return ChunkDraft(
        seq=seq,
        content=text,
        content_seg=tokenize(text),
        char_start=char_start,
        char_end=char_end,
        token_count=count_tokens(text),
    )


def chunk_text(
    content: str,
    *,
    config: ChunkingConfig | None = None,
) -> ChunkingResult:
    """正文 → chunk 列表（纯计算，不碰数据库、不碰模型）。"""
    cfg = config or ChunkingConfig()
    if not content or not content.strip():
        return ChunkingResult(())

    spans: list[tuple[int, int]] = []
    for start, end in _sentence_spans(content):
        if end - start > cfg.chunk_size:
            spans.extend(_hard_split(start, end, cfg.chunk_size))
        else:
            spans.append((start, end))

    chunks: list[ChunkDraft] = []
    seq = 1
    index = 0
    previous_end: int | None = None
    while index < len(spans):
        start = spans[index][0]
        if previous_end is not None:
            start = max(0, previous_end - cfg.overlap)
            # 重叠不能把单块撑过上限：必要时放弃重叠，保证 end - start <= chunk_size。
            start = max(start, spans[index][1] - cfg.chunk_size)
        end = spans[index][1]
        last = index
        while last + 1 < len(spans) and spans[last + 1][1] - start <= cfg.chunk_size:
            last += 1
            end = spans[last][1]
        draft = _emit(content, start, end, seq=seq)
        if draft is not None:
            chunks.append(draft)
            seq += 1
        previous_end = end
        index = last + 1
    return ChunkingResult(tuple(chunks))
