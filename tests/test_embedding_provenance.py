"""Embedding provenance: a stale index must be reported, not silently searched.

Switching EMBEDDING_PROVIDER (hash -> openai) changes the vector space. Cosine
similarity across two spaces is meaningless, so the vector half of hybrid
retrieval would quietly return noise. These tests pin the warning that makes
that visible.
"""
from __future__ import annotations

import pytest

from app.core.config import settings
from app.db import vector_store as vector_store_module
from app.llm.embeddings import embedding_provider
from app.schemas.common import LegalSourceType
from app.schemas.legal import IngestTextRequest, LegalDocumentCreate
from app.services.ingestion import ingestion_service
from app.services.retrieval import retrieval_service

DOC = LegalDocumentCreate(
    source_type=LegalSourceType.civil_law,
    title_ar="قانون المعاملات المدنية",
    law_number="14",
    promulgation_year=2002,
)
TEXT = "المادة (1) تسري أحكام هذا القانون على المعاملات المدنية."


@pytest.mark.asyncio
async def test_no_warning_when_index_matches_the_current_model(seeded_store):
    assert await retrieval_service.embedding_mismatch_warning() is None


@pytest.mark.asyncio
async def test_warning_when_the_index_was_built_by_another_model(monkeypatch):
    await ingestion_service.ingest_text(IngestTextRequest(document=DOC, text=TEXT))
    # The corpus is now stamped with the current model; switch to another one.
    monkeypatch.setattr(embedding_provider, "model", "text-embedding-3-large")
    warning = await retrieval_service.embedding_mismatch_warning()
    assert warning is not None
    assert "hash-1536" in warning and "text-embedding-3-large" in warning


@pytest.mark.asyncio
async def test_chunks_record_the_model_that_built_them():
    await ingestion_service.ingest_text(IngestTextRequest(document=DOC, text=TEXT))
    assert await vector_store_module.vector_store.embedding_models() == {embedding_provider.model}


@pytest.mark.asyncio
async def test_introspection_failure_does_not_break_retrieval(monkeypatch):
    """A store that cannot report its models must not fail the pipeline."""

    class _OpaqueStore:
        async def embedding_models(self):
            raise RuntimeError("no access")

    monkeypatch.setattr(vector_store_module, "vector_store", _OpaqueStore())
    assert await retrieval_service.embedding_mismatch_warning() is None
    assert settings.embedding_provider == "hash"


@pytest.mark.asyncio
async def test_pipeline_surfaces_the_mismatch_in_its_warnings(
    seeded_store, sample_case_payload, monkeypatch
):
    """The warning must reach the API response, not just the retrieval layer."""
    from app.schemas.case import CaseSubmissionRequest
    from app.services.pipeline import case_pipeline

    monkeypatch.setattr(embedding_provider, "model", "text-embedding-3-large")
    response = await case_pipeline.submit(CaseSubmissionRequest.model_validate(sample_case_payload))
    assert any("أعد فهرسة" in w for w in response.warnings)
