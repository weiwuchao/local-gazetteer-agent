"""[B] 文档元数据、状态机迁移、上传落盘、可见集计算

需求：FR-B01~B08, FR-D07。约束：SQL 只能经 app/repo；状态机只能经 corpus_service.transition()。
"""

from __future__ import annotations

# TODO: 实现服务方法（事务边界在此层）
