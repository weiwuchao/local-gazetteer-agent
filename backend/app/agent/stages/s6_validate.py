"""阶段 6 validate：引用闭环、数字、违禁词校验；删断言/降级/转拒答

预算：150ms。设计依据：详细设计 3.1 八阶段总表。
"""

from __future__ import annotations

from app.agent.contracts import AgentState


def run(c: AgentState) -> AgentState:
    raise NotImplementedError
