"""pytest 公共夹具：临时 DB、TestClient、admin/user 两种 principal。"""

from __future__ import annotations

import pytest


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c
