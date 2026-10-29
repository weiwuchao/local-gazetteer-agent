"""指标聚合：metric_daily（日粒度，user_id/model/span_name 维度）。"""

from __future__ import annotations


def record(name: str, value: float, **labels: str) -> None:
    raise NotImplementedError
