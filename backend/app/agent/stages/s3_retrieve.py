"""阶段 3 retrieve：混合检索 5–8 条；向量失败降级 BM25

预算：900ms。设计依据：详细设计 3.1 八阶段总表。
"""

from __future__ import annotations

from app.agent.contracts import AgentState


def run(c: AgentState) -> AgentState:
    raise NotImplementedError
