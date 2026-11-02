"""把 doc/语料素材 下的文档切块并写入 SQLite + Chroma。

用法（在 backend/ 下执行）：
    python scripts/ingest_corpus.py                       # 扫描 doc/语料素材
    python scripts/ingest_corpus.py doc/语料素材/县情概述.md --title 扶风县志

只做入库这一件事：缺依赖 / 缺模型 / 维度不一致都会直接报错，不降级。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.text import sha256  # noqa: E402
from app.rag import indexer  # noqa: E402
from app.rag.diagnostics import RagUnavailable  # noqa: E402
from app.repo import app_config_repo, db, document_repo, region_repo  # noqa: E402

DEFAULT_SOURCE = REPO_ROOT / "doc" / "语料素材"
SUPPORTED_SUFFIXES = {".md", ".txt", ".pdf", ".docx"}


def _extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".md", ".txt"}:
        return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".docx":
        return _extract_docx(path)
    raise ValueError(f"不支持的文件类型：{suffix}")


def _extract_pdf(path: Path) -> str:
    try:
        import pymupdf as fitz
    except Exception as exc:
        raise RagUnavailable(f"缺少 PDF 抽取依赖 pymupdf：{exc}") from exc
    with fitz.open(str(path)) as document:
        pages = [page.get_text("text") for page in document]
    if pages and not any(page.strip() for page in pages):
        raise ValueError(f"{path.name} 没有文本层；扫描件需要 OCR，本链路不做 OCR")
    return "\n\n".join(pages).replace("\r\n", "\n")


def _extract_docx(path: Path) -> str:
    try:
        import docx
    except Exception as exc:
        raise RagUnavailable(f"缺少 DOCX 抽取依赖 python-docx：{exc}") from exc
    document = docx.Document(str(path))
    blocks = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                blocks.append("\t".join(cells))
    return "\n\n".join(blocks).replace("\r\n", "\n")


def _guess_title(path: Path, content: str) -> str:
    for line in content.splitlines():
        if line.startswith("# ") and line[2:].strip():
            return line[2:].strip()
    return path.stem


def _collect(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.exists():
        raise SystemExit(f"目录不存在：{path}")
    return [
        item
        for item in sorted(path.rglob("*"))
        if item.is_file() and item.suffix.lower() in SUPPORTED_SUFFIXES
    ]


def _print_stats(name: str, version_no: int, stats: indexer.IndexStats) -> None:
    print(
        f"  {name}: v{version_no} "
        f"chunks={stats.chunks_total} indexed={stats.indexed} pending={stats.pending}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="语料素材 → 分块 → 向量库（不降级）")
    parser.add_argument("path", nargs="?", default=str(DEFAULT_SOURCE), help="文件或目录")
    parser.add_argument("--region", default="扶风县", help="县市名（不存在时自动创建）")
    parser.add_argument("--title", default=None, help="仅单文件时覆盖标题")
    args = parser.parse_args()

    db.init()
    app_config_repo.seed_defaults()

    source = Path(args.path)
    if not source.is_absolute():
        source = source if source.exists() else REPO_ROOT / source
    files = _collect(source)
    if not files:
        print(f"没有找到可入库文档（{', '.join(sorted(SUPPORTED_SUFFIXES))}）：{source}")
        return 1
    if args.title and len(files) > 1:
        print("--title 只能用于单个文件")
        return 1

    stack = indexer.build_stack()
    region_id = region_repo.get_or_create(name=args.region, province=None, city=None)
    print(f"语料目录：{source}")
    print(f"县市：{args.region}（{region_id}）")
    print(f"待处理：{len(files)} 个文件；index_version={stack.index_version}")
    print()

    failures = 0
    for path in files:
        try:
            content = _extract_text(path)
            if len(content.strip()) < 10:
                print(f"  跳过空文件：{path.name}")
                continue
            title = args.title or _guess_title(path, content)
            digest = sha256(content)
            relative = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
            source_note = f"本地语料素材：{relative}"

            existing = document_repo.find_by_dup_key(region_id, digest)
            if existing is not None:
                doc_id = str(existing["id"])
                document_repo.reset_for_rechunk(doc_id)
                indexer.delete_by_doc(doc_id)
            else:
                doc_id = document_repo.create_document(
                    region_id=region_id,
                    title=title,
                    source_note=source_note,
                    dup_key=digest,
                    created_by="cli",
                    status="proofreading",
                )

            version_id, version_no = document_repo.add_version(
                doc_id=doc_id,
                content=content,
                version_type="raw",
                stats={"chars": len(content), "source": source_note},
                created_by="cli",
            )
            stats = indexer.ingest_document(
                doc_id=doc_id,
                version_id=version_id,
                region_id=region_id,
                content=content,
                stack=stack,
            )
            document_repo.set_status(
                doc_id, "published", searchable_version_id=version_id
            )
            _print_stats(path.name, version_no, stats)
        except Exception as exc:
            failures += 1
            print(f"  失败：{path.name} → {type(exc).__name__}: {exc}")

    print()
    print(f"完成：{len(files) - failures}/{len(files)} 个文件")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        db.shutdown()
