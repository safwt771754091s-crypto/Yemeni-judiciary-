"""Document ingestion & vectorisation.

Pipeline: raw statute text -> article-aware chunks -> embeddings -> pgvector
(``legal_documents`` + ``legal_articles``). Re-ingesting the same law is
idempotent: the document is upserted on (source_type, law_number, year) and each
chunk on (document_id, article_number, chunk_index).
"""
from __future__ import annotations

import time
from typing import Any

from app.core.chunking import chunk_legal_document
from app.core.config import settings
from app.core.errors import IngestionError
from app.core.logging import get_logger
from app.db import vector_store as vector_store_module
from app.db.vector_store import StoredChunk
from app.llm.embeddings import embedding_provider
from app.schemas.legal import IngestResponse, IngestTextRequest

logger = get_logger(__name__)


class IngestionService:
    def __init__(self) -> None:
        self.embedder = embedding_provider

    @property
    def store(self):
        """Resolve the active store lazily so config changes/tests take effect."""
        return vector_store_module.vector_store

    async def ingest_text(self, request: IngestTextRequest) -> IngestResponse:
        started = time.perf_counter()
        document = request.document

        chunks = chunk_legal_document(
            request.text,
            max_chars=request.max_chars_per_chunk,
            overlap=request.chunk_overlap,
            default_article=request.default_article_number,
        )
        if not chunks:
            raise IngestionError("لم يتم استخراج أي مواد من النص المُرسل")

        document_id = await self.store.upsert_document(
            {
                "source_type": document.source_type.value,
                "title_ar": document.title_ar,
                "title_en": document.title_en,
                "law_number": document.law_number,
                "promulgation_year": document.promulgation_year,
                "issuing_authority": document.issuing_authority,
                "jurisdiction": document.jurisdiction,
                "source_url": document.source_url,
                "metadata": document.metadata,
            }
        )

        embeddings = await self._embed([c.content for c in chunks])
        stored = [
            StoredChunk(
                document_id=document_id,
                source_type=document.source_type.value,
                article_number=chunk.article_number,
                article_label=chunk.article_label,
                chapter=chunk.chapter,
                section=chunk.section,
                heading=chunk.heading,
                content=chunk.content,
                chunk_index=chunk.chunk_index,
                token_count=chunk.token_count,
                embedding=embedding,
                metadata={
                    **chunk.metadata,
                    "law_title": document.title_ar,
                    # Provenance for the vector: lets retrieval detect an index
                    # built by a different embedding model (a different space).
                    "embedding_model": self.embedder.model,
                },
            )
            for chunk, embedding in zip(chunks, embeddings, strict=True)
        ]
        written = await self.store.upsert_chunks(stored)
        duration_ms = (time.perf_counter() - started) * 1000

        logger.info(
            "ingested legal document",
            extra={
                "document_id": document_id,
                "chunks": written,
                "tokens": sum(c.token_count for c in chunks),
                "duration_ms": round(duration_ms, 1),
            },
        )
        return IngestResponse(
            document_id=document_id,
            title_ar=document.title_ar,
            chunks_created=written,
            articles_detected=len({c.article_number for c in chunks if c.article_number}),
            tokens_total=sum(c.token_count for c in chunks),
            embedding_model=self.embedder.model,
            duration_ms=round(duration_ms, 2),
        )

    async def ingest_many(self, requests: list[IngestTextRequest]) -> list[IngestResponse]:
        results: list[IngestResponse] = []
        for request in requests:
            results.append(await self.ingest_text(request))
        return results

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        """Embed in batches to respect provider limits."""
        batch_size = max(1, settings.embedding_batch_size)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            vectors.extend(await self.embedder.embed_documents(batch))
        if len(vectors) != len(texts):
            raise IngestionError("عدد المتجهات لا يطابق عدد النصوص")
        return vectors

    async def stats(self) -> dict[str, Any]:
        rows = await self.store.corpus_stats()
        return {
            "items": rows,
            "total_articles": sum(int(r.get("articles") or 0) for r in rows),
            "total_documents": sum(int(r.get("documents") or 0) for r in rows),
            "embedding_model": self.embedder.model,
        }


ingestion_service = IngestionService()
