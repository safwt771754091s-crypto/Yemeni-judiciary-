"""FastAPI application factory for the Yemeni Legal AI Platform."""
from __future__ import annotations

import json
import mimetypes
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from app.api.pages import canonical_host_redirect, page_gate
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.db.migrations import apply_migrations
from app.db.pool import database
from app.db.user_repository import section_repository
from app.db.vector_store import vector_store
from app.schemas.legal import IngestTextRequest, LegalDocumentCreate
from app.services.auth import DEFAULT_SECTIONS, auth_service
from app.services.ingestion import ingestion_service

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
            applied = await apply_migrations()
            if applied:
                logger.info("schema migrations applied", extra={"count": applied})
        except Exception as exc:
            logger.error("database unavailable at startup", extra={"error": str(exc)})
    await _bootstrap_platform()
    await _seed_sample_corpus()
    yield
    await database.disconnect()
    logger.info("shutdown complete")


async def _bootstrap_platform() -> None:
    """Seed the owner account and the default sections on first run.

    Failures are logged, never fatal: the API must still start (and the test
    suite must still run) when no database is configured.
    """
    try:
        created = await auth_service.ensure_owner()
        if created:
            logger.info("owner account created", extra={"username": settings.owner_username})
        if not await section_repository.list_sections():
            for section in DEFAULT_SECTIONS:
                await section_repository.create(dict(section))
            logger.info("default sections created", extra={"count": len(DEFAULT_SECTIONS)})
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("platform bootstrap skipped", extra={"error": str(exc)})


