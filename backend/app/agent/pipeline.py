"""8 阶段编排器：同步阶段统一下沉线程池，阶段 5/7 异步流式。

设计依据：详细设计 3.1。预算与异常兜底见 registry.py / budget.py。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable

from app.agent.contracts import AgentInput, AgentState, AnswerPayload
from app.agent.stages import (
    s0_assemble,
    s1_intent,
    s2_rewrite,
    s3_retrieve,
    s4_judge,
    s5_generate,
    s6_validate,
    s7_deliver,
)
from app.core.executors import RETRIEVAL_POOL

Emit = Callable[[dict], Awaitable[None]]


async def run(inp: AgentInput, emit: Emit) -> AnswerPayload:
    loop = asyncio.get_running_loop()
    c = await asyncio.to_thread(s0_assemble.run, inp, None)
    c = await asyncio.to_thread(s1_intent.run, c)
    c = await asyncio.to_thread(s2_rewrite.run, c)
    c = await loop.run_in_executor(RETRIEVAL_POOL, s3_retrieve.run, c)
    c = await asyncio.to_thread(s4_judge.run, c)
    async for ev in s5_generate.run(c):
        await emit(ev)
    c = await asyncio.to_thread(s6_validate.run, c)
    return await s7_deliver.run(c, emit)
