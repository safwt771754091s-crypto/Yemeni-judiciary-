"""Direct retrieval endpoints (debugging / n8n orchestration)."""
from __future__ import annotations

import time

from fastapi import APIRouter, Query

from app.api.deps import RetrievalDep
from app.schemas.common import LegalSourceType
from app.schemas.legal import RetrievalQuery, RetrievalResponse

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


@router.post("/search", response_model=RetrievalResponse, summary="بحث هجين في النصوص القانونية")
async def search(payload: RetrievalQuery, service: RetrievalDep) -> RetrievalResponse:
    started = time.perf_counter()
    results = await service.retrieve(payload)
    return RetrievalResponse(
        query=payload.query,
        results=results,
        count=len(results),
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
    )


@router.get("/search", response_model=RetrievalResponse, summary="بحث سريع (GET)")
async def search_get(
    service: RetrievalDep,
    q: str = Query(..., min_length=2, description="نص الاستعلام"),
    top_k: int = Query(default=8, ge=1, le=50),
    source_type: list[LegalSourceType] | None = Query(default=None),
) -> RetrievalResponse:
    started = time.perf_counter()
    results = await service.retrieve(
        RetrievalQuery(query=q, top_k=top_k, source_types=source_type)
    )
    return RetrievalResponse(
        query=q,
        results=results,
        count=len(results),
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
    )
