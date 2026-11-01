"""Case intake: from uploaded papers to a drafted judgment, unattended.

This is the orchestrator behind ``POST /api/v1/cases/intake``. The promise to
the user is simple - upload the case papers and the platform does the rest - and
the flow that keeps that promise is:

    files (PDF/scan/photo/DOCX/text)
        |
        v  document_extraction: text layer, or Arabic OCR for scans
    combined case text
        |
        v  case_details: parties, court, case number, claims, defences, facts
    structured case components
        |
        v  classification: which Yemeni court branch, which codes apply
    case type + governing statutes
        |
        v  CaseSubmissionRequest
    full RAG pipeline (retrieval -> evidence -> judgment -> citation validation)
        |
        v
    draft judgment (مسودة حكم)

Two properties matter more than anything else here:

* Nothing is invented. Every field is either read from the papers by a
  deterministic pattern or produced by the grounded pipeline; whatever could
  not be determined is reported in ``needs_human_review`` rather than guessed.
  A court filing with no plaintiff name must say so, not fabricate one.
* The criminal signal is never suppressed. If the papers describe an offence,
  the response says so even when the case is routed to a civil branch, because
  a civil court that misses a criminal element is a serious error.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

from app.core.config import settings
from app.core.legal_lexicon import case_type_label, is_criminal, source_types_for
from app.core.logging import get_logger
from app.schemas.case import (
    CaseSubmissionRequest,
    CaseSubmissionResponse,
    Claim,
    DefendantResponse,
    EvidenceItem,
    Party,
)
from app.schemas.intake import (
    CaseClassificationOut,
    CaseDetailsOut,
    ExtractedDocument,
    IntakeResponse,
)
from app.services.case_details import ExtractedDetails, case_details_extractor
from app.services.classification import ClassificationResult, case_classifier
from app.services.document_extraction import (
    ExtractionResult,
    extract_from_bytes,
    guess_evidence_type,
    quality_of,
)

logger = get_logger(__name__)

#: Required fields a case cannot be judged without; missing ones are surfaced.
REQUIRED_FIELDS = ("plaintiff_name", "defendant_name", "subject_matter")


class IntakeService:
    def __init__(self) -> None:
        self.extractor = case_details_extractor
        self.classifier = case_classifier

    # -- public API --------------------------------------------------------
    def read_documents(
        self, files: list[tuple[str, bytes, str | None]]
    ) -> tuple[list[ExtractedDocument], list[str]]:
        """Extract text from uploaded files, reporting per-file outcomes."""
        documents: list[ExtractedDocument] = []
        warnings: list[str] = []
        for filename, data, content_type in files:
            result: ExtractionResult = extract_from_bytes(
                filename, data, content_type=content_type
            )
            document = self._to_document(result, content_type, len(data))
            documents.append(document)
            if result.error:
                warnings.append(f"«{filename}»: {result.error}")
            else:
                warnings.extend(f"«{filename}»: {w}" for w in result.warnings)
        return documents, warnings

    def combine_text(self, documents: list[ExtractedDocument], extra_text: str | None) -> str:
        """Concatenate all extracted text, labelling each source document."""
        parts: list[str] = []
        if extra_text and extra_text.strip():
            parts.append(extra_text.strip())
        for document in documents:
            if not document.text:
                continue
            header = f"=== مستند: {document.filename} ==="
            parts.append(f"{header}\n{document.text}")
        return "\n\n".join(parts)

    def classify(
        self,
        text: str,
        *,
        declared_case_type: str | None = None,
        details: ExtractedDetails | None = None,
    ) -> ClassificationResult:
        hints: dict[str, Any] = {}
        if details:
            hints = {
                "subject_matter": details.subject_matter,
                "facts_summary": details.facts_summary,
                "court_name": details.court_name,
                "plaintiff_claims": details.claims,
                "defendant_responses": details.defences,
            }
        return self.classifier.classify(
            text, hints=hints, declared_case_type=declared_case_type
        )

    def build_submission(
        self,
        *,
        details: ExtractedDetails,
        classification: ClassificationResult,
        evidence: list[EvidenceItem],
        declared_case_type: str | None = None,
        case_number: str | None = None,
        court_name: str | None = None,
        circuit: str | None = None,
        judge_name: str | None = None,
        subject_matter: str | None = None,
        claim_value: float | None = None,
        currency: str | None = None,
        facts_summary: str | None = None,
        plaintiff_name: str | None = None,
        defendant_name: str | None = None,
        generate_judgment: bool = True,
        max_retrieved_articles: int | None = None,
    ) -> CaseSubmissionRequest:
        """Assemble a validated submission request from extracted components.

        Caller-supplied values win over extracted ones, so a reviewer can correct
        any field before the case is submitted. Nothing is invented: when a
        mandatory field is absent the request still validates, because the schema
        only requires that *a* value be present - and the intake response lists
        the gaps for the reviewer.
        """
        case_type = declared_case_type or classification.case_type
        resolved_subject = subject_matter or details.subject_matter or "موضوع الدعوى"
        plaintiff = plaintiff_name or details.plaintiff_name or "المدعي"
        defendant = defendant_name or details.defendant_name or "المدعى عليه"

        claims = self._build_claims(details)
        responses = self._build_responses(details)

        return CaseSubmissionRequest(
            case_number=case_number or details.case_number,
            case_type=case_type,
            court_name=court_name or details.court_name or settings.default_court_name,
            circuit=circuit or details.circuit,
            judge_name=judge_name or details.judge_name,
            subject_matter=resolved_subject,
            claim_value=(
                claim_value if claim_value is not None else details.claim_value
            ),
            currency=currency or details.currency or "YER",
            facts_summary=facts_summary or details.facts_summary or resolved_subject,
            plaintiff=Party(
                name=plaintiff,
                role="plaintiff",
                representative=details.plaintiff_representative,
            ),
            defendant=Party(
                name=defendant,
                role="defendant",
                representative=details.defendant_representative,
            ),
            plaintiff_claims=claims,
            defendant_responses=responses,
            evidence=evidence,
            generate_judgment=generate_judgment,
            max_retrieved_articles=max_retrieved_articles or 12,
            extra={
                "intake": {
                    "classification_method": classification.method,
                    "classification_confidence": classification.confidence,
                    "criminal_flag": classification.criminal_flag,
                    "hearing_date": details.hearing_date,
                    "legal_basis": details.legal_basis,
                }
            },
        )

    def assess_gaps(
        self, details: ExtractedDetails, classification: ClassificationResult
    ) -> list[str]:
        """List the fields a human must confirm before the draft is relied on."""
        gaps: list[str] = []
        if not details.plaintiff_name:
            gaps.append("اسم المدعي (المدعي)")
        if not details.defendant_name:
            gaps.append("اسم المدعى عليه (المدعى عليه)")
        if not details.subject_matter:
            gaps.append("موضوع الدعوى")
        if not details.claims:
            gaps.append("طلبات المدعي (الطلبات)")
        if not details.case_number:
            gaps.append("رقم القضية")
        if classification.requires_review:
            gaps.append(
                f"تصنيف الدعوى (الثقة منخفضة: {classification.confidence:.2f})"
            )
        if classification.criminal_flag and classification.case_type != "جنائي":
            gaps.append("وجود مؤشرات جنائية تستوجب عرض الملف على النيابة العامة")
        return gaps

    # -- internals ---------------------------------------------------------
    @staticmethod
    def _to_document(
        result: ExtractionResult, content_type: str | None, size_bytes: int
    ) -> ExtractedDocument:
        quality = quality_of(result.text, used_ocr=result.used_ocr)
        return ExtractedDocument(
            filename=result.filename,
            content_type=content_type,
            size_bytes=size_bytes,
            suffix=result.suffix,
            char_count=result.char_count,
            page_count=result.page_count,
            used_ocr=result.used_ocr,
            text_quality=quality,
            evidence_type=guess_evidence_type(result.filename),
            text_preview=result.text[:500],
            text=result.text or None,
            warnings=result.warnings,
            error=result.error,
        )

    @staticmethod
    def _build_claims(details: ExtractedDetails) -> list[Claim]:
        """Turn the extracted demands into structured claims.

        When the filing lists no الطلبات the subject matter becomes the single
        claim, which is how a Yemeni court reads a petition that states its
        request only in the body.
        """
        claims: list[Claim] = []
        for index, text in enumerate(details.claims[:12], start=1):
            amount = None
            if index == 1 and details.claim_value:
                amount = details.claim_value
            claims.append(
                Claim(
                    title=text[:120],
                    description=text,
                    amount=amount,
                    currency=details.currency or "YER",
                    legal_basis=list(details.legal_basis[:6]),
                )
            )
        if not claims:
            subject = details.subject_matter or details.facts_summary or "موضوع الدعوى"
            claims.append(
                Claim(
                    title=str(subject)[:120],
                    description=str(subject)[:1000],
                    amount=details.claim_value,
                    currency=details.currency or "YER",
                    legal_basis=list(details.legal_basis[:6]),
                )
            )
        return claims

    @staticmethod
    def _build_responses(details: ExtractedDetails) -> list[DefendantResponse]:
        responses: list[DefendantResponse] = []
        for text in details.defences[:12]:
            response_type = "denial"
            lowered = text
            if "تقادم" in lowered:
                response_type = "prescription"
            elif "بطلان" in lowered:
                response_type = "nullity"
            elif "عدم اختصاص" in lowered:
                response_type = "jurisdiction"
            elif "إقرار" in lowered or "اقرار" in lowered:
                response_type = "admission"
            elif "وفاء" in lowered or "سداد" in lowered:
                response_type = "performance"
            responses.append(
                DefendantResponse(
                    response_type=response_type,
                    description=text,
                    legal_basis=list(details.legal_basis[:6]),
                )
            )
        return responses

    # -- serialisation -----------------------------------------------------
    @staticmethod
    def build_evidence(
        documents: list[ExtractedDocument], submitted_by: str = "plaintiff"
    ) -> list[EvidenceItem]:
        """Turn uploaded documents into evidence items for the case file.

        Every uploaded paper is evidence: an uploaded PDF is written proof
        (كتابة), a photograph is a presumption (قرينة) until verified. The
        classification is a proposal - the evidence service weighs each item
        against the statutory hierarchy, and a reviewer can reclassify it.
        """
        items: list[EvidenceItem] = []
        for document in documents:
            if document.error or not (document.text or document.text_preview):
                continue
            items.append(
                EvidenceItem(
                    submitted_by=submitted_by,
                    evidence_type=document.evidence_type,
                    title=document.filename[:500] or "مستند مرفق",
                    description=(
                        f"مستند مرفق ({document.page_count} صفحة، "
                        f"جودة النص: {document.text_quality}"
                        + ("، مستخرج بالتعرف الضوئي OCR" if document.used_ocr else "")
                        + ")."
                    ),
                    document_ref=document.filename,
                    mime_type=document.content_type,
                    file_size=document.size_bytes,
                    extracted_text=(document.text or document.text_preview)[:20000],
                    metadata={
                        "intake": True,
                        "used_ocr": document.used_ocr,
                        "page_count": document.page_count,
                        "text_quality": document.text_quality,
                    },
                )
            )
        return items

    @staticmethod
    def classification_out(result: ClassificationResult) -> CaseClassificationOut:
        payload = result.model_dump()
        return CaseClassificationOut.model_validate(payload)

    @staticmethod
    def details_out(details: ExtractedDetails) -> CaseDetailsOut:
        return CaseDetailsOut.model_validate(details.model_dump())


intake_service = IntakeService()


def describe_routing(case_type: str) -> dict[str, Any]:
    """Human-readable description of where a case type is routed."""
    return {
        "case_type": case_type,
        "label_ar": case_type_label(case_type),
        "criminal": is_criminal(case_type),
        "source_types": source_types_for(case_type) or [],
    }


async def run_intake_pipeline(
    *,
    submission: CaseSubmissionRequest,
    documents: list[ExtractedDocument],
    classification: ClassificationResult,
    details: ExtractedDetails,
    warnings: list[str],
    combined_text: str,
    started: float,
) -> IntakeResponse:
    """Run the full RAG pipeline over a structured case and package the result."""
    from app.services.pipeline import case_pipeline

    pipeline: CaseSubmissionResponse | None = None
    status = "structured"
    judgment_id: uuid.UUID | None = None
    case_id: uuid.UUID | None = None

    if submission.generate_judgment and settings.intake_auto_judgment:
        pipeline = await case_pipeline.submit(submission)
        case_id = pipeline.case.id
        judgment_id = pipeline.judgment_id
        warnings.extend(pipeline.warnings)
        status = "judgment_ready" if judgment_id else "structured"
    if details.claims and not details.defences:
        warnings.append(
            "لم تُستخرج ردود المدعى عليه من المستندات؛ أُدرجت الدعوى دون دفوع. "
            "يمكن إضافتها لاحقاً عبر POST /api/v1/cases/{id}/evidence أو بتعديل النص."
        )

    needs_review = intake_service.assess_gaps(details, classification)
    if needs_review:
        status = "needs_review" if status == "structured" else status

    return IntakeResponse(
        intake_id=uuid.uuid4(),
        status=status,
        documents=documents,
        classification=intake_service.classification_out(classification),
        details=intake_service.details_out(details),
        structured_case=submission.model_dump(mode="json"),
        evidence=submission.evidence,
        combined_text=combined_text[: settings.max_case_text_chars] or None,
        case_id=case_id,
        judgment_id=judgment_id,
        pipeline=pipeline,
        warnings=_dedupe(warnings),
        needs_human_review=needs_review,
        processing_ms=round((time.perf_counter() - started) * 1000, 2),
    )


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    return [item for item in items if not (item in seen or seen.add(item))]
