"""pytest 公共夹具：临时 DB、TestClient、真实 Chroma 客户端。"""

from __future__ import annotations

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """隔离的 TestClient：临时的数据目录，不加载真实向量模型。

    ``indexer.get_stack()`` 是惰性加载，测试只要不调用检索/入库就不会碰模型；
    向量链路用 ``test_indexing.py`` 的假 stack 单独验证。
    """
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))

    from app.core.config import get_settings
    from app.repo import db

    get_settings.cache_clear()

    from fastapi.testclient import TestClient

    from app.main import create_app

    try:
        with TestClient(create_app()) as c:
            yield c
    finally:
        db.shutdown()
        get_settings.cache_clear()


@pytest.fixture()
def chroma_client(tmp_path):
    """临时目录里的真实 Chroma 客户端（未安装 chromadb 时跳过）。"""
    from app.rag import chroma_store

    pytest.importorskip("chromadb")
    chroma_store.close_client()
    try:
        yield chroma_store.get_client(tmp_path / "chroma")
    finally:
        chroma_store.close_client()
