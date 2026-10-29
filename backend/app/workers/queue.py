"""任务投递与唤醒：入队前先写 task 表（幂等键唯一索引）。"""

from __future__ import annotations

import queue

TASK_QUEUE: "queue.Queue[str]" = queue.Queue()


def enqueue(task_id: str) -> None:
    TASK_QUEUE.put(task_id)
