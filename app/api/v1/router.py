"""v1 API router aggregation."""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routes import (
    admin,
    assistant,
    auth,
    cases,
    health,
    ingestion,
    intake,
    portals,
    retrieval,
    workspace,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(portals.router)
api_router.include_router(assistant.router)
api_router.include_router(workspace.router)
api_router.include_router(ingestion.router)
api_router.include_router(retrieval.router)
api_router.include_router(intake.router)
api_router.include_router(cases.router)
