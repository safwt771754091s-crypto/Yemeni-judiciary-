"""Schemas for the commercial litigation & financial extraction engine.

A commercial dispute turns on figures: what was contracted, what was paid, what
is admitted, and what the court awards. These schemas model that chain so the
draft can present a defensible financial breakdown instead of prose.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import CommercialEvidenceKind


class MoneyAmount(BaseModel):
    """A single monetary figure with its provenance and reading."""

    amount: float = Field(ge=0)
    currency: str = "YER"
    source: str = Field(default="numeric", description="numeric|written")
    span: str | None = Field(default=None, description="النص الأصلي الذي ورد فيه المبلغ")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class PaymentMilestone(BaseModel):
    """A contractual payment deadline / instalment (أجل أو قسط سداد)."""

    description: str = Field(description="وصف الاستحقاق")
    amount: float | None = Field(default=None, ge=0)
    currency: str = "YER"
    due_date: str | None = Field(default=None, description="تاريخ/أجل الاستحقاق")
    due_days: int | None = Field(default=None, ge=0, description="المدة بالأيام إن ذُكرت")


class PenaltyClause(BaseModel):
    """A contractual penalty / delay fine (شرط جزائي / غرامة تأخيرية)."""

    clause_type: str = Field(default="penalty", description="penalty|delay_fine|liquidated_damages")
    description: str = Field(description="نص الشرط الجزائي")
    rate_percent: float | None = Field(default=None, ge=0, description="النسبة المئوية")
    rate_period: str | None = Field(default=None, description="يومي|شهري|سنوي")
    amount: float | None = Field(default=None, ge=0)
    currency: str = "YER"
    capped: bool | None = Field(default=None, description="هل الشرط الجزائي محدود بسقف؟")
    span: str | None = None


class CommercialClause(BaseModel):
    """A material contractual term relevant to a commercial dispute."""

    clause_type: str = Field(
        description="payment_term|delivery|interest|penalty|jurisdiction|good_faith|termination|other"
    )
    text: str = Field(description="نص الشرط أو خلاصته")
    amount: float | None = Field(default=None, ge=0)
    currency: str | None = None


class CommercialTerms(BaseModel):
    """Contractual terms extracted from the case and its documents."""

    clauses: list[CommercialClause] = Field(default_factory=list)
    penalties: list[PenaltyClause] = Field(default_factory=list)
    milestones: list[PaymentMilestone] = Field(default_factory=list)
    payment_deadline_days: int | None = Field(default=None, ge=0)
    interest_rate_percent: float | None = Field(default=None, ge=0)
    notes: list[str] = Field(default_factory=list)


class FinancialLine(BaseModel):
    """One disputed financial item, from claim to award."""

    claim_ref: str | None = Field(default=None, description="مرجع البند في الدعوى")
    subject: str = Field(description="موضوع البند المالي")
    claimed_amount: float | None = Field(default=None, ge=0)
    acknowledged_amount: float | None = Field(default=None, ge=0, description="المبلغ المعترف به")
    awarded_amount: float | None = Field(default=None, ge=0, description="المبلغ المقضي به")
    currency: str = "YER"
    basis: str | None = Field(default=None, description="سند البند (المادة/العقد)")
    supported_by: list[str] = Field(default_factory=list, description="أدلة تساند البند")


class FinancialSummary(BaseModel):
    """The «Commercial Financial Summary» section of the draft.

    Claimed / acknowledged / awarded are kept distinct on purpose: conflating
    them is how a draft ends up awarding an amount nobody pleaded.
    """

    currency: str = "YER"
    lines: list[FinancialLine] = Field(default_factory=list)
    total_claimed: float = Field(default=0.0, ge=0)
    total_acknowledged: float = Field(default=0.0, ge=0)
    total_awarded: float = Field(default=0.0, ge=0)
    reconciled: bool | None = Field(
        default=None, description="هل يطابق المبلغ الرقمي المبلغ المكتوب؟"
    )
    reconciliation_note: str | None = None
    evidence_kinds: list[CommercialEvidenceKind] = Field(default_factory=list)
    narrative: str | None = Field(default=None, description="خلاصة مالية")


class CommercialExtraction(BaseModel):
    """Everything the commercial engine recovers from a case file."""

    is_commercial: bool = False
    financial_summary: FinancialSummary = Field(default_factory=FinancialSummary)
    terms: CommercialTerms = Field(default_factory=CommercialTerms)
    warnings: list[str] = Field(default_factory=list)


class CommercialIntake(BaseModel):
    """Optional structured commercial details a client may supply explicitly.

    The engine extracts these from the filing text too; anything supplied here
    takes precedence, so a bank or a corporate legal team that already holds the
    contract terms does not have to rely on the parser.
    """

    penalties: list[PenaltyClause] = Field(default_factory=list)
    milestones: list[PaymentMilestone] = Field(default_factory=list)
    payment_deadline_days: int | None = Field(default=None, ge=0)
    interest_rate_percent: float | None = Field(default=None, ge=0)
    clauses: list[CommercialClause] = Field(default_factory=list)
