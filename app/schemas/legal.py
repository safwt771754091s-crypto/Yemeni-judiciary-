"""Schemas for legal documents, articles and retrieval."""
from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.text_utils import format_law_title
from app.schemas.common import LegalSourceType, ORMModel


class LegalDocumentCreate(BaseModel):
    source_type: LegalSourceType
    title_ar: str = Field(min_length=2, max_length=500)
    title_en: str | None = None
    law_number: str | None = Field(default=None, max_length=64)
    promulgation_year: int | None = Field(default=None, ge=1900, le=2100)
    issuing_authority: str = "مجلس النواب"
    jurisdiction: str = "الجمهورية اليمنية"
    source_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LegalDocumentOut(ORMModel):
    id: uuid.UUID
    source_type: LegalSourceType
    title_ar: str
    title_en: str | None = None
    law_number: str | None = None
    promulgation_year: int | None = None
    issuing_authority: str | None = None
    jurisdiction: str
    source_url: str | None = None


class LegalDocumentUpdate(BaseModel):
    """Editable metadata of a legal document (law)."""

    title_ar: str | None = Field(default=None, min_length=2, max_length=500)
    title_en: str | None = Field(default=None, max_length=500)
    law_number: str | None = Field(default=None, max_length=64)
    promulgation_year: int | None = Field(default=None, ge=1900, le=2100)
    issuing_authority: str | None = Field(default=None, max_length=200)
    source_url: str | None = Field(default=None, max_length=1000)
    section_id: uuid.UUID | None = None


class LegalArticleOut(ORMModel):
    id: uuid.UUID
    document_id: uuid.UUID
    source_type: LegalSourceType
    article_number: str | None = None
    article_label: str | None = None
    chapter: str | None = None
    heading_ar: str | None = None
    content_ar: str
    token_count: int | None = None


class RetrievedArticle(BaseModel):
    """A statute article returned by the hybrid retriever."""

    article_id: str
    document_id: str
    source_type: LegalSourceType
    law_title: str = Field(description="اسم القانون")
    law_number: str | None = None
    article_number: str | None = None
    article_label: str
    chapter: str | None = None
    heading: str | None = None
    content: str
    similarity: float = Field(ge=0.0, le=1.0)
    fused_score: float = 0.0
    vector_rank: int | None = None
    keyword_rank: int | None = None
    source_url: str | None = Field(
        default=None, description="رابط المصدر الرسمي للنص"
    )
    text_quality: str | None = Field(
        default=None,
        description="جودة النص: high (نص رسمي منسوخ) أو medium/low (مستخرج آلياً من نسخة ممسوحة)",
    )
    verbatim: bool | None = Field(
        default=None, description="هل النص منقول حرفياً عن المصدر الرسمي"
    )

    @property
    def citation(self) -> str:
        return f"{self.article_label} من {format_law_title(self.law_title, self.law_number)}"


class RetrievalQuery(BaseModel):
    query: str = Field(min_length=2, max_length=4000)
    top_k: int = Field(default=8, ge=1, le=50)
    source_types: list[LegalSourceType] | None = None
    law_title_contains: str | None = None
    document_id: uuid.UUID | None = None
    min_similarity: float = Field(default=0.0, ge=0.0, le=1.0)
    rerank: bool = True


class RetrievalResponse(BaseModel):
    query: str
    results: list[RetrievedArticle]
    count: int
    strategy: str = "hybrid_rrf"
    latency_ms: float | None = None


class ArticleSearchQuery(BaseModel):
    """Public statute lookup by article number and/or a text fragment."""

    query: str | None = Field(default=None, max_length=400, description="جزء من نص المادة")
    article_number: str | None = Field(
        default=None, max_length=20, description="رقم المادة (يقبل الأرقام العربية)"
    )
    source_types: list[LegalSourceType] | None = None
    limit: int = Field(default=20, ge=1, le=100)


class ArticleSearchResponse(BaseModel):
    query: str | None = None
    article_number: str | None = None
    results: list[RetrievedArticle]
    count: int
    latency_ms: float | None = None


class IngestTextRequest(BaseModel):
    document: LegalDocumentCreate
    text: str = Field(min_length=10)
    max_chars_per_chunk: int = Field(default=1800, ge=400, le=8000)
    chunk_overlap: int = Field(default=200, ge=0, le=1000)
    default_article_number: str | None = None

    @field_validator("chunk_overlap")
    @classmethod
    def _overlap_lt_chunk(cls, v: int, info) -> int:
        max_chars = info.data.get("max_chars_per_chunk", 1800)
        if v >= max_chars:
            raise ValueError("chunk_overlap يجب أن يكون أقل من max_chars_per_chunk")
        return v


class IngestResponse(BaseModel):
    document_id: uuid.UUID
    title_ar: str
    chunks_created: int
    chunks_updated: int = 0
    articles_detected: int
    tokens_total: int
    embedding_model: str
    duration_ms: float


class CorpusStatsItem(BaseModel):
    source_type: str
    source_label_ar: str
    documents: int
    articles: int
    embedded: int
    total_tokens: int


class CorpusStatsResponse(BaseModel):
    items: list[CorpusStatsItem]
    total_articles: int
    total_documents: int
    embedding_model: str
    database_connected: bool
