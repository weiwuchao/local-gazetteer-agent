"""[A] 注册/登录/令牌轮换/吊销/锁定/邀请码

需求：FR-A01~A04。约束：SQL 只能经 app/repo；状态机只能经 corpus_service.transition()。
"""

from __future__ import annotations

# TODO: 实现服务方法（事务边界在此层）
