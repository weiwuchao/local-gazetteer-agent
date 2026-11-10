"""全文路：预分词 MATCH，AND/OR 三级回退。

设计依据：详细设计 4.2–4.4、4.3.2。

写入与查询必须用同一套 jieba 分词，且都用原字（不做繁简归一）。SQL 与回退
策略在 ``fts_store``，这里只负责转成 LangChain ``Document``。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from app.rag import fts_store
from app.rag.normalizer import query_tokens
from app.rag.scope import SearchScope

log = logging.getLogger(__name__)


class FtsRetriever(BaseRetriever):
    scope: SearchScope
    k: int = 20

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: Any = None,
    ) -> list[Document]:
        if not self.scope.visible_doc_ids:
            return []

        hits = fts_store.search(
            query,
            query_tokens(query),
            scope=self.scope,
            limit=self.k,
        )
        if hits:
            log.debug(
                "fts fallback=%s hits=%d scope_docs=%d",
                hits[0].fallback,
                len(hits),
                len(self.scope.visible_doc_ids),
            )
        return [
            Document(
                page_content=hit.content,
                metadata={
                    "chunk_id": hit.chunk_id,
                    "doc_id": hit.doc_id,
                    "char_start": hit.char_start,
                    "char_end": hit.char_end,
                    "ocr_flag": hit.ocr_flag,
                    "score": hit.score,
                    "bm25_rank": rank,
                    "fts_fallback": hit.fallback,
                },
            )
            for rank, hit in enumerate(hits, 1)
        ]
