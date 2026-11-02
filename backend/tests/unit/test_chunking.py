"""按长度切块（不识别卷/类目，不做文本转换）。"""

from __future__ import annotations

from app.rag.chunking import ChunkingConfig, chunk_text

SENTENCE = "内容" * 40 + "。"          # 81 字，带句末标点
CONTENT = SENTENCE * 10                # 810 字，可正常切块
LONG = "无标点" * 300                  # 900 字，没有任何边界，只能硬切
CONFIG = ChunkingConfig(chunk_size=300, overlap=50)


def test_offsets_point_into_original_text() -> None:
    result = chunk_text(CONTENT, config=CONFIG)
    assert result.chunks
    for chunk in result.chunks:
        assert CONTENT[chunk.char_start : chunk.char_end] == chunk.content


def test_chunks_respect_size_limit() -> None:
    for content in (CONTENT, LONG):
        for chunk in chunk_text(content, config=CONFIG).chunks:
            assert len(chunk.content) <= CONFIG.chunk_size


def test_adjacent_chunks_overlap() -> None:
    result = chunk_text(CONTENT, config=CONFIG)
    assert len(result.chunks) > 1
    for previous, current in zip(result.chunks, result.chunks[1:], strict=False):
        assert current.char_start < previous.char_end
        assert previous.char_end - current.char_start <= CONFIG.overlap


def test_long_sentence_is_hard_split() -> None:
    result = chunk_text(LONG, config=CONFIG)
    assert len(result.chunks) >= 3
    assert "".join(chunk.content for chunk in result.chunks).startswith("无标点")


def test_content_seg_keeps_original_and_is_tokenized() -> None:
    content = "縣北有法門寺。"
    chunk = chunk_text(content).chunks[0]
    assert chunk.content == content
    assert "縣" in chunk.content_seg     # 不转简
    assert "县" not in chunk.content_seg
    assert " " in chunk.content_seg      # 已分词


def test_empty_content_returns_no_chunks() -> None:
    assert chunk_text("").chunks == ()
    assert chunk_text("   \n\n  ").chunks == ()
