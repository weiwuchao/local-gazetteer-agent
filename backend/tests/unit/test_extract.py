"""本地语料导入的 PDF/DOCX 文本抽取（脚本级，不启动 CLI）。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ingest_corpus.py"


def load_script():
    spec = importlib.util.spec_from_file_location("ingest_corpus_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_extract_docx(tmp_path) -> None:
    docx = pytest.importorskip("docx")
    path = tmp_path / "sample.docx"
    document = docx.Document()
    document.add_paragraph("县情概述")
    document.add_paragraph("扶风县位于陕西省关中平原西部。")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "面积"
    table.cell(0, 1).text = "745.8 平方公里"
    document.save(path)

    text = load_script()._extract_docx(path)
    assert "扶风县位于陕西省关中平原西部。" in text
    assert "面积\t745.8 平方公里" in text


def test_extract_pdf(tmp_path) -> None:
    fitz = pytest.importorskip("pymupdf")
    path = tmp_path / "sample.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Fufeng County Gazetteer")
    document.save(path)
    document.close()

    text = load_script()._extract_pdf(path)
    assert "Fufeng County Gazetteer" in text


def test_blank_pdf_reports_no_text_layer(tmp_path) -> None:
    fitz = pytest.importorskip("pymupdf")
    path = tmp_path / "blank.pdf"
    document = fitz.open()
    document.new_page()
    document.save(path)
    document.close()

    with pytest.raises(ValueError, match="没有文本层"):
        load_script()._extract_pdf(path)
