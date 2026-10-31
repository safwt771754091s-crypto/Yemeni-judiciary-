"""FastAPI application factory for the Yemeni Legal AI Platform."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.db.pool import database
from app.db.vector_store import vector_store

logger = get_logger(__name__)

DESCRIPTION = """
منصة الذكاء الاصطناعي القانوني للجمهورية اليمنية.

محرك القرار والاستدلال القانوني:
- إدخال وتقطيع وتوليد متجهات للقوانين اليمنية (المدني، التجاري، الإثبات، المرافعات).
- تحليل دعوى المدعي وردود المدعى عليه والأدلة.
- استرجاع المواد القانونية ذات الصلة (RAG) من قاعدة متجهات Supabase/pgvector.
- تقييم الأدلة وفق قانون الإثبات اليمني.
- توليد مسودة حكم قضائي مُهيكلة (الديباجة، الوقائع، الأسباب والحيثيات، المنطوق)
  بصيغة JSON و Markdown، مع منع الاستشهاد بنصوص غير موجودة في المدونة القانونية.

⚠️ المخرجات مسودات آلية لا تُعدّ أحكاماً قضائية إلا بعد اعتماد القاضي المختص.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info(
        "starting legal AI platform",
        extra={
            "env": settings.app_env,
            "database": settings.database_enabled,
            "embedding_provider": settings.embedding_provider,
            "llm_provider": settings.llm_provider,
            "vector_store": type(vector_store).__name__,
        },
    )
    if settings.database_enabled:
        try:
            await database.connect()
        except Exception as exc:
            logger.error("database unavailable at startup", extra={"error": str(exc)})
    yield
    await database.disconnect()
    logger.info("shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        description=DESCRIPTION,
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if not settings.is_production else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def add_timing_header(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - started) * 1000:.2f}"
        return response

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)

    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        app.mount("/ui", StaticFiles(directory=str(static_dir), html=True), name="ui")

    @app.get("/", tags=["root"], summary="واجهة المنصة")
    async def root():
        index = static_dir / "index.html"
        if index.is_file():
            return FileResponse(str(index))
        return {
            "name": settings.app_name,
            "version": "1.0.0",
            "docs": "/docs",
            "api_prefix": settings.api_prefix,
            "jurisdiction": settings.jurisdiction,
            "endpoints": {
                "submit_case": f"{settings.api_prefix}/cases/submit",
                "ingest_law": f"{settings.api_prefix}/ingestion/text",
                "search_laws": f"{settings.api_prefix}/retrieval/search",
                "health": f"{settings.api_prefix}/health",
            },
        }

    return app


app = create_app()
