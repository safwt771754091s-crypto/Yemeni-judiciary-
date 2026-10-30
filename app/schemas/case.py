"""Schemas for case submission (دعوى المدعي / ردود المدعى عليه / الأدلة)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import (
    Admissibility,
    EvidenceStrength,
    EvidenceType,
    ORMModel,
    PartyRole,
    ProofCategory,
)


# ---------------------------------------------------------------------------
# Parties
# ---------------------------------------------------------------------------
class Party(BaseModel):
    name: str = Field(min_length=2, max_length=300, description="الاسم")
    role: PartyRole
    national_id: str | None = Field(default=None, description="الرقم الوطني / السجل")
    address: str | None = Field(default=None, description="العنوان")
    representative: str | None = Field(default=None, description="الوكيل / المحامي")
    representative_id: str | None = None
    phone: str | None = None
    is_legal_person: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Claims & responses
# ---------------------------------------------------------------------------
class Claim(BaseModel):
    """A single plaintiff claim (بند من بنود الدعوى)."""

    title: str = Field(min_length=2, max_length=500, description="موضوع المطلب")
    description: str = Field(min_length=2, description="تفصيل المطلب")
    amount: float | None = Field(default=None, ge=0, description="المبلغ المطالب به")
    currency: str = "YER"
    relief_sought: str | None = Field(default=None, description="الطلبات")
    legal_basis: list[str] = Field(default_factory=list, description="المواد التي يستند إليها المدعي")


class DefendantResponse(BaseModel):
    """A defendant response / defence (دفع أو رد)."""

    claim_ref: str | None = Field(default=None, description="مرجع المطلب المطعون فيه")
    response_type: str = Field(
        default="denial",
        description="denial|admission|objection|procedural_defence|counterclaim|prescription",
    )
    description: str = Field(min_length=2, description="تفصيل الرد")
    amount: float | None = Field(default=None, ge=0)
    legal_basis: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------
class EvidenceItem(BaseModel):
    """Uploaded evidence / supporting document metadata (الأدلة والأسانيد)."""

    id: str | None = Field(default=None, description="معرّف داخلي اختياري")
    submitted_by: PartyRole = PartyRole.plaintiff
    evidence_type: EvidenceType = EvidenceType.document
    title: str = Field(min_length=2, max_length=500, description="اسم الدليل")
    description: str | None = Field(default=None, description="وصف الدليل ووجه الدلالة")
    document_ref: str | None = Field(default=None, description="مسار/رابط المستند")
    mime_type: str | None = None
    file_size: int | None = Field(default=None, ge=0)
    sha256: str | None = None
    extracted_text: str | None = Field(default=None, description="النص المستخرج من المستند")
    proof_category: ProofCategory | None = None
    evidence_date: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceAssessment(BaseModel):
    """Engine output: how an item of evidence is weighed under Yemeni evidence law."""

    evidence_id: str | None = None
    title: str
    submitted_by: PartyRole
    proof_category: ProofCategory | None = None
    strength: EvidenceStrength
    admissibility: Admissibility
    weight: float = Field(ge=0.0, le=1.0)
    supports: list[str] = Field(default_factory=list, description="أي طرف يعززه")
    reasoning: str = Field(description="سبب الترجيح")
    cited_articles: list[str] = Field(default_factory=list)
    requires_verification: bool = False


# ---------------------------------------------------------------------------
# Case submission request / response
# ---------------------------------------------------------------------------
class CaseSubmissionRequest(BaseModel):
    """POST /api/v1/cases/submit payload."""

    case_number: str | None = Field(default=None, max_length=120)
    case_type: str = Field(default="مدني", description="مدني|تجاري|جنائي|أسري|عمالي|إداري")
    court_name: str = "المحكمة الابتدائية"
    circuit: str | None = None
    judge_name: str | None = None
    subject_matter: str = Field(min_length=2, description="موضوع الدعوى")
    claim_value: float | None = Field(default=None, ge=0, description="قيمة الدعوى")
    currency: str = "YER"
    facts_summary: str | None = Field(default=None, description="ملخص الوقائع")

    plaintiff: Party
    defendant: Party

    plaintiff_claims: list[Claim] = Field(min_length=1, description="دعوى المدعي")
    defendant_responses: list[DefendantResponse] = Field(
        default_factory=list, description="ردود المدعى عليه"
    )
    evidence: list[EvidenceItem] = Field(default_factory=list, description="الأدلة والأسانيد")

    # engine controls
    generate_judgment: bool = Field(default=True, description="توليد مسودة الحكم")
    language: str = "ar"
    max_retrieved_articles: int = Field(default=12, ge=1, le=40)
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("plaintiff")
    @classmethod
    def _plaintiff_role(cls, v: Party) -> Party:
        if v.role not in (PartyRole.plaintiff,):
            v = v.model_copy(update={"role": PartyRole.plaintiff})
        return v

    @field_validator("defendant")
    @classmethod
    def _defendant_role(cls, v: Party) -> Party:
        if v.role not in (PartyRole.defendant,):
            v = v.model_copy(update={"role": PartyRole.defendant})
        return v

    @field_validator("defendant_responses")
    @classmethod
    def _unique_claim_refs(cls, v: list[DefendantResponse]) -> list[DefendantResponse]:
        return v


class CaseRecord(ORMModel):
    id: uuid.UUID
    case_number: str | None = None
    case_type: str
    court_name: str
    judge_name: str | None = None
    status: str
    plaintiff_name: str
    defendant_name: str
    subject_matter: str | None = None
    created_at: datetime
    updated_at: datetime


class CaseSubmissionResponse(BaseModel):
    """Full pipeline result returned by /cases/submit."""

    case: CaseRecord
    judgment_id: uuid.UUID | None = None
    judgment: JudgmentDraft | None = None
    judgment_markdown: str | None = None
    retrieved_articles: list[dict[str, Any]] = Field(default_factory=list)
    evidence_analysis: list[EvidenceAssessment] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    pipeline_ms: float
    model: str | None = None
    grounding_score: float | None = None


class CaseJudgmentSummary(BaseModel):
    """Compact view of a stored draft, used in case listings."""

    judgment_id: uuid.UUID
    version: int
    status: str
    grounding_score: float | None = None
    confidence: float | None = None
    model: str | None = None
    created_at: datetime | None = None


class CaseJudgmentListResponse(BaseModel):
    case_id: uuid.UUID
    items: list[CaseJudgmentSummary] = Field(default_factory=list)


class CaseStatusResponse(BaseModel):
    case_id: uuid.UUID
    status: str
    judgments: list[CaseJudgmentSummary] = Field(default_factory=list)


# Resolve the forward reference declared above.
from app.schemas.judgment import JudgmentDraft  # noqa: E402

CaseSubmissionResponse.model_rebuild()
