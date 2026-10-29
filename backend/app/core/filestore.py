"""文件落盘：data/files/{doc_id}/、data/exports/{report_id}/，0600、不在 Web 根。"""

from __future__ import annotations

from pathlib import Path


def save_upload(doc_id: str, filename: str, data: bytes) -> Path:
    raise NotImplementedError


def export_path(report_id: str, name: str) -> Path:
    raise NotImplementedError
