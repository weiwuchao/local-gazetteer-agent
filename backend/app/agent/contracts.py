"""Agent 内部契约：与 HTTP DTO 分离。

设计依据：架构文档 3.1、详细设计 3.2。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AnswerType(str, Enum):
    FACT = "fact"
    SUMMARY = "summary"
    COMPARE = "compare"
    REFUSE = "refuse"
    CHITCHAT = "chitchat"


class Intent(str, Enum):
    QA = "qa"
    REPORT = "report"
    CHITCHAT = "chitchat"
    WRITE_REQUEST = "write_request"


@dataclass(frozen=True, slots=True)
class AgentInput:
    user_id: str
    role: str
    region_id: str
    question: str
    conversation_id: str | None = None
    message_id: str | None = None
    preview: bool = False
    mode: str = "answer"
    scope: frozenset[str] = frozenset()  # visible_doc_ids，唯一由 chat_service 产出
    memory: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentState:
    input: AgentInput
    intent: Intent | None = None
    query: str = ""
    chunks: list[Any] = field(default_factory=list)
    answer: Any | None = None
    trace_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AnswerPayload:
    answer_type: AnswerType
    region: dict[str, Any]
    direct_answer: str
    explanation: str
    citations: list[dict[str, Any]]
    confidence: str
    caveats: list[str]
    followups: list[str]
    refusal: dict[str, Any] | None
    meta: dict[str, Any]
