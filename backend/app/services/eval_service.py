"""[S] 评测集 CRUD、运行编排、指标计算

需求：FR-S10。约束：SQL 只能经 app/repo；状态机只能经 corpus_service.transition()。
"""

from __future__ import annotations

# TODO: 实现服务方法（事务边界在此层）
