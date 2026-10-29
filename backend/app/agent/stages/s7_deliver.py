"""阶段 7 deliver：10 字段 payload + done；断流可重放

预算：—。设计依据：详细设计 3.1 八阶段总表。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.agent.contracts import AgentState, AnswerPayload


async def run(c: AgentState, emit: Callable[[dict], Awaitable[None]]) -> AnswerPayload:
    """下发最终 payload 与 done 事件；写入内存重放缓冲。"""
    raise NotImplementedError
