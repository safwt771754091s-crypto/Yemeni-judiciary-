"""Case submission and judgment endpoints - the core of the platform.

POST /cases/submit          - the full pipeline (claims + defences + evidence -> draft judgment)
GET  /cases                 - list cases
GET  /cases/{id}            - case details with judgments
GET  /cases/{id}/judgments  - judgment history
POST /cases/{id}/evidence   - append evidence to an existing case
POST /cases/{id}/judgments/{jid}/approve - human-in-the-loop approval
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Header, HTTPException, Query, status

from app.api.deps import CaseRepoDep, N8NDep, PipelineDep
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.schemas.case import (
    CaseJudgmentListResponse,
    CaseRecord,
    CaseStatusResponse,
    CaseSubmissionRequest,
    CaseSubmissionResponse,
    EvidenceItem,
)
from app.schemas.judgment import JudgmentDraft, JudgmentResponse, JudgmentStatus

router = APIRouter(prefix="/cases", tags=["cases"])
logger = get_logger(__name__)


@router.post(
    "/submit",
    response_model=CaseSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="تقديم دعوى واستخراج مسودة حكم قضائي",
    description=(
        "يستقبل دعوى المدعي وردود المدعى عليه والأدلة، ثم:\n"
        "1) يخزّن ملف القضية، 2) يسترجع المواد القانونية اليمنية ذات الصلة (RAG)، "
        "3) يقيّم الأدلة وفق قانون الإثبات اليمني، 4) يولّد مسودة حكم مُسنَدة إلى النصوص."
    ),
)
async def submit_case(
    payload: CaseSubmissionRequest,
    pipeline: PipelineDep,
    n8n: N8NDep,
    x_n8n_secret: str | None = Header(default=None, alias="X-N8N-Secret"),
) -> CaseSubmissionResponse:
    if x_n8n_secret is not None and not n8n.verify(x_n8n_secret.encode(), x_n8n_secret):
        raise HTTPException(status_code=401, detail="X-N8N-Secret غير صحيح")

    response = await pipeline.submit(payload)

    if response.judgment_id and response.judgment is not None:
        await n8n.emit_judgment(
            str(response.case.id),
            {
                "grounding_score": response.grounding_score,
                "confidence": None,
                "warnings": response.warnings,
                "citations": response.retrieved_articles,
                "draft": {"verdict": response.judgment.verdict.model_dump(mode="json")},
            },
        )
    return response


@router.get("", summary="قائمة القضايا")
async def list_cases(
    repository: CaseRepoDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    cases = await repository.list_cases(limit=limit, offset=offset)
    return {
        "items": [CaseRecord.model_validate(c).model_dump(mode="json") for c in cases],
        "total": await repository.count_cases(),
        "limit": limit,
        "offset": offset,
    }


@router.get("/{case_id}", response_model=CaseStatusResponse, summary="تفاصيل قضية")
async def get_case(case_id: uuid.UUID, repository: CaseRepoDep) -> CaseStatusResponse:
    case = await repository.get_case(str(case_id))
    if not case:
        raise NotFoundError("القضية غير موجودة")
    judgments = await repository.list_judgments(str(case_id))
    return CaseStatusResponse(
        case_id=case_id,
        status=str(case.get("status")),
        judgments=[_judgment_summary(j) for j in judgments],
    )


def _judgment_summary(record: dict) -> dict:
    """Map a stored judgment row onto the API summary shape.

    Postgres returns numeric columns as Decimal, which would serialize as a
    string; coercing here keeps grounding_score/confidence real JSON numbers.
    """
    return {
        "judgment_id": record["id"],
        "version": record["version"],
        "status": record["status"],
        "grounding_score": _as_float(record.get("grounding_score")),
        "confidence": _as_float(record.get("confidence")),
        "model": record.get("model"),
        "created_at": record.get("created_at"),
    }


def _as_float(value: object) -> float | None:
    return float(value) if value is not None else None


@router.get(
    "/{case_id}/judgments",
    response_model=CaseJudgmentListResponse,
    summary="سجل مسودات الحكم",
)
async def list_judgments(case_id: uuid.UUID, repository: CaseRepoDep) -> CaseJudgmentListResponse:
    case = await repository.get_case(str(case_id))
    if not case:
        raise NotFoundError("القضية غير موجودة")
    judgments = await repository.list_judgments(str(case_id))
    return CaseJudgmentListResponse(
        case_id=case_id,
        items=[_judgment_summary(j) for j in judgments],
    )


@router.get(
    "/{case_id}/judgments/{judgment_id}",
    response_model=JudgmentResponse,
    summary="استرجاع مسودة حكم كاملة",
)
async def get_judgment(
    case_id: uuid.UUID, judgment_id: uuid.UUID, repository: CaseRepoDep
) -> JudgmentResponse:
    record = await repository.get_judgment(str(judgment_id))
    if not record or str(record["case_id"]) != str(case_id):
        raise NotFoundError("مسودة الحكم غير موجودة")
    draft = JudgmentDraft.model_validate(
        {
            "preamble": record.get("preamble") or {},
            "facts": record.get("facts") or {},
            "reasoning": record.get("reasoning") or {},
            "verdict": record.get("verdict") or {},
        }
    )
    return JudgmentResponse(
        judgment_id=judgment_id,
        case_id=case_id,
        version=record["version"],
        status=JudgmentStatus(record["status"]),
        draft=draft,
        markdown_ar=record.get("markdown_ar") or "",
        citations=record.get("citations") or [],
        confidence=float(record.get("confidence") or 0.0),
        grounding_score=float(record.get("grounding_score") or 0.0),
        warnings=record.get("warnings") or [],
        created_at=record.get("created_at"),
    )


@router.post(
    "/{case_id}/evidence",
    status_code=status.HTTP_201_CREATED,
    summary="إضافة أدلة إلى قضية قائمة",
)
async def add_evidence(
    case_id: uuid.UUID, items: list[EvidenceItem], repository: CaseRepoDep
) -> dict:
    case = await repository.get_case(str(case_id))
    if not case:
        raise NotFoundError("القضية غير موجودة")
    if not items:
        raise ValidationError("لم يتم إرسال أي دليل")
    stored = await repository.add_evidence(
        str(case_id), [item.model_dump(mode="json") for item in items]
    )
    return {"case_id": str(case_id), "added": len(stored)}


@router.post(
    "/{case_id}/judgments/{judgment_id}/approve",
    summary="اعتماد مسودة الحكم (مراجعة بشرية)",
)
async def approve_judgment(
    case_id: uuid.UUID,
    judgment_id: uuid.UUID,
    repository: CaseRepoDep,
    decision: str = Query(default="approved", pattern="^(approved|rejected|final)$"),
) -> dict:
    record = await repository.get_judgment(str(judgment_id))
    if not record or str(record["case_id"]) != str(case_id):
        raise NotFoundError("مسودة الحكم غير موجودة")
    await repository.audit(
        "judgment", f"human_{decision}", str(judgment_id), payload={"case_id": str(case_id)}
    )
    return {
        "judgment_id": str(judgment_id),
        "case_id": str(case_id),
        "decision": decision,
        "note": "تم تسجيل قرار المراجعة البشرية في سجل التدقيق.",
    }
