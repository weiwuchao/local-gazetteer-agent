"""阶段 1 intent：规则优先判定 qa/report/chitchat/write_request（覆盖目标 ≥70%）

预算：100ms。设计依据：详细设计 3.1 八阶段总表。
"""

from __future__ import annotations

from app.agent.contracts import AgentState


def run(c: AgentState) -> AgentState:
    raise NotImplementedError
