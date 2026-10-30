"""Health and readiness endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings
from app.db.pool import database
from app.db.vector_store import vector_store
from app.llm.embeddings import embedding_provider
from app.llm.provider import llm_provider

router = APIRouter(tags=["health"])


@router.get("/health", summary="فحص صحة الخدمة")
async def health() -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.app_env,
        "version": "1.0.0",
    }


@router.get("/health/ready", summary="فحص الجاهزية والاعتماديات")
async def readiness() -> dict:
    db_ok = await database.healthy()
    return {
        "status": "ready" if db_ok or not settings.database_enabled else "degraded",
        "database": {
            "configured": settings.database_enabled,
            "connected": db_ok,
            "store": type(vector_store).__name__,
        },
        "embeddings": {"provider": settings.embedding_provider, "model": embedding_provider.model},
        "llm": {"provider": llm_provider.name, "model": llm_provider.model},
        "retrieval": {
            "top_k": settings.retrieval_top_k,
            "vector_weight": settings.vector_weight,
            "keyword_weight": settings.keyword_weight,
        },
        "n8n": {"enabled": settings.n8n_enabled},
    }
