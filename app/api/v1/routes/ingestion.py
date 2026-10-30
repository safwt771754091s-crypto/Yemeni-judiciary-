"""Legal corpus ingestion & vectorisation endpoints.

POST /ingestion/text        - ingest one statute (chunk + embed + store)
POST /ingestion/batch       - ingest several statutes in one call
POST /ingestion/upload      - upload a .txt/.md/.json statute file
GET  /ingestion/stats       - corpus statistics
"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, UploadFile, status

from app.api.deps import IngestionDep
from app.core.errors import IngestionError
from app.core.logging import get_logger
from app.schemas.common import SOURCE_LABELS_AR, LegalSourceType
from app.schemas.legal import (
    CorpusStatsItem,
    CorpusStatsResponse,
    IngestResponse,
    IngestTextRequest,
    LegalDocumentCreate,
)

router = APIRouter(prefix="/ingestion", tags=["ingestion"])
logger = get_logger(__name__)


@router.post(
    "/text",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="إدخال قانون/تشريع يمني (تقطيع + توليد متجهات + تخزين)",
)
async def ingest_text(payload: IngestTextRequest, service: IngestionDep) -> IngestResponse:
    return await service.ingest_text(payload)


@router.post(
    "/batch",
    response_model=list[IngestResponse],
    status_code=status.HTTP_201_CREATED,
    summary="إدخال مجموعة قوانين دفعة واحدة",
)
async def ingest_batch(
    payload: list[IngestTextRequest], service: IngestionDep
) -> list[IngestResponse]:
    if not payload:
        raise IngestionError("القائمة المُرسلة فارغة")
    return await service.ingest_many(payload)


@router.post(
    "/upload",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="رفع ملف نصي لقانون يمني وإدخاله",
)
async def ingest_upload(
    service: IngestionDep,
    file: UploadFile = File(..., description="ملف نصي (.txt / .md / .json)"),
    source_type: LegalSourceType = Form(...),
    title_ar: str = Form(...),
    law_number: str | None = Form(default=None),
    promulgation_year: int | None = Form(default=None),
    issuing_authority: str = Form(default="مجلس النواب"),
) -> IngestResponse:
    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp1256")  # common Arabic Windows encoding
        except UnicodeDecodeError as exc:
            raise IngestionError(
                "تعذّر قراءة الملف؛ يجب أن يكون بترميز UTF-8 أو CP1256", details=str(exc)
            ) from exc
    request = IngestTextRequest(
        document=LegalDocumentCreate(
            source_type=source_type,
            title_ar=title_ar,
            law_number=law_number,
            promulgation_year=promulgation_year,
            issuing_authority=issuing_authority,
        ),
        text=text,
    )
    return await service.ingest_text(request)


@router.get("/stats", response_model=CorpusStatsResponse, summary="إحصاءات المدونة القانونية")
async def corpus_stats(service: IngestionDep) -> CorpusStatsResponse:
    data = await service.stats()
    items = [
        CorpusStatsItem(
            source_type=str(row.get("source_type")),
            source_label_ar=SOURCE_LABELS_AR.get(str(row.get("source_type")), str(row.get("source_type"))),
            documents=int(row.get("documents") or 0),
            articles=int(row.get("articles") or 0),
            embedded=int(row.get("embedded") or 0),
            total_tokens=int(row.get("total_tokens") or 0),
        )
        for row in data["items"]
    ]
    return CorpusStatsResponse(
        items=items,
        total_articles=data["total_articles"],
        total_documents=data["total_documents"],
        embedding_model=data["embedding_model"],
        database_connected=await _db_connected(),
    )


async def _db_connected() -> bool:
    from app.db.pool import database

    return await database.healthy()
