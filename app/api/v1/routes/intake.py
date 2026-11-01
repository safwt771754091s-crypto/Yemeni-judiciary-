"""Case intake endpoints: upload the papers, the platform does the rest.

POST /cases/intake   - multipart upload of case files (+ optional text). Extracts
                       text (with Arabic OCR for scans), classifies the case,
                       structures its components, and runs the full RAG pipeline
                       to a draft judgment.
POST /cases/classify - classify a case from text alone (no files, no judgment).
POST /cases/extract  - structure a case from text alone, without submitting it.
GET  /cases/lexicon  - the case-type catalogue and the codes each one searches.
"""
from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile, status

from app.api.deps import CurrentUserDep, N8NDep
from app.core.config import settings
from app.core.errors import ValidationError
from app.core.legal_lexicon import CASE_TYPES, case_type_label
from app.core.logging import get_logger
from app.schemas.intake import (
    CaseClassificationOut,
    CaseClassifyRequest,
    CaseExtractRequest,
    CaseExtractResponse,
    IntakeResponse,
)
from app.services.document_extraction import SUPPORTED_SUFFIXES, ocr_status
from app.services.intake import (
    describe_routing,
    intake_service,
    run_intake_pipeline,
)

router = APIRouter(prefix="/cases", tags=["intake"])
logger = get_logger(__name__)


