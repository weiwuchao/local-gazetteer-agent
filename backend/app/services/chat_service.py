"""[C] 装配 AgentInput、build_scope（全系统唯一产地）、消息落库、SSE 桥接

需求：FR-C01~C08。约束：SQL 只能经 app/repo；状态机只能经 corpus_service.transition()。
"""

from __future__ import annotations

# TODO: 实现服务方法（事务边界在此层）
