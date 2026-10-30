"""Schemas for the structured Yemeni judgment draft (مسودة الحكم)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import JudgmentStatus, ORMModel


# ---------------------------------------------------------------------------
# الديباجة
# ---------------------------------------------------------------------------
class JudgmentPreamble(BaseModel):
    court_name: str = Field(description="اسم المحكمة")
    circuit: str | None = Field(default=None, description="الدائرة")
    case_number: str | None = Field(default=None, description="رقم القضية")
    hearing_date: str | None = Field(default=None, description="تاريخ الجلسة")
    judgment_date: str | None = Field(default=None, description="تاريخ الحكم")
    judge_name: str | None = Field(default=None, description="القاضي")
    jurisdiction: str = Field(default="الجمهورية اليمنية")
    plaintiff: dict[str, Any] = Field(default_factory=dict)
    defendant: dict[str, Any] = Field(default_factory=dict)
    attendance: str | None = Field(default=None, description="حضور الأطراف")
    subject_matter: str | None = None


# ---------------------------------------------------------------------------
# الوقائع
# ---------------------------------------------------------------------------
class FactsClaim(BaseModel):
    title: str
    description: str
    amount: float | None = None
    currency: str | None = None
    supported_by: list[str] = Field(default_factory=list)


class FactsDefence(BaseModel):
    claim_ref: str | None = None
    response_type: str
    description: str
    legal_basis: list[str] = Field(default_factory=list)


class JudgmentFacts(BaseModel):
    narrative: str = Field(description="سرد الوقائع بإيجاز")
    plaintiff_claims: list[FactsClaim] = Field(default_factory=list)
    defendant_responses: list[FactsDefence] = Field(default_factory=list)
    procedural_history: list[str] = Field(default_factory=list)
    disputed_points: list[str] = Field(default_factory=list, description="نقاط الخلاف")
    undisputed_points: list[str] = Field(default_factory=list, description="الوقائع غير المتنازع عليها")


# ---------------------------------------------------------------------------
# الأسباب والحيثيات
# ---------------------------------------------------------------------------
class CitedArticle(BaseModel):
    article_id: str | None = None
    article_label: str = Field(description="مثال: المادة (1045)")
    law_title: str = Field(description="اسم القانون")
    law_number: str | None = None
    source_type: str | None = None
    quote: str | None = Field(default=None, description="النص المستشهد به")
    relevance: str = Field(description="وجه الاستدلال")
    similarity: float | None = Field(default=None, ge=0.0, le=1.0)


class ReasoningStep(BaseModel):
    step: int = Field(ge=1)
    question: str = Field(description="المسألة القانونية محل البحث")
    analysis: str = Field(description="التحليل القانوني")
    conclusion: str = Field(description="النتيجة")
    cited_articles: list[str] = Field(default_factory=list, description="أرقام المواد المستند إليها")


class EvidenceAnalysis(BaseModel):
    evidence_title: str
    submitted_by: str
    proof_category: str | None = None
    admissibility: str
    weight: float = Field(ge=0.0, le=1.0)
    assessment: str = Field(description="تقييم الدليل وفق قانون الإثبات")
    cited_articles: list[str] = Field(default_factory=list)


class JudgmentReasoning(BaseModel):
    """الأسباب والحيثيات."""

    summary: str = Field(description="خلاصة الأسباب")
    burden_of_proof: str | None = Field(default=None, description="توزيع عبء الإثبات")
    evidence_analysis: list[EvidenceAnalysis] = Field(default_factory=list)
    legal_analysis: list[ReasoningStep] = Field(default_factory=list)
    cited_articles: list[CitedArticle] = Field(default_factory=list)
    defendant_defences_assessment: list[str] = Field(default_factory=list)
    precedents: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# المنطوق
# ---------------------------------------------------------------------------
class VerdictItem(BaseModel):
    claim_ref: str | None = None
    decision: str = Field(description="قبول|رفض|إلزام|عدم اختصاص|بطلان|شطب")
    subject: str = Field(description="موضوع الفصل")
    detail: str = Field(description="تفصيل المنطوق")
    amount: float | None = None
    currency: str | None = None
    legal_basis: list[str] = Field(default_factory=list)


class JudgmentVerdict(BaseModel):
    """المنطوق."""

    summary: str = Field(description="ملخص المنطوق")
    items: list[VerdictItem] = Field(default_factory=list)
    costs: str | None = Field(default=None, description="المصاريف")
    final: bool = Field(default=True, description="حكم ابتدائي/نهائي")
    appeal_deadline: str | None = Field(default=None, description="ميعاد الطعن")


# ---------------------------------------------------------------------------
# Full draft
# ---------------------------------------------------------------------------
class JudgmentDraft(BaseModel):
    preamble: JudgmentPreamble
    facts: JudgmentFacts
    reasoning: JudgmentReasoning
    verdict: JudgmentVerdict

    @model_validator(mode="after")
    def _verdict_grounded(self) -> JudgmentDraft:
        if not self.verdict.items:
            raise ValueError("المنطوق لا يحتوي على أي بنود")
        return self


class JudgmentDraftEnvelope(BaseModel):
    """Structured draft + grounding metadata produced by the engine."""

    draft: JudgmentDraft
    citations: list[CitedArticle] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    grounding_score: float = Field(default=0.0, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    model: str | None = None
    unverified_citations: list[str] = Field(default_factory=list)


class JudgmentRecord(ORMModel):
    id: uuid.UUID
    case_id: uuid.UUID
    version: int
    status: JudgmentStatus
    language: str
    model: str | None = None
    confidence: float | None = None
    grounding_score: float | None = None
    markdown_ar: str | None = None
    created_at: datetime


class JudgmentResponse(BaseModel):
    judgment_id: uuid.UUID
    case_id: uuid.UUID
    version: int
    status: JudgmentStatus
    draft: JudgmentDraft
    markdown_ar: str
    citations: list[CitedArticle] = Field(default_factory=list)
    confidence: float
    grounding_score: float
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