@router.post(
    "/intake",
    response_model=IntakeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="استلام قضية من ملفات مرفوعة وتفصيلها وتصنيفها وإصدار مسودة الحكم",
    description=(
        "ارفع أوراق القضية (PDF أو صورة ممسوحة أو Word أو نص) وسيقوم النظام تلقائياً بـ:\n"
        "1) استخراج النص (مع التعرّف الضوئي OCR للنصوص العربية الممسوحة)،\n"
        "2) تفصيل الدعوى (الأطراف، المحكمة، رقم القضية، الطلبات، الدفوع، الوقائع)،\n"
        "3) تصنيف الدعوى (مدني/تجاري/جنائي/أسري/عمالي/إداري/عقاري/تنفيذ) وتحديد القوانين الواجبة التطبيق،\n"
        "4) إجراء الاسترجاع الدلالي (RAG) على النصوص القانونية اليمنية،\n"
        "5) تقييم الأدلة وفق قانون الإثبات، وتوليد مسودة حكم مُسنَدة إلى نصوص المواد.\n\n"
        "يمكن تجاوز أي حقل مستخرج بإرساله ضمن النموذج (form fields)."
    ),
)
async def submit_intake(
    user: CurrentUserDep,
    n8n: N8NDep,
    files: Annotated[list[UploadFile] | None, File(description="أوراق القضية")] = None,
    text: Annotated[str | None, Form(description="نص القضية (اختياري، يُضاف للملفات)")] = None,
    declared_case_type: Annotated[
        str | None, Form(description="النوع المصرَّح به؛ يُعالج كقرينة لا كحكم")
    ] = None,
    case_number: Annotated[str | None, Form()] = None,
    court_name: Annotated[str | None, Form()] = None,
    circuit: Annotated[str | None, Form()] = None,
    judge_name: Annotated[str | None, Form()] = None,
    subject_matter: Annotated[str | None, Form()] = None,
    claim_value: Annotated[float | None, Form(ge=0)] = None,
    currency: Annotated[str | None, Form()] = None,
    facts_summary: Annotated[str | None, Form()] = None,
    plaintiff_name: Annotated[str | None, Form()] = None,
    defendant_name: Annotated[str | None, Form()] = None,
    generate_judgment: Annotated[bool, Form()] = True,
    use_llm_classification: Annotated[bool, Form()] = False,
    max_retrieved_articles: Annotated[int, Form(ge=1, le=40)] = 12,
    include_full_text: Annotated[bool, Form()] = False,
    x_n8n_secret: Annotated[str | None, Header(alias="X-N8N-Secret")] = None,
) -> IntakeResponse:
    if not n8n.verify_token(x_n8n_secret):
        raise HTTPException(status_code=401, detail="X-N8N-Secret غير صحيح")

    import time

    started = time.perf_counter()
    warnings: list[str] = []

    # 1. read the uploaded papers -----------------------------------------
    payloads: list[tuple[str, bytes, str | None]] = []
    total_bytes = 0
    limit = settings.max_upload_mb * 1024 * 1024
    for upload in files or []:
        if not upload.filename:
            continue
        data = await upload.read()
        total_bytes += len(data)
        if len(data) > limit:
            warnings.append(
                f"«{upload.filename}»: تجاوز حجم الملف الحد الأقصى ({settings.max_upload_mb} م.ب) وتم تجاهله."
            )
            continue
        payloads.append((upload.filename, data, upload.content_type))
    if total_bytes > limit * settings.max_upload_files:
        raise ValidationError(
            f"إجمالي حجم الملفات تجاوز الحد المسموح ({settings.max_upload_mb * settings.max_upload_files} م.ب)"
        )
    if not payloads and not (text and text.strip()):
        raise ValidationError(
            "لم يتم إرسال أي ملف أو نص. ارفع أوراق القضية أو أرسل حقل text."
        )

    documents, doc_warnings = intake_service.read_documents(payloads)
    warnings.extend(doc_warnings)

    # 2. combine + structure ----------------------------------------------
    # The combined text is built before the per-document full text is dropped:
    # the response keeps only a preview by default to stay small, but the
    # pipeline needs the whole document.
    combined = intake_service.combine_text(documents, text)
    if not include_full_text:
        for document in documents:
            document.text = None
    if not combined.strip():
        raise ValidationError(
            "تعذّر استخراج أي نص من الملفات المرفوعة. إن كانت ممسوحة ضوئياً فتأكد من "
            "تفعيل التعرّف الضوئي (OCR)، أو أرسل نص القضية في حقل text."
        )
    if len(combined) > settings.max_case_text_chars:
        combined = combined[: settings.max_case_text_chars]
        warnings.append("تم اقتطاع النص لطوله الشديد؛ راجع الحقول المستخرجة بعناية.")

    details = intake_service.extractor.extract(combined)

    # 3. classify ----------------------------------------------------------
    classification = intake_service.classify(
        combined, declared_case_type=declared_case_type, details=details
    )
    if use_llm_classification:
        classification = await _refine_classification(classification, combined, warnings)

    # 4. assemble + run the pipeline ---------------------------------------
    evidence = intake_service.build_evidence(documents)
    submission = intake_service.build_submission(
        details=details,
        classification=classification,
        evidence=evidence,
        declared_case_type=declared_case_type,
        case_number=case_number,
        court_name=court_name,
        circuit=circuit,
        judge_name=judge_name,
        subject_matter=subject_matter,
        claim_value=claim_value,
        currency=currency,
        facts_summary=facts_summary,
        plaintiff_name=plaintiff_name,
        defendant_name=defendant_name,
        generate_judgment=generate_judgment,
        max_retrieved_articles=max_retrieved_articles,
    )

    response = await run_intake_pipeline(
        submission=submission,
        documents=documents,
        classification=classification,
        details=details,
        warnings=warnings,
        combined_text=combined,
        started=started,
    )

    if response.judgment_id:
        await n8n.emit_judgment(
            str(response.case_id),
            {
                "source": "intake",
                "classification": classification.case_type,
                "grounding_score": response.pipeline.grounding_score
                if response.pipeline
                else None,
                "warnings": response.warnings,
            },
        )
    logger.info(
        "case intake completed",
        extra={
            "user": user.get("username"),
            "documents": len(documents),
            "case_type": classification.case_type,
            "status": response.status,
            "duration_ms": response.processing_ms,
        },
    )
    return response


