"""Vector store abstraction.

Two implementations share one interface:

* ``PgVectorStore``  - Supabase/PostgreSQL + pgvector, hybrid (vector + Arabic
  FTS + trigram) retrieval fused with Reciprocal Rank Fusion, executed entirely
  inside Postgres via ``match_legal_articles``.
* ``InMemoryVectorStore`` - deterministic fallback used for local development,
  tests and CI when no database is configured. It reproduces the same fusion
  maths in Python so behaviour is comparable.

Both return ``RetrievedArticle`` objects.
"""
from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.config import settings
from app.core.logging import get_logger
from app.core.text_utils import normalize_arabic
from app.db.pool import database
from app.schemas.common import LegalSourceType
from app.schemas.legal import RetrievedArticle

logger = get_logger(__name__)


@dataclass(slots=True)
class StoredChunk:
    """Chunk payload handed to the store for persistence."""

    document_id: str
    source_type: str
    article_number: str | None
    article_label: str
    chapter: str | None
    section: str | None
    heading: str | None
    content: str
    chunk_index: int
    token_count: int
    embedding: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)


class VectorStore(Protocol):
    async def upsert_document(self, doc: dict[str, Any]) -> str: ...

    async def upsert_chunks(self, chunks: Sequence[StoredChunk]) -> int: ...

    async def search(
        self,
        *,
        query: str,
        query_embedding: list[float],
        top_k: int = 8,
        source_types: list[str] | None = None,
        law_title_contains: str | None = None,
        document_id: str | None = None,
        min_similarity: float = 0.0,
    ) -> list[RetrievedArticle]: ...

    async def find_by_article_number(
        self,
        article_number: str,
        source_types: list[str] | None = None,
        limit: int = 3,
    ) -> list[RetrievedArticle]:
        """Exact article lookup, independent of embedding similarity.

        A party who cites "المادة 146" must get *that* article, which a semantic
        search cannot guarantee.
        """
        ...

    async def corpus_stats(self) -> list[dict[str, Any]]: ...

    async def document_exists(self, source_type: str, law_number: str | None, year: int | None) -> str | None: ...


# ---------------------------------------------------------------------------
# Postgres / pgvector implementation
# ---------------------------------------------------------------------------
class PgVectorStore:
    async def upsert_document(self, doc: dict[str, Any]) -> str:
        row = await database.fetchrow(
            "select upsert_legal_document($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb) as id",
            doc["source_type"],
            doc["title_ar"],
            doc.get("title_en"),
            doc.get("law_number"),
            doc.get("promulgation_year"),
            doc.get("issuing_authority", "مجلس النواب"),
            doc.get("jurisdiction", settings.jurisdiction),
            doc.get("source_url"),
            doc.get("metadata") or {},
        )
        return str(row["id"])

    async def upsert_chunks(self, chunks: Sequence[StoredChunk]) -> int:
        written = 0
        async with database.transaction() as conn:
            for chunk in chunks:
                await conn.execute(
                    "select upsert_legal_article($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::vector,$12::jsonb)",
                    uuid.UUID(chunk.document_id),
                    chunk.source_type,
                    chunk.article_number,
                    chunk.article_label,
                    chunk.chapter,
                    chunk.section,
                    chunk.heading,
                    chunk.content,
                    chunk.chunk_index,
                    chunk.token_count,
                    _vector_literal(chunk.embedding),
                    chunk.metadata or {},
                )
                written += 1
        return written

    async def search(
        self,
        *,
        query: str,
        query_embedding: list[float],
        top_k: int = 8,
        source_types: list[str] | None = None,
        law_title_contains: str | None = None,
        document_id: str | None = None,
        min_similarity: float = 0.0,
    ) -> list[RetrievedArticle]:
        rows = await database.fetch(
            "select * from match_legal_articles($1::vector,$2,$3,$4,$5::text[],$6,$7,$8,$9,$10)",
            _vector_literal(query_embedding),
            query,
            top_k,
            min_similarity,
            source_types,
            law_title_contains,
            uuid.UUID(document_id) if document_id else None,
            settings.rrf_k,
            settings.vector_weight,
            settings.keyword_weight,
        )
        return [_row_to_article(r) for r in rows]

    async def find_by_article_number(
        self,
        article_number: str,
        source_types: list[str] | None = None,
        limit: int = 3,
    ) -> list[RetrievedArticle]:
        if not article_number:
            return []
        rows = await database.fetch(
            "select a.id as article_id, a.document_id, a.source_type, d.title_ar, d.law_number, "
            "       a.article_number, a.article_label, a.chapter, a.heading_ar, a.content_ar, "
            "       d.source_url, d.metadata -> 'provenance' ->> 'quality' as text_quality, "
            "       (d.metadata -> 'provenance' ->> 'verbatim')::boolean as verbatim "
            "from legal_articles a "
            "join legal_documents d on d.id = a.document_id "
            "where regexp_replace(normalize_digits(a.article_number), '\\D', '', 'g') = "
            "      regexp_replace(normalize_digits($1), '\\D', '', 'g') "
            "  and ($2::text[] is null or a.source_type::text = any($2::text[])) "
            "order by a.chunk_index "
            "limit $3",
            article_number,
            source_types,
            limit,
        )
        articles: list[RetrievedArticle] = []
        for row in rows:
            data = dict(row)
            data.setdefault("similarity", 1.0)
            data.setdefault("fused_score", 1.0)
            articles.append(_row_to_article(data))
        return articles

    async def corpus_stats(self) -> list[dict[str, Any]]:
        rows = await database.fetch("select * from legal_corpus_stats()")
        return [dict(r) for r in rows]

    async def document_exists(self, source_type: str, law_number: str | None, year: int | None) -> str | None:
        row = await database.fetchrow(
            "select id from legal_documents where source_type = $1::legal_source_type "
            "and law_number is not distinct from $2 and promulgation_year is not distinct from $3",
            source_type,
            law_number,
            year,
        )
        return str(row["id"]) if row else None

    async def count_articles(self) -> int:
        return int(await database.fetchval("select count(*) from legal_articles") or 0)