async def _seed_sample_corpus() -> None:
    """Load the bundled legal excerpt into the empty in-memory store.

    Only runs when no Postgres DSN is configured and the store has no articles.
    The full corpus belongs in the database (``scripts/seed_corpus.py``); this is
    a bounded fallback so a serverless deployment without a database still
    answers legal search instead of returning nothing. Failures are logged,
    never fatal.
    """
    if settings.database_enabled or not settings.seed_sample_corpus:
        return
    try:
        if (await ingestion_service.stats())["total_articles"]:
            return
        sample = Path(__file__).parent / "data" / "samples" / "yemeni_laws_excerpt.json"
        if not sample.is_file():
            return
        payload = json.loads(sample.read_text(encoding="utf-8"))
        for entry in payload.get("documents", []):
            await ingestion_service.ingest_text(
                IngestTextRequest(
                    document=LegalDocumentCreate(**entry["document"]),
                    text=entry["text"],
                )
            )
        stats = await ingestion_service.stats()
        logger.info(
            "sample corpus loaded into the in-memory store",
            extra={"articles": stats["total_articles"], "documents": stats["total_documents"]},
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("sample corpus seeding skipped", extra={"error": str(exc)})


#: Media types that never change once shipped, so the browser and the CDN may
#: hold them for a year without ever asking again.
_IMMUTABLE_PREFIXES = ("image/", "font/", "audio/", "video/")
_APK_TYPE = "application/vnd.android.package-archive"

#: Scripts and stylesheets keep their filename across deploys, so the browser
#: must revalidate them. ``s-maxage`` still lets the CDN hold them for a year,
#: so the revalidation is answered at the edge instead of by the serverless
#: function; ``stale-while-revalidate`` serves the cached copy while it refreshes.
_EDGE_CACHED = "public, max-age=0, s-maxage=31536000, stale-while-revalidate=86400"
_MUST_REVALIDATE = "public, max-age=0, must-revalidate"


def _static_cache_control(media_type: str | None) -> str:
    """The Cache-Control to attach to a static response of this media type."""
    kind = (media_type or "").split(";", 1)[0].strip().lower()
    if kind.startswith(_IMMUTABLE_PREFIXES) or kind == _APK_TYPE:
        return "public, max-age=31536000, immutable"
    if kind == "text/html":
        return _MUST_REVALIDATE
    return _EDGE_CACHED


class _CacheAwareStaticFiles(StaticFiles):
    """Static files served with cache headers, so a reload is not a cold start.

    Starlette's default ``max-age=0, must-revalidate`` marks every script,
    stylesheet and icon as stale, so each page load re-fetches each asset from
    the serverless function. On a cold start that is seconds per asset. This
    subclass gives images and fonts an immutable year and lets the CDN hold
    scripts and stylesheets, while the browser still revalidates them.
    """

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers.setdefault(
                "Cache-Control", _static_cache_control(response.headers.get("content-type"))
            )
        return response


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

    # The offline statute index (legal-index.json) is a few megabytes; gzip
    # brings it down to a few hundred KB on the wire. Compression is skipped
    # for already-compressed types and for responses under 1 KB.
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.middleware("http")
    async def add_timing_header(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - started) * 1000:.2f}"
        return response

    # Per-page access control for the static role portals: a gated page is
    # checked before the static mount can serve it, so a direct URL cannot
    # bypass the role's permissions.
    app.middleware("http")(page_gate)

    # A duplicate deployment (same code, no database) must not serve a second
    # copy of the app. Registered last so it runs first and the redirect happens
    # before any page or asset work.
    app.middleware("http")(canonical_host_redirect)

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)

    static_dir = Path(__file__).parent / "static"
    if static_dir.is_dir():
        # Android package files must be served as application/vnd.android.package-archive
        # or some mobile browsers refuse to install them. Python's mimetypes does
        # not know the extension by default.
        mimetypes.add_type("application/vnd.android.package-archive", ".apk")

        # Serve the offline statute index pre-compressed. Compressing the ~3.8 MB
        # JSON on every request costs ~0.5 s of CPU (Starlette's gzip runs at
        # level 9 on the event loop), which serialises concurrent requests. The
        # build step writes the .gz next to the .json, so this just hands the
        # bytes over. Registered before the /ui mount so it wins the match.
        index_json = static_dir / "legal-index.json"
        index_gz = static_dir / "legal-index.json.gz"

        @app.get("/ui/legal-index.json", include_in_schema=False)
        async def statute_index(request: Request) -> Response:
            accepts_gzip = "gzip" in request.headers.get("accept-encoding", "")
            if accepts_gzip and index_gz.is_file():
                return FileResponse(
                    str(index_gz),
                    media_type="application/json",
                    headers={
                        "Content-Encoding": "gzip",
                        "Vary": "Accept-Encoding",
                        "Cache-Control": "public, max-age=86400",
                    },
                )
            return FileResponse(
                str(index_json),
                media_type="application/json",
                headers={"Cache-Control": "public, max-age=86400", "Vary": "Accept-Encoding"},
            )

        app.mount("/ui", _CacheAwareStaticFiles(directory=str(static_dir), html=True), name="ui")

        # The PWA manifest and service worker are served from the site root so
        # the worker can claim the whole origin (scope "/"), not only /ui/.
        # A worker at /ui/sw.js would otherwise be limited to the /ui/ subtree.
        @app.get("/manifest.json", include_in_schema=False)
        async def manifest() -> FileResponse:
            return FileResponse(
                str(static_dir / "manifest.json"),
                media_type="application/manifest+json",
                headers={"Cache-Control": "public, max-age=86400"},
            )

        @app.get("/sw.js", include_in_schema=False)
        async def service_worker() -> FileResponse:
            return FileResponse(
                str(static_dir / "sw.js"),
                media_type="application/javascript",
                headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
            )

    @app.get("/", tags=["root"], summary="واجهة المنصة")
    async def root():
        # The public landing page is the front door; the judgment generator
        # stays available at /ui/index.html for existing links.
        for name in ("landing.html", "index.html"):
            page = static_dir / name
            if page.is_file():
                return FileResponse(
                    str(page),
                    media_type="text/html",
                    headers={"Cache-Control": "public, max-age=0, must-revalidate"},
                )
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
