"""Output schemas for the LLM chains.

Each chain declares the JSON object it expects back. ``LegalChain`` renders the
schema into the request, so the model is told the exact key names instead of
guessing them - without this, a model that follows the Arabic instructions
literally invents Arabic keys ("الديباجة", "الوقائع", ...) that the English
schema cannot read, and the structured draft silently degrades to fallbacks.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Tolerant(BaseModel):
    """Ignore keys the model adds; missing optional keys keep their defaults."""

    model_config = ConfigDict(extra="ignore")


class QueryExpansionOutput(_Tolerant):
    queries: list[str] = Field(description="استعلامات بحث قانونية دقيقة (2-6)")


class ClassificationOutput(_Tolerant):
    case_type: str = Field(description="نوع الدعوى المختار من المرشحين")
    reason: str | None = Field(default=None, description="سبب اختيار هذا النوع")


class EvidenceAssessmentOutput(_Tolerant):
    evidence_title: str = Field(description="عنوان الدليل كما ورد في الملف")
    submitted_by: str = Field(description="الطرف المُقدِّم للدليل")
    proof_category: str | None = Field(default=None, description="مرتبة الدليل في قانون الإثبات")
    admissibility: str = Field(description="admissible|conditional|requires_verification|inadmissible")
    strength: str | None = Field(default=None, description="decisive|strong|moderate|weak")
    weight: float | None = Field(default=None, ge=0.0, le=1.0)
    assessment: str = Field(description="تقييم الدليل وفق قانون الإثبات")
    cited_articles: list[str] = Field(default_factory=list)


class EvidenceAnalysisOutput(_Tolerant):
    assessments: list[EvidenceAssessmentOutput] = Field(default_factory=list)


class ReasoningOutput(_Tolerant):
    summary: str = Field(description="خلاصة الأسباب")
    burden_of_proof: str | None = None
    evidence_analysis: list[dict[str, Any]] = Field(default_factory=list)
    legal_analysis: list[dict[str, Any]] = Field(default_factory=list)
    cited_articles: list[dict[str, Any]] = Field(default_factory=list)
    defendant_defences_assessment: list[str] = Field(default_factory=list)
    precedents: list[str] = Field(default_factory=list)


class AssistantOutput(_Tolerant):
    answer: str = Field(description="الإجابة القانونية المسندة إلى النصوص المسترجعة")
    reasoning: list[str] = Field(default_factory=list)
    cited_articles: list[dict[str, Any]] = Field(default_factory=list)
    gap: str | None = Field(default=None, description="ما لا تغطيه النصوص المسترجعة")


class DraftOutput(_Tolerant):
    markdown: str = Field(description="نص المسودة بصيغة Markdown")
    notes: list[str] = Field(default_factory=list)
    cited_articles: list[dict[str, Any]] = Field(default_factory=list)
