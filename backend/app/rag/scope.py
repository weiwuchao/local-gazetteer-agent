"""SearchScope（不可变，权限的唯一载体）

设计依据：详细设计 4.1。

``visible_doc_ids`` 必须是 tuple：如果允许 list，某个 retriever 往里 append 一个
doc_id 就会造成越权，而且极难排查。排序去重由唯一产地 ``chat_service.build_scope()``
负责，这里只做类型与空值校验。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchScope:
    region_id: str
    visible_doc_ids: tuple[str, ...]
    index_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.visible_doc_ids, tuple):
            raise TypeError("visible_doc_ids must be tuple (immutable)")
        if not self.region_id or not self.region_id.strip():
            raise ValueError("region_id 不能为空")
        if not self.index_version or not self.index_version.strip():
            raise ValueError("index_version 不能为空")
        if any(not isinstance(doc_id, str) or not doc_id for doc_id in self.visible_doc_ids):
            raise ValueError("visible_doc_ids 只能包含非空字符串")