def _vector_literal(values: Sequence[float]) -> str:
    """pgvector text literal: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{v:.7f}" for v in values) + "]"


def _row_to_article(row: Any) -> RetrievedArticle:
    data = dict(row)
    return RetrievedArticle(
        article_id=str(data["article_id"]),
        document_id=str(data["document_id"]),
        source_type=LegalSourceType(data["source_type"]),
        law_title=data["title_ar"],
        law_number=data.get("law_number"),
        article_number=data.get("article_number"),
        article_label=data.get("article_label") or "مادة",
        chapter=data.get("chapter"),
        heading=data.get("heading_ar"),
        content=data["content_ar"],
        similarity=_clamp(data.get("similarity") or 0.0),
        fused_score=float(data.get("fused_score") or 0.0),
        vector_rank=data.get("vector_rank"),
        keyword_rank=data.get("keyword_rank"),
        source_url=data.get("source_url"),
        text_quality=data.get("text_quality"),
        verbatim=data.get("verbatim"),
    )


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _digits_only(value: str) -> str:
    """Reduce an article number to its digits, in Western form."""
    return "".join(
        str("٠١٢٣٤٥٦٧٨٩".index(c)) if c in "٠١٢٣٤٥٦٧٨٩" else c for c in (value or "")
    ).replace(" ", "").lstrip("0") or ""


def _chunk_to_article(
    chunk: dict[str, Any],
    document: dict[str, Any],
    *,
    similarity: float,
    fused_score: float,
    vector_rank: int | None = None,
    keyword_rank: int | None = None,
) -> RetrievedArticle:
    provenance = (document.get("metadata") or {}).get("provenance") or {}
    return RetrievedArticle(
        article_id=chunk["id"],
        document_id=chunk["document_id"],
        source_type=LegalSourceType(chunk["source_type"]),
        law_title=document.get("title_ar", ""),
        law_number=document.get("law_number"),
        article_number=chunk["article_number"],
        article_label=chunk["article_label"],
        chapter=chunk["chapter"],
        heading=chunk["heading"],
        content=chunk["content"],
        similarity=similarity,
        fused_score=fused_score,
        vector_rank=vector_rank,
        keyword_rank=keyword_rank,
        source_url=document.get("source_url"),
        text_quality=provenance.get("quality"),
        verbatim=provenance.get("verbatim"),
    )


# ---------------------------------------------------------------------------
# In-memory fallback
# ---------------------------------------------------------------------------
class InMemoryVectorStore:
    """Deterministic, dependency-free store mirroring the SQL fusion logic."""

    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.chunks: dict[str, dict[str, Any]] = {}

    async def upsert_document(self, doc: dict[str, Any]) -> str:
        key = (doc["source_type"], doc.get("law_number"), doc.get("promulgation_year"))
        for existing in self.documents.values():
            if (existing["source_type"], existing.get("law_number"), existing.get("promulgation_year")) == key:
                existing.update(doc)
                return existing["id"]
        doc_id = str(uuid.uuid4())
        self.documents[doc_id] = {**doc, "id": doc_id}
        return doc_id

    async def upsert_chunks(self, chunks: Sequence[StoredChunk]) -> int:
        written = 0
        for chunk in chunks:
            key = f"{chunk.document_id}:{chunk.article_number}:{chunk.chunk_index}"
            self.chunks[key] = {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, key)),
                "document_id": chunk.document_id,
                "source_type": chunk.source_type,
                "article_number": chunk.article_number,
                "article_label": chunk.article_label,
                "chapter": chunk.chapter,
                "heading": chunk.heading,
                "content": chunk.content,
                "content_norm": normalize_arabic(chunk.content),
                "chunk_index": chunk.chunk_index,
                "token_count": chunk.token_count,
                "embedding": list(chunk.embedding),
                "metadata": chunk.metadata,
            }
            written += 1
        return written

    async def search(
        self,
        *,
        query: str,
        query_embedding: list[float],
        top_k: int = 8,
        source_types: list[str] | None = None,
        law_title_contains: str | None = None,
        document_id: str | None = None,
        min_similarity: float = 0.0,
    ) -> list[RetrievedArticle]:
        q_norm = normalize_arabic(query)
        q_terms = set(q_norm.split())
        candidates: list[tuple[dict[str, Any], float, float]] = []

        for chunk in self.chunks.values():
            if source_types and chunk["source_type"] not in source_types:
                continue
            if document_id and chunk["document_id"] != document_id:
                continue
            document = self.documents.get(chunk["document_id"], {})
            if law_title_contains and law_title_contains not in (document.get("title_ar") or ""):
                continue
            similarity = _cosine(query_embedding, chunk["embedding"])
            lexical = _lexical_score(q_terms, chunk["content_norm"])
            candidates.append((chunk, similarity, lexical))

        if not candidates:
            return []

        by_vector = sorted(candidates, key=lambda c: c[1], reverse=True)[: max(top_k * 4, 40)]
        lexical_only = [c for c in candidates if c[2] > 0]
        by_keyword = sorted(lexical_only, key=lambda c: (c[2], c[1]), reverse=True)[: max(top_k * 4, 40)]

        vrank = {c[0]["id"]: i + 1 for i, c in enumerate(by_vector)}
        krank = {c[0]["id"]: i + 1 for i, c in enumerate(by_keyword)}

        fused: list[tuple[dict[str, Any], float, float]] = []
        for chunk, similarity, _ in candidates:
            cid = chunk["id"]
            vr, kr = vrank.get(cid), krank.get(cid)
            if vr is None and kr is None:
                continue
            score = 0.0
            if vr is not None:
                score += settings.vector_weight * (1.0 / (settings.rrf_k + vr))
            if kr is not None:
                score += settings.keyword_weight * (1.0 / (settings.rrf_k + kr))
            if similarity >= min_similarity:
                fused.append((chunk, score, similarity))

        fused.sort(key=lambda item: (item[1], item[2]), reverse=True)

        results: list[RetrievedArticle] = []
        for chunk, score, similarity in fused[:top_k]:
            document = self.documents.get(chunk["document_id"], {})
            results.append(
                _chunk_to_article(
                    chunk,
                    document,
                    similarity=_clamp(similarity),
                    fused_score=score,
                    vector_rank=vrank.get(chunk["id"]),
                    keyword_rank=krank.get(chunk["id"]),
                )
            )
        return results

    async def find_by_article_number(
        self,
        article_number: str,
        source_types: list[str] | None = None,
        limit: int = 3,
    ) -> list[RetrievedArticle]:
        target = _digits_only(article_number)
        if not target:
            return []
        matches = [
            chunk
            for chunk in self.chunks.values()
            if _digits_only(chunk.get("article_number") or "") == target
            and (not source_types or chunk["source_type"] in source_types)
        ]
        matches.sort(key=lambda c: c.get("chunk_index", 0))
        return [
            _chunk_to_article(
                chunk,
                self.documents.get(chunk["document_id"], {}),
                similarity=1.0,
                fused_score=1.0,
            )
            for chunk in matches[:limit]
        ]

    async def corpus_stats(self) -> list[dict[str, Any]]:
        buckets: dict[str, dict[str, Any]] = {}
        for chunk in self.chunks.values():
            bucket = buckets.setdefault(
                chunk["source_type"],
                {"source_type": chunk["source_type"], "documents": set(), "articles": 0,
                 "embedded": 0, "total_tokens": 0},
            )
            bucket["documents"].add(chunk["document_id"])
            bucket["articles"] += 1
            bucket["embedded"] += 1 if chunk["embedding"] else 0
            bucket["total_tokens"] += chunk["token_count"] or 0
        return [
            {**b, "documents": len(b["documents"])}
            for b in sorted(buckets.values(), key=lambda x: x["articles"], reverse=True)
        ]

    async def document_exists(self, source_type: str, law_number: str | None, year: int | None) -> str | None:
        for doc in self.documents.values():
            if (doc["source_type"], doc.get("law_number"), doc.get("promulgation_year")) == (
                source_type, law_number, year,
            ):
                return doc["id"]
        return None

    async def count_articles(self) -> int:
        return len(self.chunks)


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return max(0.0, dot / (na * nb))


def _lexical_score(query_terms: set[str], content_norm: str) -> float:
    if not query_terms or not content_norm:
        return 0.0
    content_terms = set(content_norm.split())
    overlap = query_terms & content_terms
    if not overlap:
        return 0.0
    return len(overlap) / math.sqrt(len(query_terms) * max(len(content_terms), 1))


def build_vector_store() -> VectorStore:
    """Return the pgvector store when a database is configured, else in-memory."""
    if settings.database_enabled:
        return PgVectorStore()
    logger.warning("SUPABASE_DB_URL not set - using in-memory vector store")
    return InMemoryVectorStore()


vector_store: VectorStore = build_vector_store()


def reset_vector_store() -> VectorStore:
    """Rebuild the module-level store (used by tests and after config changes)."""
    global vector_store
    vector_store = build_vector_store()
    return vector_store
