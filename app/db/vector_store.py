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
from datetime import UTC, datetime
from typing import Any, Protocol

from app.core.config import settings
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.core.text_utils import normalize_arabic
from app.db.pool import database
from app.schemas.common import LegalSourceType
from app.schemas.legal import RetrievedArticle

logger = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


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

    async def search_articles(
        self,
        *,
        query: str | None = None,
        article_number: str | None = None,
        source_types: list[str] | None = None,
        limit: int = 20,
    ) -> list[RetrievedArticle]:
        """Deterministic article lookup by number and/or a text fragment.

        Unlike :meth:`search` this needs no embedding: it matches the article
        number exactly (digits only, so "٥" and "5" agree) and the text by
        normalized term overlap. That is what powers the public "search the
        statutes" box, which must also work when no embedding model is
        reachable.
        """
        ...

    async def corpus_stats(self) -> list[dict[str, Any]]: ...

    async def document_exists(self, source_type: str, law_number: str | None, year: int | None) -> str | None: ...

    async def embedding_models(self) -> set[str]:
        """The distinct embedding models that built the stored vectors.

        Switching embedding provider/model leaves the vectors in a different
        space: cosine similarity is then meaningless and retrieval silently
        returns noise. Recording the model per chunk lets retrieval detect the
        mismatch and warn instead.
        """
        ...


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

    async def search_articles(
        self,
        *,
        query: str | None = None,
        article_number: str | None = None,
        source_types: list[str] | None = None,
        limit: int = 20,
    ) -> list[RetrievedArticle]:
        """Number + text lookup, served by the ``search_legal_articles`` function.

        Kept in Postgres so the public search box gets the same digits-only
        number match and normalized-term overlap as the in-memory store without
        shipping the whole corpus to the client.
        """
        if not (query and query.strip()) and not (article_number and article_number.strip()):
            return []
        rows = await database.fetch(
            "select * from search_legal_articles($1,$2,$3::text[],$4)",
            query,
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

    async def embedding_models(self) -> set[str]:
        rows = await database.fetch(
            "select distinct metadata ->> 'embedding_model' as model from legal_articles "
            "where embedding is not null"
        )
        return {str(r["model"]) for r in rows if r["model"]}


class LegalDocumentAdminRepository:
    """Read/update/delete operations on the legal corpus for the admin UI.

    The ingestion path already owns creation (``upsert_legal_document`` +
    ``upsert_legal_article``); this repository covers browsing, editing metadata
    and removing documents, in both Postgres and in-memory modes.
    """

    def __init__(self) -> None:
        self._documents: dict[str, dict[str, Any]] = {}
        self._articles: dict[str, list[dict[str, Any]]] = {}

    @property
    def persistent(self) -> bool:
        return settings.database_enabled

    def _memory(self) -> InMemoryVectorStore | None:
        """The active in-memory store, so admin edits are visible to retrieval."""
        return vector_store if isinstance(vector_store, InMemoryVectorStore) else None

    async def list_documents(
        self, *, source_type: str | None = None, section_id: str | None = None
    ) -> list[dict[str, Any]]:
        if self.persistent:
            rows = await database.fetch(
                "select d.id, d.source_type, d.title_ar, d.title_en, d.law_number, "
                "       d.promulgation_year, d.issuing_authority, d.jurisdiction, "
                "       d.source_url, d.section_id, d.metadata, d.created_at, d.updated_at, "
                "       s.title_ar as section_title_ar, "
                "       (select count(*) from legal_articles a where a.document_id = d.id) "
                "         as article_count "
                "from legal_documents d "
                "left join platform_sections s on s.id = d.section_id "
                "where ($1::text is null or d.source_type::text = $1) "
                "  and ($2::uuid is null or d.section_id = $2) "
                "order by d.source_type::text asc, d.promulgation_year asc nulls last",
                source_type,
                uuid.UUID(section_id) if section_id else None,
            )
            return [dict(r) for r in rows]
        rows = []
        for doc in self._documents.values():
            if source_type and doc["source_type"] != source_type:
                continue
            if section_id and str(doc.get("section_id")) != str(section_id):
                continue
            row = dict(doc)
            row["article_count"] = len(self._articles.get(str(doc["id"]), []))
            rows.append(row)
        store = self._memory()
        if store is not None:
            for doc_id, doc in store.documents.items():
                if source_type and doc["source_type"] != source_type:
                    continue
                if section_id and str(doc.get("section_id")) != str(section_id):
                    continue
                row = dict(doc)
                row["article_count"] = sum(
                    1 for c in store.chunks.values() if c["document_id"] == doc_id
                )
                rows.append(row)
        return rows

    async def get_document(self, document_id: str | uuid.UUID) -> dict[str, Any] | None:
        if self.persistent:
            row = await database.fetchrow(
                "select id, source_type, title_ar, title_en, law_number, promulgation_year, "
                "       issuing_authority, jurisdiction, source_url, section_id, metadata, "
                "       created_at, updated_at "
                "from legal_documents where id = $1",
                uuid.UUID(str(document_id)),
            )
            return dict(row) if row else None
        key = str(document_id)
        if key in self._documents:
            return dict(self._documents[key])
        store = self._memory()
        if store is not None and key in store.documents:
            return dict(store.documents[key])
        return None

    async def update_document(
        self, document_id: str | uuid.UUID, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if await self.get_document(document_id) is None:
            raise NotFoundError("القانون غير موجود")
        if self.persistent:
            row = await database.fetchrow(
                "update legal_documents set "
                "title_ar = coalesce($2, title_ar), "
                "title_en = coalesce($3, title_en), "
                "law_number = coalesce($4, law_number), "
                "promulgation_year = coalesce($5, promulgation_year), "
                "issuing_authority = coalesce($6, issuing_authority), "
                "source_url = coalesce($7, source_url), "
                "section_id = coalesce($8, section_id) "
                "where id = $1 "
                "returning id, source_type, title_ar, title_en, law_number, "
                "          promulgation_year, issuing_authority, jurisdiction, source_url, "
                "          section_id, metadata, created_at, updated_at",
                uuid.UUID(str(document_id)),
                payload.get("title_ar"),
                payload.get("title_en"),
                payload.get("law_number"),
                payload.get("promulgation_year"),
                payload.get("issuing_authority"),
                payload.get("source_url"),
                uuid.UUID(str(payload["section_id"])) if payload.get("section_id") else None,
            )
            return dict(row)
        stored = self._documents[str(document_id)]
        for column in (
            "title_ar",
            "title_en",
            "law_number",
            "promulgation_year",
            "issuing_authority",
            "source_url",
            "section_id",
        ):
            if payload.get(column) is not None:
                stored[column] = payload[column]
        stored["updated_at"] = _now()
        store = self._memory()
        if store is not None and str(document_id) in store.documents:
            for column in ("title_ar", "title_en", "law_number", "promulgation_year",
                           "issuing_authority", "source_url", "section_id"):
                if payload.get(column) is not None:
                    store.documents[str(document_id)][column] = payload[column]
        return dict(stored)

    async def delete_document(self, document_id: str | uuid.UUID) -> int:
        """Delete a law and cascade its articles; returns the article count removed."""
        if await self.get_document(document_id) is None:
            raise NotFoundError("القانون غير موجود")
        if self.persistent:
            removed = int(
                await database.fetchval(
                    "select count(*) from legal_articles where document_id = $1",
                    uuid.UUID(str(document_id)),
                )
                or 0
            )
            await database.execute(
                "delete from legal_documents where id = $1", uuid.UUID(str(document_id))
            )
            return removed
        key = str(document_id)
        removed = len(self._articles.pop(key, []))
        self._documents.pop(key, None)
        store = self._memory()
        if store is not None and key in store.documents:
            removed += sum(1 for c in list(store.chunks.values()) if c["document_id"] == key)
            store.documents.pop(key, None)
            for chunk_key in [k for k, c in store.chunks.items() if c["document_id"] == key]:
                store.chunks.pop(chunk_key, None)
        return removed

    async def list_articles(
        self,
        document_id: str | uuid.UUID,
        *,
        limit: int = 100,
        offset: int = 0,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        if self.persistent:
            rows = await database.fetch(
                "select id, document_id, source_type, article_number, article_label, "
                "       chapter, section, heading_ar, content_ar, chunk_index, token_count, "
                "       created_at "
                "from legal_articles where document_id = $1 "
                "  and ($4::text is null or content_ar ilike '%' || $4 || '%' "
                "       or article_number = $4) "
                "order by chunk_index asc limit $2 offset $3",
                uuid.UUID(str(document_id)),
                limit,
                offset,
                search,
            )
            return [dict(r) for r in rows]
        key = str(document_id)
        rows = [dict(a) for a in self._articles.get(key, [])]
        store = self._memory()
        if store is not None:
            for chunk in store.chunks.values():
                if chunk["document_id"] != key:
                    continue
                rows.append(
                    {
                        "id": chunk["id"],
                        "document_id": chunk["document_id"],
                        "source_type": chunk["source_type"],
                        "article_number": chunk["article_number"],
                        "article_label": chunk["article_label"],
                        "chapter": chunk.get("chapter"),
                        "section": chunk.get("section"),
                        "heading_ar": chunk.get("heading"),
                        "content_ar": chunk["content"],
                        "chunk_index": chunk["chunk_index"],
                        "token_count": chunk["token_count"],
                    }
                )
        if search:
            rows = [
                a
                for a in rows
                if search in (a.get("content_ar") or "") or a.get("article_number") == search
            ]
        rows.sort(key=lambda a: (a.get("chunk_index") or 0, str(a.get("id"))))
        return rows[offset : offset + limit]

    async def delete_article(self, article_id: str | uuid.UUID) -> None:
        if self.persistent:
            result = await database.execute(
                "delete from legal_articles where id = $1", uuid.UUID(str(article_id))
            )
            if result == "DELETE 0":
                raise NotFoundError("المادة غير موجودة")
            return
        for rows in self._articles.values():
            for index, row in enumerate(rows):
                if str(row["id"]) == str(article_id):
                    rows.pop(index)
                    return
        store = self._memory()
        if store is not None:
            for key, chunk in list(store.chunks.items()):
                if str(chunk["id"]) == str(article_id):
                    store.chunks.pop(key, None)
                    return
        raise NotFoundError("المادة غير موجودة")

    async def update_article(
        self, article_id: str | uuid.UUID, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Edit an article's text. The embedding is recomputed by the caller."""
        if self.persistent:
            row = await database.fetchrow(
                "update legal_articles set "
                "content_ar = coalesce($2, content_ar), "
                "content_norm = normalize_arabic(coalesce($2, content_ar)), "
                "article_number = coalesce($3, article_number), "
                "article_label = coalesce($4, article_label), "
                "heading_ar = coalesce($5, heading_ar) "
                "where id = $1 "
                "returning id, document_id, source_type, article_number, article_label, "
                "          chapter, section, heading_ar, content_ar, chunk_index, token_count",
                uuid.UUID(str(article_id)),
                payload.get("content_ar"),
                payload.get("article_number"),
                payload.get("article_label"),
                payload.get("heading_ar"),
            )
            if row is None:
                raise NotFoundError("المادة غير موجودة")
            return dict(row)
        for rows in self._articles.values():
            for row in rows:
                if str(row["id"]) == str(article_id):
                    for column in ("content_ar", "article_number", "article_label", "heading_ar"):
                        if payload.get(column) is not None:
                            row[column] = payload[column]
                    return dict(row)
        store = self._memory()
        chunk = None
        if store is not None:
            chunk = next(
                (c for c in store.chunks.values() if str(c["id"]) == str(article_id)), None
            )
        if chunk is not None:
            if payload.get("content_ar") is not None:
                chunk["content"] = payload["content_ar"]
                chunk["content_norm"] = normalize_arabic(payload["content_ar"])
            if payload.get("article_number") is not None:
                chunk["article_number"] = payload["article_number"]
            if payload.get("article_label") is not None:
                chunk["article_label"] = payload["article_label"]
            if payload.get("heading_ar") is not None:
                chunk["heading"] = payload["heading_ar"]
            return {
                "id": chunk["id"],
                "document_id": chunk["document_id"],
                "source_type": chunk["source_type"],
                "article_number": chunk["article_number"],
                "article_label": chunk["article_label"],
                "chapter": chunk.get("chapter"),
                "section": chunk.get("section"),
                "heading_ar": chunk.get("heading"),
                "content_ar": chunk["content"],
                "chunk_index": chunk["chunk_index"],
                "token_count": chunk["token_count"],
            }
        raise NotFoundError("المادة غير موجودة")

    async def update_embedding(self, article_id: str | uuid.UUID, embedding: Sequence[float]) -> None:
        if self.persistent:
            await database.execute(
                "update legal_articles set embedding = $2::vector where id = $1",
                uuid.UUID(str(article_id)),
                _vector_literal(embedding),
            )
            return
        store = self._memory()
        if store is not None:
            for chunk in store.chunks.values():
                if str(chunk["id"]) == str(article_id):
                    chunk["embedding"] = list(embedding)
                    return

    def reset(self) -> None:
        self._documents.clear()
        self._articles.clear()


legal_document_admin_repository = LegalDocumentAdminRepository()


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
    """Reduce an article number to its digits, in Western form.

    Arabic-Indic digits become Western ones, every non-digit is dropped, and
    leading zeros are stripped, so "١٤٦", "146" and "0146" all compare equal.
    Mirrored by ``normalize_article_number`` in sql/002_functions.sql and by
    ``digitsOnly`` in app/static/statute-search.js.
    """
    converted = "".join(
        str("٠١٢٣٤٥٦٧٨٩".index(c)) if c in "٠١٢٣٤٥٦٧٨٩" else c for c in (value or "")
    )
    return "".join(c for c in converted if c in "0123456789").lstrip("0")


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

    async def search_articles(
        self,
        *,
        query: str | None = None,
        article_number: str | None = None,
        source_types: list[str] | None = None,
        limit: int = 20,
    ) -> list[RetrievedArticle]:
        """Deterministic lookup by article number and/or text fragment.

        Mirrors the SQL ``search_legal_articles`` function so the offline search
        box ranks exactly like the online one: an exact number match always
        outranks a text hit, and text hits are ordered by normalized-term
        overlap (the same lexical score hybrid retrieval uses).
        """
        target = _digits_only(article_number or "")
        query_norm = normalize_arabic(query or "")
        terms = {t for t in query_norm.split() if len(t) > 1}
        if not target and not terms:
            return []

        scored: list[tuple[int, float, dict[str, Any]]] = []
        for chunk in self.chunks.values():
            if source_types and chunk["source_type"] not in source_types:
                continue
            number_match = bool(target) and _digits_only(
                chunk.get("article_number") or ""
            ) == target
            if target and not number_match:
                # A number was supplied, so only that number is wanted.
                continue
            lexical = _lexical_score(terms, chunk.get("content_norm") or "") if terms else 0.0
            if terms and not number_match and lexical <= 0.0:
                continue
            scored.append((1 if number_match else 0, lexical, chunk))

        # Number matches first, then by text relevance, then by reading order.
        scored.sort(
            key=lambda item: (item[0], item[1], -item[2].get("chunk_index", 0)),
            reverse=True,
        )
        return [
            _chunk_to_article(
                chunk,
                self.documents.get(chunk["document_id"], {}),
                similarity=1.0 if number_match else min(1.0, lexical * 4),
                fused_score=lexical,
            )
            for number_match, lexical, chunk in scored[:limit]
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

    async def embedding_models(self) -> set[str]:
        return {
            str(c["metadata"].get("embedding_model"))
            for c in self.chunks.values()
            if c.get("embedding") and c.get("metadata", {}).get("embedding_model")
        }


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
