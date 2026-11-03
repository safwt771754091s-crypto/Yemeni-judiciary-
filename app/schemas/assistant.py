"""Schemas for the AI legal assistant (المساعد القانوني الذكي).

An answer is always grounded: it carries the retrieved statutes it relied on, a
grounding score and an explicit ``gap`` when the corpus did not cover the
question. A thread of turns forms a conversation, scoped to one user.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class AssistantCitation(BaseModel):
    """A statute the assistant relied on, with the quoted text."""

    article_label: str = Field(description="مثال: المادة (146)")
    law_title: str = Field(description="اسم القانون")
    law_number: str | None = None
    source_type: str | None = None
    article_id: str | None = None
    quote: str | None = Field(default=None, description="النص المستشهد به حرفياً")
    relevance: str = Field(default="", description="وجه الاستدلال")
    similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    source_url: str | None = None
    text_quality: str | None = None


class AssistantAskRequest(BaseModel):
    """POST /assistant/ask payload."""

    question: str = Field(min_length=2, max_length=4000, description="السؤال القانوني")
    conversation_id: uuid.UUID | None = Field(
        default=None, description="متابعة محادثة قائمة؛ إن لم تُحدد تُنشأ محادثة جديدة"
    )
    case_id: uuid.UUID | None = Field(
        default=None, description="ربط الاستفسار بقضية محددة (اختياري)"
    )
    case_type: str | None = Field(
        default=None, description="توجيه الاسترجاع لنوع الدعوى (مدني/تجاري/جنائي/…)"
    )
    top_k: int = Field(default=8, ge=1, le=20, description="عدد النصوص المسترجعة")
    include_history: bool = Field(
        default=True, description="تضمين سياق المحادثة السابقة في الاستدلال"
    )


class AssistantAnswer(BaseModel):
    """The assistant's grounded answer to one question."""

    conversation_id: uuid.UUID
    message_id: uuid.UUID
    question: str
    answer: str
    reasoning: list[str] = Field(default_factory=list, description="خطوات الاستدلال")
    cited_articles: list[AssistantCitation] = Field(default_factory=list)
    gap: str | None = Field(default=None, description="ما لم تغطّه المدونة القانونية")
    needs_review: bool = Field(
        default=False, description="هل تحتاج الإجابة إلى مراجعة بشرية (إسناد ضعيف)"
    )
    grounding_score: float = Field(default=0.0, ge=0.0, le=1.0)
    model: str | None = None
    provider: str | None = None
    created_at: datetime | None = None


class AssistantMessageOut(ORMModel):
    id: uuid.UUID
    role: str
    content: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    grounding_score: float | None = None
    needs_review: bool = False
    created_at: datetime | None = None


class AssistantConversationOut(ORMModel):
    id: uuid.UUID
    title: str
    case_id: uuid.UUID | None = None
    case_type: str | None = None
    is_archived: bool = False
    message_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class AssistantConversationDetail(BaseModel):
    conversation: AssistantConversationOut
    messages: list[AssistantMessageOut] = Field(default_factory=list)


class AssistantConversationList(BaseModel):
    items: list[AssistantConversationOut]
    total: int
