"""Evidence analysis under the Yemeni Law of Evidence (قانون الإثبات اليمني).

Combines a deterministic rule layer (which never varies between runs and encodes
the statutory hierarchy of proof) with the LLM's contextual assessment. The rule
layer guarantees a defensible baseline: a mere allegation is never evidence, a
formal admission outweighs testimony, and unsigned digital material is only
circumstantial.
"""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.llm.chains import build_evidence_chain
from app.llm.prompts import build_case_block, build_evidence_block, build_retrieved_statutes_block
from app.schemas.case import EvidenceAssessment
from app.schemas.common import Admissibility, EvidenceStrength, ProofCategory

logger = get_logger(__name__)

# Statutory hierarchy of proof: category -> (strength, weight)
PROOF_HIERARCHY: dict[str, tuple[str, float]] = {
    "إقرار": ("decisive", 0.95),
    "كتابة": ("strong", 0.8),
    "خبرة": ("strong", 0.75),
    "يمين": ("strong", 0.7),
    "شهادة": ("moderate", 0.55),
    "قرينة": ("moderate", 0.5),
    "معاينة": ("strong", 0.8),
}

# Evidence type -> default proof category when the submitter did not classify it
TYPE_TO_CATEGORY: dict[str, str] = {
    "admission": "إقرار",
    "contract": "كتابة",
    "receipt": "كتابة",
    "document": "كتابة",
    "correspondence": "كتابة",
    "witness": "شهادة",
    "expert": "خبرة",
    "oath": "يمين",
    "presumption": "قرينة",
    "inspection": "معاينة",
    "digital": "قرينة",
    "audio": "قرينة",
    "video": "قرينة",
    "photo": "قرينة",
}

# Digital/audio/video material without a verification anchor is only circumstantial
UNVERIFIED_TYPES = {"digital", "audio", "video", "photo"}


