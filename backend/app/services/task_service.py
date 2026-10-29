"""任务投递/查询/幂等（L5 门面）

需求：FR-B07, FR-E06。约束：SQL 只能经 app/repo；状态机只能经 corpus_service.transition()。
"""

from __future__ import annotations

# TODO: 实现服务方法（事务边界在此层）
