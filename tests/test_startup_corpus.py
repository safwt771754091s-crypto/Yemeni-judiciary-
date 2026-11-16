"""The in-memory fallback must not serve an empty statute book.

Without a Postgres DSN the app uses the in-process vector store, which starts
empty. A serverless deployment that never sets the DSN then answered every legal
search with zero results. Startup now loads the bundled excerpt so the platform
has real content before the durable database is linked, and skips that work as
soon as a database is configured or the corpus is already present.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import create_app
from app.schemas.legal import IngestTextRequest, LegalDocumentCreate
from app.services.ingestion import ingestion_service
from tests.conftest import SAMPLE


def _stats(client: TestClient) -> dict:
    return client.get("/api/v1/ingestion/stats").json()


def test_startup_seeds_the_sample_corpus_without_a_database():
    with TestClient(create_app()) as client:
        stats = _stats(client)
        assert stats["total_articles"] > 0
        assert stats["total_documents"] > 0
        # The corpus is searchable, not merely counted.
        found = client.get("/api/v1/retrieval/search", params={"q": "البيع", "top_k": 3}).json()
        assert found["count"] > 0


def test_startup_skips_seeding_when_a_database_is_configured(monkeypatch):
    monkeypatch.setattr(
        type(settings), "database_enabled", property(lambda self: True)
    )
    with TestClient(create_app()) as client:
        assert _stats(client)["total_articles"] == 0


def test_startup_skips_seeding_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "seed_sample_corpus", False)
    with TestClient(create_app()) as client:
        assert _stats(client)["total_articles"] == 0


def test_startup_does_not_reseed_a_populated_store():
    """An operator who already loaded the full corpus must not be overwritten."""
    import asyncio

    payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
    entry = payload["documents"][0]
    asyncio.run(
        ingestion_service.ingest_text(
            IngestTextRequest(
                document=LegalDocumentCreate(**entry["document"]),
                text=entry["text"],
            )
        )
    )
    before = asyncio.run(ingestion_service.stats())["total_articles"]
    with TestClient(create_app()) as client:
        assert _stats(client)["total_articles"] == before
