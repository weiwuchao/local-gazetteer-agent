"""阶段 5 generate：先引证档（5a）后白话解读（5b，流式）

预算：TTFT 2.5s。设计依据：详细设计 3.1 八阶段总表。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.agent.contracts import AgentState


async def run(c: AgentState) -> AsyncIterator[dict]:
    """5a 引证档（非流式，结构化）→ citation 事件；5b 白话解读逐 token delta。"""
    raise NotImplementedError
    yield {}  # pragma: no cover
