"""两个线程池，按性质分开，互不挤占（详细设计 4.7.6）。

检索池：CPU 密集（编码 + 重排），固定 2；通用池：PDF 解析、DOCX 导出、文件 IO。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

RETRIEVAL_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="retrieval")
IO_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="io")
