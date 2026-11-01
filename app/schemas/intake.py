"""Schemas for case intake (استلام القضية وتفصيلها وتصنيفها).

The intake flow takes raw papers - uploaded files and/or pasted text - and
returns a fully structured, classified case ready to be decided. These schemas
describe what the platform extracted, what it inferred, and what it needs a
human to confirm.
"""
from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.case import CaseSubmissionResponse, EvidenceItem
from app.schemas.common import ORMModel


# ---------------------------------------------------------------------------
# Uploads and extraction
# ---------------------------------------------------------------------------
class ExtractedDocument(BaseModel):
    """One uploaded file after text extraction."""

    filename: str
    content_type: str | None = None
    size_bytes: int = 0
    suffix: str = ""
    char_count: int = 0
    page_count: int = 0
    used_ocr: bool = False
    text_quality: str = Field(default="low", description="high|medium|low")
    evidence_type: str = Field(default="document", description="نوع الدليل المقترح")
    text_preview: str = Field(default="", description="أول 500 حرف من النص المستخرج")
    text: str | None = Field(default=None, description="النص الكامل (اختياري)")
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class CaseClassificationOut(BaseModel):
    """The court branch the case was routed to."""

    case_type: str
    label_ar: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_types: list[str] = Field(default_factory=list)
    criminal_flag: bool = False
    requires_review: bool = False
    method: str = "lexicon"
    notes: list[str] = Field(default_factory=list)
    scores: list[dict[str, Any]] = Field(default_factory=list)
    signals: list[dict[str, Any]] = Field(default_factory=list)


class CaseDetailsOut(BaseModel):
    """Structured case components recovered from the papers (تفصيل الدعوى)."""

    court_name: str | None = None
    circuit: str | None = None
    case_number: str | None = None
    hearing_date: str | None = None
    judge_name: str | None = None
    subject_matter: str | None = None
    claim_value: float | None = None
    currency: str = "YER"
    facts_summary: str | None = None
    plaintiff_name: str | None = None
    plaintiff_representative: str | None = None
    defendant_name: str | None = None
    defendant_representative: str | None = None
    claims: list[str] = Field(default_factory=list)
    defences: list[str] = Field(default_factory=list)
    legal_basis: list[str] = Field(default_factory=list)
    witnesses: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    fields_found: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------
class CaseClassifyRequest(BaseModel):
    """POST /cases/classify - classify a case from text alone."""

    text: str = Field(min_length=10, description="نص القضية أو الوقائع")
    declared_case_type: str | None = Field(
        default=None, description="النوع المصرَّح به إن وُجد (يُعالج كقرينة لا كحكم)"
    )
    subject_matter: str | None = None
    use_llm: bool = Field(default=False, description="استخدام النموذج اللغوي لتحسين التصنيف")


class CaseExtractRequest(BaseModel):
    """POST /cases/extract - structure a case from text alone (no files)."""

    text: str = Field(min_length=10, description="نص القضية الكامل")
    declared_case_type: str | None = None


class CaseExtractResponse(BaseModel):
    details: CaseDetailsOut
    classification: CaseClassificationOut
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Intake response
# ---------------------------------------------------------------------------
class IntakeResponse(BaseModel):
    """Result of POST /cases/intake.

    The response is deliberately rich: the caller sees exactly what the platform
    read (documents), how it routed the case (classification), what it
    understood (details), the request it assembled (structured_case), and - when
    requested - the drafted judgment produced by the full pipeline.
    """

    intake_id: uuid.UUID
    status: str = Field(description="extracted|structured|judgment_ready|needs_review")
    documents: list[ExtractedDocument] = Field(default_factory=list)
    classification: CaseClassificationOut
    details: CaseDetailsOut
    structured_case: dict[str, Any] = Field(
        default_factory=dict, description="الحقول التي ستُقدَّم إلى محرك الحكم"
    )
    evidence: list[EvidenceItem] = Field(default_factory=list)
    combined_text: str | None = Field(
        default=None, description="النص الموحّد المستخرج من جميع الملفات"
    )
    case_id: uuid.UUID | None = None
    judgment_id: uuid.UUID | None = None
    pipeline: CaseSubmissionResponse | None = None
    warnings: list[str] = Field(default_factory=list)
    needs_human_review: list[str] = Field(
        default_factory=list, description="حقول يلزم تأكيدها بشرياً"
    )
    processing_ms: float = 0.0


class IntakeRecord(ORMModel):
    id: uuid.UUID
    status: str
    case_type: str | None = None
    case_id: uuid.UUID | None = None
    document_count: int = 0
    created_at: Any = None
