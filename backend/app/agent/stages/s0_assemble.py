"""阶段 0 auth：校验 AgentInput 不变量、装配 AgentState（不碰 SQL）

预算：30ms。设计依据：详细设计 3.1 八阶段总表。
"""

from __future__ import annotations

from app.agent.contracts import AgentState


def run(c: AgentState) -> AgentState:
    raise NotImplementedError
