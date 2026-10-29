"""TraceCallbackHandler：on_chat_model_start/end、on_retriever_end → AgentEvent。"""

from __future__ import annotations

from typing import Any


class TraceCallbackHandler:
    def __init__(self, trace: Any) -> None:
        self.trace = trace
