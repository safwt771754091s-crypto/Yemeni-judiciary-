#!/usr/bin/env python3
"""Seed the vector store with the bundled Yemeni legal corpus.

Usage
-----
    python scripts/seed_corpus.py                        # bundled sample corpus
    python scripts/seed_corpus.py --file my_corpus.json  # custom corpus (same schema)
    python scripts/seed_corpus.py --dry-run              # chunk only, no writes

Corpus JSON schema
------------------
    {"documents": [{"document": {...LegalDocumentCreate...}, "text": "..."}]}

Requires SUPABASE_DB_URL in the environment (or .env) for real persistence;
without it the script runs against the in-memory store and warns loudly.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.chunking import chunk_legal_document
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.schemas.legal import IngestTextRequest, LegalDocumentCreate
from app.services.ingestion import ingestion_service

logger = get_logger("seed_corpus")
DEFAULT_CORPUS = Path(__file__).resolve().parents[1] / "app/data/corpus/yemeni_laws.json"


async def seed(path: Path, dry_run: bool) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    documents = payload.get("documents", [])
    if not documents:
        logger.error("corpus file contains no documents", extra={"path": str(path)})
        return 1

    if not settings.database_enabled:
        logger.warning(
            "SUPABASE_DB_URL is not set - seeding the IN-MEMORY store only. "
            "Set SUPABASE_DB_URL to persist into Supabase/pgvector."
        )

    total_chunks = 0
    for entry in documents:
        request = IngestTextRequest(
            document=LegalDocumentCreate(**entry["document"]),
            text=entry["text"],
        )
        chunks = chunk_legal_document(request.text, max_chars=1800, overlap=200)
        if dry_run:
            logger.info(
                "dry-run",
                extra={
                    "title": request.document.title_ar,
                    "chunks": len(chunks),
                    "articles": len({c.article_number for c in chunks if c.article_number}),
                },
            )
            total_chunks += len(chunks)
            continue

        result = await ingestion_service.ingest_text(request)
        total_chunks += result.chunks_created
        print(
            f"  ✓ {result.title_ar}: {result.chunks_created} مقطع، "
            f"{result.articles_detected} مادة، {result.tokens_total} توكن "
            f"({result.duration_ms:.0f} ms)"
        )

    stats = await ingestion_service.stats()
    print(
        f"\nالمجموع: {total_chunks} مقطع في {len(documents)} قانون. "
        f"إجمالي المواد المخزنة: {stats['total_articles']} "
        f"(نموذج المتجهات: {stats['embedding_model']})"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the Yemeni legal corpus")
    parser.add_argument("--file", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.file.exists():
        print(f"corpus file not found: {args.file}", file=sys.stderr)
        return 2

    configure_logging()
    return asyncio.run(seed(args.file, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
