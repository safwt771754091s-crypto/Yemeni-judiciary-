"""Shared schema primitives."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class LegalSourceType(str, Enum):
    civil_law = "civil_law"
    commercial_law = "commercial_law"
    evidence_law = "evidence_law"
    civil_procedure = "civil_procedure"
    criminal_law = "criminal_law"
    criminal_procedure = "criminal_procedure"
    family_law = "family_law"
    labour_law = "labour_law"
    constitution = "constitution"
    executive_regulation = "executive_regulation"
    judicial_precedent = "judicial_precedent"
    fatwa = "fatwa"
    other = "other"


SOURCE_LABELS_AR: dict[str, str] = {
    "civil_law": "القانون المدني",
    "commercial_law": "القانون التجاري",
    "evidence_law": "قانون الإثبات",
    "civil_procedure": "قانون المرافعات المدنية",
    "criminal_law": "قانون الجرائم والعقوبات",
    "criminal_procedure": "قانون الإجراءات الجزائية",
    "family_law": "قانون الأحوال الشخصية",
    "labour_law": "قانون العمل",
    "constitution": "الدستور",
    "executive_regulation": "اللائحة التنفيذية",
    "judicial_precedent": "مبدأ قضائي",
    "fatwa": "فتوى",
    "other": "أخرى",
}


class PartyRole(str, Enum):
    plaintiff = "plaintiff"
    defendant = "defendant"
    court = "court"
    third_party = "third_party"


class EvidenceType(str, Enum):
    document = "document"
    contract = "contract"
    witness = "witness"
    expert = "expert"
    receipt = "receipt"
    correspondence = "correspondence"
    audio = "audio"
    video = "video"
    photo = "photo"
    digital = "digital"
    oath = "oath"
    admission = "admission"
    presumption = "presumption"
    inspection = "inspection"


class ProofCategory(str, Enum):
    """Categories of proof under the Yemeni Law of Evidence."""

    kitaba = "كتابة"          # written evidence
    shahada = "شهادة"          # testimony
    qarina = "قرينة"           # circumstantial / presumption
    iqrar = "إقرار"            # admission / confession
    yamin = "يمين"             # oath
    khibra = "خبرة"            # expert opinion
    muayana = "معاينة"         # inspection / site view


class EvidenceStrength(str, Enum):
    decisive = "decisive"
    strong = "strong"
    moderate = "moderate"
    weak = "weak"
    inadmissible = "inadmissible"


class Admissibility(str, Enum):
    admissible = "admissible"
    inadmissible = "inadmissible"
    conditional = "conditional"
    requires_verification = "requires_verification"


class JudgmentStatus(str, Enum):
    draft = "draft"
    ai_generated = "ai_generated"
    human_reviewed = "human_reviewed"
    approved = "approved"
    rejected = "rejected"
    final = "final"


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class Pagination(BaseModel):
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


Language = Literal["ar", "en"]
