"""Administration service: sections, laws and articles.

Wraps the corpus repositories with permission checks and keeps the vector index
consistent when an article's text changes (the embedding is recomputed so
retrieval never returns stale text).
"""
from __future__ import annotations

from typing import Any

from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.db.repositories import case_repository
from app.db.user_repository import SectionRepository, section_repository
from app.db.vector_store import (
    LegalDocumentAdminRepository,
    legal_document_admin_repository,
)
from app.llm.embeddings import embedding_provider
from app.services.auth import AuthService, auth_service

logger = get_logger(__name__)


class AdminService:
    def __init__(
        self,
        *,
        auth: AuthService | None = None,
        sections: SectionRepository | None = None,
        documents: LegalDocumentAdminRepository | None = None,
    ) -> None:
        self.auth = auth or auth_service
        self.sections = sections or section_repository
        self.documents = documents or legal_document_admin_repository

    # -- sections ----------------------------------------------------------
    async def list_sections(self, user: dict[str, Any], *, include_inactive: bool = True) -> list[dict]:
        self.auth.require_permission(user, "sections.read")
        return await self.sections.list_sections(include_inactive=include_inactive)

    async def create_section(self, user: dict[str, Any], payload: dict[str, Any]) -> dict:
        self.auth.require_permission(user, "sections.write")
        section = await self.sections.create(payload)
        await case_repository.audit(
            "section", "created", str(section["id"]), actor=user["username"], payload=payload
        )
        return section

    async def update_section(
        self, user: dict[str, Any], section_id: str, payload: dict[str, Any]
    ) -> dict:
        self.auth.require_permission(user, "sections.write")
        section = await self.sections.update(section_id, payload)
        await case_repository.audit(
            "section", "updated", section_id, actor=user["username"], payload=payload
        )
        return section

    async def delete_section(self, user: dict[str, Any], section_id: str) -> None:
        self.auth.require_permission(user, "sections.delete")
        await self.sections.delete(section_id)
        await case_repository.audit("section", "deleted", section_id, actor=user["username"])

    # -- laws --------------------------------------------------------------
    async def list_documents(
        self, user: dict[str, Any], *, source_type: str | None = None, section_id: str | None = None
    ) -> list[dict]:
        self.auth.require_permission(user, "laws.read")
        return await self.documents.list_documents(source_type=source_type, section_id=section_id)

    async def get_document(self, user: dict[str, Any], document_id: str) -> dict:
        self.auth.require_permission(user, "laws.read")
        document = await self.documents.get_document(document_id)
        if document is None:
            from app.core.errors import NotFoundError

            raise NotFoundError("القانون غير موجود")
        return document

    async def update_document(
        self, user: dict[str, Any], document_id: str, payload: dict[str, Any]
    ) -> dict:
        self.auth.require_permission(user, "laws.write")
        document = await self.documents.update_document(document_id, payload)
        await case_repository.audit(
            "legal_document", "updated", document_id, actor=user["username"], payload=payload
        )
        return document

    async def delete_document(self, user: dict[str, Any], document_id: str) -> int:
        self.auth.require_permission(user, "laws.delete")
        removed = await self.documents.delete_document(document_id)
        await case_repository.audit(
            "legal_document", "deleted", document_id, actor=user["username"],
            payload={"articles_removed": removed},
        )
        return removed

    # -- articles ----------------------------------------------------------
    async def list_articles(
        self,
        user: dict[str, Any],
        document_id: str,
        *,
        limit: int = 100,
        offset: int = 0,
        search: str | None = None,
    ) -> list[dict]:
        self.auth.require_permission(user, "articles.read")
        return await self.documents.list_articles(
            document_id, limit=limit, offset=offset, search=search
        )

    async def update_article(
        self, user: dict[str, Any], article_id: str, payload: dict[str, Any]
    ) -> dict:
        """Edit an article and re-embed it so retrieval stays accurate."""
        self.auth.require_permission(user, "articles.write")
        if not payload:
            raise ValidationError("لا يوجد أي تغيير لتطبيقه")
        article = await self.documents.update_article(article_id, payload)
        if payload.get("content_ar"):
            embedding = await embedding_provider.embed_query(payload["content_ar"])
            await self.documents.update_embedding(article_id, embedding)
        await case_repository.audit(
            "legal_article", "updated", article_id, actor=user["username"], payload=payload
        )
        return article

    async def delete_article(self, user: dict[str, Any], article_id: str) -> None:
        self.auth.require_permission(user, "articles.delete")
        await self.documents.delete_article(article_id)
        await case_repository.audit(
            "legal_article", "deleted", article_id, actor=user["username"]
        )

    # -- corpus ingest -----------------------------------------------------
    async def ingest(
        self,
        user: dict[str, Any],
        *,
        source_type: str,
        title_ar: str,
        text: str,
        law_number: str | None = None,
        promulgation_year: int | None = None,
        source_url: str | None = None,
        section_id: str | None = None,
    ) -> dict[str, Any]:
        """Ingest a whole law's text, replacing any prior version of the same law."""
        self.auth.require_permission(user, "corpus.ingest")
        from app.schemas.legal import IngestTextRequest, LegalDocumentCreate
        from app.services.ingestion import ingestion_service

        request = IngestTextRequest(
            document=LegalDocumentCreate(
                source_type=source_type,
                title_ar=title_ar,
                law_number=law_number,
                promulgation_year=promulgation_year,
                source_url=source_url,
            ),
            text=text,
        )
        result = await ingestion_service.ingest_text(request)
        if section_id:
            await self.documents.update_document(str(result.document_id), {"section_id": section_id})
        await case_repository.audit(
            "legal_document",
            "ingested",
            str(result.document_id),
            actor=user["username"],
            payload={"source_type": source_type, "title_ar": title_ar},
        )
        return result.model_dump(mode="json")

    # -- monitoring --------------------------------------------------------
    async def overview(self, user: dict[str, Any]) -> dict[str, Any]:
        """System health snapshot for the admin dashboard."""
        # Monitoring exposes platform internals and the user roster, so it is
        # gated at admin level (users.read), not merely sections.read.
        self.auth.require_permission(user, "users.read")
        from app.core.config import settings
        from app.db.vector_store import vector_store

        try:
            stats = await vector_store.corpus_stats()
        except Exception:  # pragma: no cover - defensive
            stats = []
        users = await self.auth.list_users()
        return {
            "app_env": settings.app_env,
            "database": settings.database_enabled,
            "llm_provider": settings.llm_provider,
            "embedding_provider": settings.embedding_provider,
            "vector_store": type(vector_store).__name__,
            "users": {
                "total": len(users),
                "active": sum(1 for u in users if u.get("is_active", True)),
            },
            "corpus": {
                "documents": len(stats),
                "articles": sum(int(s.get("articles") or 0) for s in stats),
                "by_source": stats,
            },
            "cases": await case_repository.count_cases(),
            "audit_events": await case_repository.count_audit(),
        }

    async def audit_trail(
        self, user: dict[str, Any], *, limit: int = 50, offset: int = 0, entity: str | None = None
    ) -> list[dict[str, Any]]:
        self.auth.require_permission(user, "users.read")
        return await case_repository.list_audit(limit=limit, offset=offset, entity=entity)


admin_service = AdminService()
