"""向量路：过滤下推 doc_id ∈ scope；继承 BaseRetriever。

设计依据：详细设计 4.2、4.7.2。

权限过滤由 SQL 算出 ``visible_doc_ids`` 后下推 ``where``；Chroma 元数据不用于
权限判断。query 侧原文直喂，不做繁简转换或分词。

返回的 ``Document`` 列表按 ``score``（余弦相似度）降序：顺序由逐条距离值决定，
不依赖 Chroma 的返回顺序（其升序行为未写入文档）。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from app.rag import chroma_store
from app.rag.scope import SearchScope

log = logging.getLogger(__name__)


class VectorRetriever(BaseRetriever):
    scope: SearchScope
    k: int = 20
    embed: Any
    collection: Any

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: Any = None,
    ) -> list[Document]:
        # 空集短路，永不查全库。
        if not self.scope.visible_doc_ids:
            return []

        where = chroma_store.build_where(
            region_id=self.scope.region_id,
            visible_doc_ids=self.scope.visible_doc_ids,
            index_version=self.scope.index_version,
        )
        result = self.collection.query(
            query_embeddings=[self.embed.embed_query(query)],
            n_results=self.k,
            where=where,
            include=["metadatas", "distances", "documents"],
        )

        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        if not (len(documents) == len(metadatas) == len(distances)):
            log.warning(
                "Chroma 返回长度不一致：documents=%d metadatas=%d distances=%d",
                len(documents),
                len(metadatas),
                len(distances),
            )

        visible = set(self.scope.visible_doc_ids)
        docs: list[Document] = []
        leaked = 0
        for text, metadata, distance in zip(documents, metadatas, distances, strict=False):
            meta = dict(metadata or {})
            doc_id = meta.get("doc_id")
            if doc_id not in visible:
                # 只告警并丢弃，不作为过滤手段：where 下推才是权限边界。
                leaked += 1
                continue
            docs.append(
                Document(
                    page_content=text,
                    metadata={
                        **meta,
                        "score": 1.0 - float(distance) if distance is not None else 0.0,
                    },
                )
            )

        if leaked:
            log.error(
                "scope leak：Chroma 返回了 %d 条可见范围外的片段（where 下推异常）",
                leaked,
            )

        # 自己按距离值排序，不赌 Chroma 的返回顺序；同分用 chunk_id 保证可复现。
        docs.sort(
            key=lambda doc: (
                -float(doc.metadata["score"]),
                str(doc.metadata.get("chunk_id") or ""),
            )
        )
        return docs