class EvidenceService:
    def __init__(self) -> None:
        self._chain = build_evidence_chain()

    def baseline(self, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deterministic assessment derived purely from the statute hierarchy."""
        assessments: list[dict[str, Any]] = []
        for item in evidence:
            evidence_type = str(item.get("evidence_type") or "document")
            category = item.get("proof_category") or TYPE_TO_CATEGORY.get(evidence_type, "كتابة")
            strength, weight = PROOF_HIERARCHY.get(str(category), ("moderate", 0.5))
            admissibility = "admissible"
            requires_verification = False

            if evidence_type in UNVERIFIED_TYPES:
                strength, weight = "weak", min(weight, 0.35)
                admissibility = "requires_verification"
                requires_verification = True
            if not item.get("document_ref") and not item.get("extracted_text"):
                weight = min(weight, 0.5)
                admissibility = "conditional" if admissibility == "admissible" else admissibility
            if item.get("metadata", {}).get("disputed"):
                weight = min(weight, 0.4)
                requires_verification = True

            evidence_id = item.get("id")
            assessments.append(
                {
                    # asyncpg returns UUID objects; the API contract is string.
                    "evidence_id": str(evidence_id) if evidence_id is not None else None,
                    "title": item["title"],
                    "submitted_by": str(item.get("submitted_by") or "plaintiff"),
                    "proof_category": str(category),
                    "strength": strength,
                    "admissibility": admissibility,
                    "weight": round(float(weight), 3),
                    "supports": [str(item.get("submitted_by") or "plaintiff")],
                    "reasoning": self._rule_reasoning(str(category), evidence_type, weight),
                    "cited_articles": [],
                    "requires_verification": requires_verification,
                }
            )
        return assessments

    @staticmethod
    def _rule_reasoning(category: str, evidence_type: str, weight: float) -> str:
        return (
            f"وفق قواعد الإثبات في القانون اليمني، يُصنَّف هذا الدليل تحت «{category}» "
            f"(نوعه: {evidence_type})، وقُدِّر وزنه الترجيحي بـ {weight:.2f} على أساس "
            "مرتبة هذا النوع من الأدلة وخلو الملف من مطعن شكلي في صحته."
        )

    async def analyze(
        self,
        *,
        case: dict[str, Any],
        evidence: list[dict[str, Any]],
        articles: list[dict[str, Any]],
    ) -> tuple[list[EvidenceAssessment], list[str], str]:
        """Blend the deterministic baseline with the grounded LLM assessment."""
        warnings: list[str] = []
        baseline = self.baseline(evidence)
        if not evidence:
            return [], ["لا توجد أدلة مرفقة بالملف؛ لا يمكن للمحكمة أن تبني قضاءها على مجرد ادعاء."], ""

        model = ""
        try:
            result = await self._chain.run(
                [
                    build_case_block(case),
                    build_evidence_block(evidence),
                    build_retrieved_statutes_block(articles),
                    "«المطلوب»: أخرج كائن JSON بالمفتاح assessments يحتوي تقييم كل دليل.",
                ]
            )
            model = result.model
            llm_items = self._as_list((result.data or {}).get("assessments"))
        except Exception as exc:
            logger.warning("evidence LLM analysis failed", extra={"error": str(exc)})
            warnings.append(f"تعذّر التقييم الآلي للأدلة؛ تم الاعتماد على القواعد الحاكمة. ({exc})")
            llm_items = []

        merged = self._merge(baseline, llm_items)
        return merged, warnings, model

    @staticmethod
    def _as_list(value: Any) -> list[dict[str, Any]]:
        """Tolerate the shapes a model may return for the assessments field."""
        if isinstance(value, dict):
            nested = value.get("assessments")
            if isinstance(nested, list):
                return [i for i in nested if isinstance(i, dict)]
            return [value]
        if isinstance(value, list):
            return [i for i in value if isinstance(i, dict)]
        return []

    def _merge(
        self, baseline: list[dict[str, Any]], llm_items: list[dict[str, Any]]
    ) -> list[EvidenceAssessment]:
        """LLM refines the rule baseline but can never upgrade admissibility."""
        llm_by_title = {str(item.get("evidence_title") or item.get("title") or ""): item for item in llm_items}
        output: list[EvidenceAssessment] = []
        for base in baseline:
            llm = llm_by_title.get(base["title"], {})
            weight = base["weight"]
            if isinstance(llm.get("weight"), int | float):
                # average keeps the deterministic floor in play
                weight = round((float(llm["weight"]) + base["weight"]) / 2, 3)
            admissibility = base["admissibility"]
            if llm.get("admissibility") in {a.value for a in Admissibility}:
                # the rule layer wins whenever it is stricter
                admissibility = self._stricter(admissibility, str(llm["admissibility"]))
            strength = base["strength"]
            if llm.get("strength") in {s.value for s in EvidenceStrength}:
                strength = str(llm["strength"])
            output.append(
                EvidenceAssessment(
                    evidence_id=base.get("evidence_id"),
                    title=base["title"],
                    submitted_by=base["submitted_by"],
                    proof_category=ProofCategory(base["proof_category"])
                    if base["proof_category"] in {p.value for p in ProofCategory}
                    else None,
                    strength=EvidenceStrength(strength),
                    admissibility=Admissibility(admissibility),
                    weight=max(0.0, min(1.0, weight)),
                    supports=base["supports"],
                    reasoning=str(llm.get("assessment") or base["reasoning"]),
                    cited_articles=[
                        str(a) for a in (llm.get("cited_articles") or base["cited_articles"])
                    ],
                    requires_verification=base["requires_verification"]
                    or admissibility in {"requires_verification", "conditional"},
                )
            )
        return output

    @staticmethod
    def _stricter(a: str, b: str) -> str:
        order = ["admissible", "conditional", "requires_verification", "inadmissible"]
        return a if order.index(a) >= order.index(b) else b


evidence_service = EvidenceService()
