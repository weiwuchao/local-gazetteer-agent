"""应用启动冒烟测试：lifespan 在无模型情况下仍能启动。"""

from __future__ import annotations


def test_healthz(client) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
