"""Direct retrieval endpoints (debugging / n8n orchestration)."""
from __future__ import annotations

import time

from fastapi import APIRouter, Query

from app.api.deps import RetrievalDep
from app.schemas.common import LegalSourceType
from app.schemas.legal import (
    ArticleSearchQuery,
    ArticleSearchResponse,
    RetrievalQuery,
    RetrievalResponse,
)

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


@router.get(
    "/articles",
    response_model=ArticleSearchResponse,
    summary="بحث في المواد القانونية برقم المادة أو جزء من نصها",
)
async def search_articles(
    service: RetrievalDep,
    number: str | None = Query(
        default=None, max_length=20, description="رقم المادة (يقبل الأرقام العربية)"
    ),
    q: str | None = Query(default=None, max_length=400, description="جزء من نص المادة"),
    limit: int = Query(default=20, ge=1, le=100),
    source_type: list[LegalSourceType] | None = Query(default=None),
) -> ArticleSearchResponse:
    started = time.perf_counter()
    results = await service.search_articles(
        query=q,
        article_number=number,
        source_types=[s.value for s in source_type] if source_type else None,
        limit=limit,
    )
    return ArticleSearchResponse(
        query=q,
        article_number=number,
        results=results,
        count=len(results),
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
    )


@router.post(
    "/articles",
    response_model=ArticleSearchResponse,
    summary="بحث في المواد القانونية (POST)",
)
async def search_articles_post(
    payload: ArticleSearchQuery, service: RetrievalDep
) -> ArticleSearchResponse:
    started = time.perf_counter()
    results = await service.search_articles(
        query=payload.query,
        article_number=payload.article_number,
        source_types=[s.value for s in payload.source_types] if payload.source_types else None,
        limit=payload.limit,
    )
    return ArticleSearchResponse(
        query=payload.query,
        article_number=payload.article_number,
        results=results,
        count=len(results),
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
    )