@router.post(
    "/classify",
    response_model=CaseClassificationOut,
    summary="تصنيف دعوى من نصها دون تقديمها",
    description="يحدد نوع الدعوى (مدني/تجاري/جنائي/أسري/عمالي/إداري/عقاري/تنفيذ) والقوانين الواجبة التطبيق.",
)
async def classify_case(
    payload: CaseClassifyRequest, _user: CurrentUserDep
) -> CaseClassificationOut:
    classification = intake_service.classify(
        payload.text,
        declared_case_type=payload.declared_case_type,
        details=None,
    )
    if payload.subject_matter:
        # Re-run with the subject as an extra hint when one was supplied.
        from app.services.case_details import ExtractedDetails

        hint_details = ExtractedDetails(subject_matter=payload.subject_matter)
        classification = intake_service.classify(
            payload.text,
            declared_case_type=payload.declared_case_type,
            details=hint_details,
        )
    if payload.use_llm:
        warnings: list[str] = []
        classification = await _refine_classification(classification, payload.text, warnings)
        classification.notes.extend(warnings)
    return intake_service.classification_out(classification)


@router.post(
    "/extract",
    response_model=CaseExtractResponse,
    summary="تفصيل دعوى من نصها (الأطراف والطلبات والدفوع والوقائع)",
    description="يستخرج مكونات القضية ويصنّفها دون تقديمها أو إصدار مسودة حكم.",
)
async def extract_case(
    payload: CaseExtractRequest, _user: CurrentUserDep
) -> CaseExtractResponse:
    details = intake_service.extractor.extract(payload.text)
    classification = intake_service.classify(
        payload.text, declared_case_type=payload.declared_case_type, details=details
    )
    warnings = list(details.notes)
    warnings.extend(intake_service.assess_gaps(details, classification))
    return CaseExtractResponse(
        details=intake_service.details_out(details),
        classification=intake_service.classification_out(classification),
        warnings=warnings,
    )


@router.get(
    "/lexicon",
    summary="دليل أنواع الدعاوى والقوانين الواجبة التطبيق",
    description="يعرض أنواع الدعاوى المدعومة والمؤشرات اللفظية والقوانين التي يُبحث فيها لكل نوع.",
)
async def get_lexicon(_user: CurrentUserDep) -> dict[str, Any]:
    return {
        "case_types": [
            {
                "key": profile.key,
                "label_ar": profile.label_ar,
                "description": profile.description,
                "criminal": profile.criminal,
                "default_court": profile.default_court,
                "source_types": list(profile.source_types),
                "indicators": {
                    "strong": list(profile.strong)[:15],
                    "weak": list(profile.weak)[:15],
                },
            }
            for profile in CASE_TYPES
        ],
        "ocr": ocr_status(),
        "supported_formats": sorted(SUPPORTED_SUFFIXES),
        "classification_min_confidence": settings.classification_min_confidence,
    }


async def _refine_classification(classification, text: str, warnings: list[str]):
    """Ask the LLM to refine a classification, within the lexicon's candidates."""
    from app.llm.chains import build_classification_chain

    try:
        chain = build_classification_chain()
        candidates = ", ".join(
            f"{s.case_type} (درجة {s.score:.1f})" for s in classification.scores[:4]
        )
        result = await chain.run(
            [
                f"«نص القضية»:\n{text[:4000]}",
                f"«المرشحون»: {candidates}",
                f"«التصنيف المبدئي»: {classification.case_type}",
                "«المطلوب»: أخرج JSON بالمفتاحين case_type و reason.",
            ]
        )
        data = result.data or {}
        classification = intake_service.classifier.apply_llm_refinement(
            classification,
            suggested=data.get("case_type"),
            reason=data.get("reason"),
        )
    except Exception as exc:
        warnings.append(f"تعذّر تحسين التصنيف بالنموذج اللغوي: {exc}")
    return classification


# Re-exported for the lexicon endpoint documentation.
__all__ = ["case_type_label", "describe_routing", "router", "uuid"]
