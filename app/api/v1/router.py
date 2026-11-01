"""v1 API router aggregation."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routes import admin, auth, cases, health, ingestion, retrieval

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(ingestion.router)
api_router.include_router(retrieval.router)
api_router.include_router(cases.router)
